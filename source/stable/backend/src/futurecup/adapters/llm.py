"""Explicit Mock and opt-in, non-streaming Chat Completions HTTP adapter."""

import asyncio
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from futurecup.application.ports import ModelRequest, ModelResponse
from futurecup.domain.errors import RuntimeFault, HTTPErrorCategory, ProviderHTTPDiagnostic, ProviderCompletionDiagnostic
from futurecup.domain.models import JsonValue
from futurecup.domain.llm import ModelPolicy, ProviderProfile, RetryPolicy
from futurecup.adapters.provider_config import validate_origin, validate_dns, EnvironmentSecretGuard
from futurecup.adapters.scenario_mock import scenario_output


class MockLLMProvider:
    is_simulated = True

    async def infer(self, request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(0)
        last = request.messages[-1]
        if request.model in {'mock-workspace-planner','mock-workspace-worker'}:
            from futurecup.adapters.workspace_demo import mock_response
            if request.model=='mock-workspace-worker' and 'untrusted_knowledge' in str(last['content']):
                data=json.loads(str(last['content']))['untrusted_knowledge']
                value=mock_response(request.model,str(request.messages[1]['content']))
                value['citations']=[{'chunk_id':h['id'],'quote':h['text'][:120]} for h in data['hits']]
                value['items'].extend('[MOCK 基于实际检索] '+h['text'][:120] for h in data['hits'])
                return ModelResponse(json.dumps(value,ensure_ascii=False),(),0,0,None,False)
            return ModelResponse(json.dumps(mock_response(request.model,str(last['content'])),ensure_ascii=False),(),0,0,None,False)
        if request.model == "mock-scenario08":
            return ModelResponse(scenario_output(str(last.get("content", ""))), (), 0, 0, None, False)
        if request.model == "mock-repair-demo":
            # Explicit scripted demonstration, NOT real LLM inference or self-evaluation.
            try:
                value = json.loads(str(last.get("content", "")))
            except ValueError:
                value = None
            repaired = isinstance(value, dict) and isinstance(value.get("recovery"), dict)
            return ModelResponse(json.dumps({"answer": 42} if repaired else {"wrong": "MOCK SIMULATED first attempt"}), (), 0, 0, None, False)
        if request.model == "mock-echo":
            return ModelResponse("[MOCK SIMULATED] " + str(last.get("content", "")), (), 0, 0, None, False)
        if request.model != "mock-calculator":
            raise RuntimeFault("mock_model_unknown", "Unknown mock model")
        if last.get("role") == "tool":
            return ModelResponse("[MOCK SIMULATED] Tool result: " + str(last.get("content")), (), 0, 0, None, False)
        try:
            arguments = json.loads(str(last["content"]))
            if not isinstance(arguments, dict):
                raise ValueError
        except (ValueError, KeyError):
            raise RuntimeFault("mock_input_invalid", "mock-calculator expects calculator arguments as JSON") from None
        return ModelResponse("", ({"id": "mock_" + request.trace_id, "name": "calculator", "arguments": arguments},), 0, 0, None, False)


class OpenAICompatibleProvider:
    is_simulated = False

    def __init__(self, base_url: str, api_key_env: str = "FUTURECUP_LLM_API_KEY",
                 enabled: bool = False, transport: httpx.AsyncBaseTransport | None = None, *,
                 profile: ProviderProfile | None = None) -> None:
        parsed = urlsplit(base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme == "http" and not local)):
            raise ValueError("LLM base URL requires HTTPS (HTTP allowed only for loopback)")
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key_env = api_key_env
        self._enabled = enabled
        self._transport = transport
        self.profile = profile
        self.is_simulated = transport is not None
        self.retry_policy = profile.retry if profile else RetryPolicy()
        self.last_outcome: dict = {"state": "not_checked", "error_code": None, "at": None}
        if profile:
            validate_origin(profile)
            if base_url != profile.base_url or api_key_env != profile.key_env:
                raise ValueError("Adapter/profile mismatch")

    def policy(self, model: str) -> ModelPolicy | None:
        if self.profile is None:
            return None
        found = next((p for p in self.profile.models if p.id == model), None)
        if found is None:
            raise RuntimeFault("model_not_configured", "Model is not in the server model allowlist", 422)
        return found

    def validate_agent(self, agent) -> None:
        policy = self.policy(agent.model)
        if policy:
            if agent.tool_refs and not policy.supports_tools:
                raise RuntimeFault("model_tools_unsupported", "Configured model does not support tools", 422)
            if agent.response_format not in policy.response_formats:
                raise RuntimeFault("model_format_unsupported", "Configured response format is unsupported", 422)
            if agent.tool_refs and agent.response_format == "json_object" and not policy.tools_with_json:
                raise RuntimeFault("model_combination_unsupported", "Tools with native JSON are not enabled for this model", 422)

    def check_request(self, request: ModelRequest) -> ModelPolicy | None:
        policy = self.policy(request.model)
        if policy:
            if request.tools and not policy.supports_tools:
                raise RuntimeFault("model_tools_unsupported", "Model tool capability is disabled", 422)
            if request.response_format not in policy.response_formats:
                raise RuntimeFault("model_format_unsupported", "Model response format is disabled", 422)
            if request.tools and request.response_format == "json_object" and not policy.tools_with_json:
                raise RuntimeFault("model_combination_unsupported", "Native JSON with tools is not enabled", 422)
            if self.profile.kind == "deepseek" and request.tools and not policy.thinking_disabled:
                raise RuntimeFault("model_combination_unsupported", "Thinking-mode tool history is not implemented; configure non-thinking mode", 422)
            context = json.dumps({"messages": request.messages, "tools": request.tools}, ensure_ascii=False, allow_nan=False)
            if len(context.encode("utf-8")) > policy.max_context_bytes:
                raise RuntimeFault("model_context_limit", "Context exceeds configured byte limit", 422)
            if request.max_output_tokens > policy.max_output_tokens:
                raise RuntimeFault("model_output_limit", "Output token request exceeds model policy", 422)
        return policy

    async def infer(self, request: ModelRequest) -> ModelResponse:
        try:
            result = await self._infer(request)
            self.last_outcome = {"state": "protocol_simulated" if self.is_simulated else "observed_success",
                                 "error_code": None, "at": datetime.now(timezone.utc).isoformat()}
            return result
        except RuntimeFault as fault:
            self.last_outcome = {"state": "error", "error_code": fault.code, "at": datetime.now(timezone.utc).isoformat()}
            if fault.provider_http:
                self.last_outcome["provider_http"] = asdict(fault.provider_http)
            raise
        except asyncio.CancelledError:
            self.record_failure("request_cancelled")
            raise

    def record_failure(self, code: str) -> None:
        self.last_outcome = {"state": "error", "error_code": code, "at": datetime.now(timezone.utc).isoformat()}

    def _http_fault(self, response: httpx.Response, key: str) -> RuntimeFault:
        """Never read error bodies or copy arbitrary headers/messages into records."""
        status = int(response.status_code)
        category = {400: HTTPErrorCategory.INVALID_REQUEST,
                    401: HTTPErrorCategory.AUTHENTICATION_FAILED,
                    402: HTTPErrorCategory.INSUFFICIENT_BALANCE,
                    403: HTTPErrorCategory.AUTHENTICATION_FAILED,
                    404: HTTPErrorCategory.MODEL_UNAVAILABLE,
                    429: HTTPErrorCategory.RATE_LIMITED}.get(status, HTTPErrorCategory.UNKNOWN_HTTP_ERROR)
        if status >= 500:
            category = HTTPErrorCategory.SERVER_ERROR
        request_id = None
        values = response.headers.get_list("x-request-id")
        if len(values) == 1 and len(values[0]) in {32, 36} and key.casefold() not in values[0].casefold():
            try:
                candidate = ProviderHTTPDiagnostic(status, category, values[0]).request_id
                # Also reject UUID-shaped credential material with a prefix or
                # punctuation removed; allowlisting syntax alone is insufficient.
                normalized_id = candidate.replace("-", "").casefold()
                normalized_key = "".join(c for c in key.casefold() if c.isalnum())
                if not normalized_key or (normalized_id not in normalized_key and normalized_key not in normalized_id):
                    request_id = candidate
            except ValueError:
                pass
        diagnostic = ProviderHTTPDiagnostic(status, category, request_id)
        # Preserve every previous public code/status and therefore retry semantics.
        code, message, api_status = "provider_http_error", "Model service rejected the request", 502
        if status == 429:
            code, message, api_status = "provider_rate_limited", "Model service rate limit reached", 503
        elif self.profile and status in {401, 403}:
            code, message = "provider_auth_error", "Model service rejected the credentials or permissions"
        elif self.profile and status >= 500:
            code, message = "provider_server_error", "Model service failed"
        return RuntimeFault(code, message, api_status, provider_http=diagnostic)

    async def _infer(self, request: ModelRequest) -> ModelResponse:
        if not self._enabled:
            raise RuntimeFault("real_provider_disabled", "Real provider requires explicit operator opt-in", 409)
        key = os.environ.get(self._api_key_env)
        if not key or not key.strip():
            raise RuntimeFault("provider_credentials_missing", "Provider credentials are not configured", 409)
        key = key.strip()
        if not 8 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise RuntimeFault("provider_credentials_invalid", "Provider credential format is invalid", 409)
        policy = self.check_request(request)
        EnvironmentSecretGuard((self._api_key_env,)).check(request.messages)
        payload: dict[str, JsonValue] = {
            "model": request.model, "messages": list(request.messages),
            "max_tokens": request.max_output_tokens, "stream": False,
        }
        if request.tools:
            payload["tools"] = list(request.tools)
        if request.response_format == "json_object":
            if policy is None:
                raise RuntimeFault("model_format_unsupported", "Native JSON requires a server model capability policy", 422)
            payload["response_format"] = {"type": "json_object"}
        if policy and policy.thinking_disabled and self.profile.kind == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        timeout = min(request.timeout_seconds, self.profile.timeout_seconds) if self.profile else request.timeout_seconds
        try:
            async with asyncio.timeout(timeout):
                if self.profile and self._transport is None:
                    await validate_dns(self.profile)
                async with httpx.AsyncClient(transport=self._transport, timeout=timeout,
                                            follow_redirects=False, trust_env=False) as client:
                    async with client.stream("POST", self._url, headers={"Authorization": "Bearer " + key}, json=payload) as response:
                        if response.status_code < 200 or response.status_code >= 300:
                            raise self._http_fault(response, key)
                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > 1_048_576:
                                raise RuntimeFault("provider_response_invalid", "Model response exceeds the size limit", 502)
            raw = bytes(chunks)
            if key.encode() in raw:
                raise RuntimeFault("provider_sensitive_response", "Response contains a configured credential", 502)
            try:
                result = self._parse(raw)
            except RuntimeFault as fault:
                if fault.provider_completion:
                    EnvironmentSecretGuard((self._api_key_env,)).check(asdict(fault.provider_completion))
                raise
            EnvironmentSecretGuard((self._api_key_env,)).check({"content": result.content, "tools": result.tool_calls, "model": result.model, "request_id": result.provider_request_id})
            if policy and result.tool_calls and not policy.supports_tools:
                raise RuntimeFault("model_tools_unsupported", "Provider returned tools despite disabled capability", 502)
            return result
        except (httpx.TimeoutException, TimeoutError):
            raise RuntimeFault("model_timeout", "Model request timed out", 504) from None
        except httpx.RequestError:
            raise RuntimeFault("provider_connection_failed", "Cannot reach model service", 502) from None
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError):
            raise RuntimeFault("provider_response_invalid", "Model response has an invalid format", 502) from None

    @staticmethod
    def _parse(raw: bytes) -> ModelResponse:
        def reject_constant(value):
            raise ValueError("Non-finite response value")
        data = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or len(data["choices"]) != 1:
            raise ValueError
        choice = data["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError
        message = choice["message"]
        if message.get("role") != "assistant":
            raise ValueError
        content = message.get("content")
        if content is not None and (not isinstance(content, str) or len(content) > 65536):
            raise ValueError
        finish = choice.get('finish_reason')
        if not isinstance(finish,str) or finish not in {'stop','tool_calls'}:
            tokens_in,tokens_out,known,model,request_id=OpenAICompatibleProvider._usage_metadata(data)
            if request_id is not None:
                try:request_id=ProviderHTTPDiagnostic(200,HTTPErrorCategory.UNKNOWN_HTTP_ERROR,request_id).request_id
                except ValueError:request_id=None
            diagnostic=ProviderCompletionDiagnostic(finish if finish in ('length','content_filter') else 'unknown',
                content or '',tokens_in,tokens_out,known,model,request_id,bool(message.get('tool_calls')))
            raise RuntimeFault('provider_incomplete','Model response did not finish normally',502,provider_completion=diagnostic)
        calls = message.get("tool_calls")
        if calls is None:
            calls = []
        if not isinstance(calls, list) or len(calls) > 32:
            raise ValueError
        normalized: list[dict[str, JsonValue]] = []
        ids: set[str] = set()
        for call in calls:
            if not isinstance(call, dict) or call.get("type") != "function":
                raise ValueError
            identifier, function = call.get("id"), call.get("function")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 128 or identifier in ids:
                raise ValueError
            ids.add(identifier)
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"] or len(function["name"]) > 64:
                raise ValueError
            arguments = function.get("arguments")
            if not isinstance(arguments, str) or len(arguments) > 16384:
                raise ValueError
            arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError
            json.dumps(arguments, allow_nan=False)
            normalized.append({"id": identifier, "name": function["name"], "arguments": arguments})
        if not normalized and (not isinstance(content, str) or not content.strip()):
            raise ValueError
        if choice["finish_reason"] == "tool_calls" and not normalized:
            raise ValueError
        input_tokens,output_tokens,usage_known,model,request_id=OpenAICompatibleProvider._usage_metadata(data)
        return ModelResponse(content or "", tuple(normalized), input_tokens, output_tokens, request_id, usage_known, model, finish)

    @staticmethod
    def _usage_metadata(data: dict) -> tuple[int,int,bool,str | None,str | None]:
        if (self_model := data.get("model")) is not None:
            if not isinstance(self_model, str) or not self_model.strip() or len(self_model) > 128 or any(ord(c) < 32 for c in self_model):
                raise ValueError
        usage = data.get("usage")
        if usage is not None and (not isinstance(usage, dict)
                                  or "prompt_tokens" not in usage or "completion_tokens" not in usage):
            raise ValueError
        usage_known = isinstance(usage, dict) and "prompt_tokens" in usage and "completion_tokens" in usage
        input_tokens = usage["prompt_tokens"] if usage_known else 0
        output_tokens = usage["completion_tokens"] if usage_known else 0
        if any(type(value) is not int or not 0 <= value <= 10000000 for value in (input_tokens, output_tokens)):
            raise ValueError
        request_id = data.get("id")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 128):
            raise ValueError
        if usage_known and "total_tokens" in usage and (type(usage["total_tokens"]) is not int or usage["total_tokens"] != input_tokens + output_tokens):
            raise ValueError
        return input_tokens,output_tokens,usage_known,data.get('model'),request_id
