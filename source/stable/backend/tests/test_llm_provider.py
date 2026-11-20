"""OpenAI-compatible adapter tests with SIMULATED HTTP responses; no real LLM."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from futurecup.adapters.llm import OpenAICompatibleProvider
from futurecup.application.ports import ModelRequest
from futurecup.domain.errors import RuntimeFault


def request(timeout=1):
    return ModelRequest("test", ({"role": "user", "content": "test"},), 32, timeout, "trace", "test-model")


def completion(message=None, usage=None):
    return {"id": "simulated-response", "choices": [{"message": message or
            {"role": "assistant", "content": "[SIMULATED HTTP] response"}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 3, "completion_tokens": 4}}


def adapter(handler, enabled=True):
    return OpenAICompatibleProvider("https://model.invalid/v1", "TEST_RUNTIME_KEY", enabled, httpx.MockTransport(handler))


def test_simulated_http_request_response_and_tool_normalization(monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    def handler(incoming):
        assert incoming.url.path == "/v1/chat/completions"
        assert incoming.headers["Authorization"] == "Bearer fake-test-secret"
        payload = json.loads(incoming.content)
        assert payload["model"] == "test-model" and payload["max_tokens"] == 32
        assert payload["stream"] is False
        return httpx.Response(200, json=completion({"role": "assistant", "content": None, "tool_calls": [
            {"id": "tool-1", "type": "function", "function": {"name": "calculator", "arguments": '{"a":2}'}}
        ]}))
    result = asyncio.run(adapter(handler).infer(request()))
    assert result.tool_calls == ({"id": "tool-1", "name": "calculator", "arguments": {"a": 2}},)
    assert result.input_tokens == 3 and result.output_tokens == 4
    assert result.usage_reported


@pytest.mark.parametrize("status,code", [(401, "provider_http_error"), (429, "provider_rate_limited"),
                                         (500, "provider_http_error"), (302, "provider_http_error")])
def test_simulated_http_errors_do_not_expose_keys_or_body(monkeypatch, status, code):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    provider = adapter(lambda _: httpx.Response(status, text="fake-test-secret upstream body", headers={"Location": "https://untrusted.invalid"}))
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(provider.infer(request()))
    assert error.value.code == code
    assert "fake-test-secret" not in str(error.value) + repr(provider)
    assert "upstream body" not in str(error.value)


@pytest.mark.parametrize("payload", [
    {}, {"choices": []}, {"choices": [None]},
    completion({"role": "user", "content": "bad"}),
    completion({"role": "assistant", "content": ""}),
    completion(usage={"prompt_tokens": -1, "completion_tokens": 2}),
    completion({"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "calculator", "arguments": "not json"}}
    ]}),
])
def test_simulated_malformed_responses_rejected(monkeypatch, payload):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(lambda _: httpx.Response(200, json=payload)).infer(request()))
    assert error.value.code == "provider_response_invalid"


def test_simulated_missing_usage_is_explicit(monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    payload = completion()
    payload.pop("usage")
    result = asyncio.run(adapter(lambda _: httpx.Response(200, json=payload)).infer(request()))
    assert not result.usage_reported


def test_real_provider_requires_opt_in_and_credentials(monkeypatch):
    monkeypatch.delenv("TEST_RUNTIME_KEY", raising=False)
    def unexpected(_):
        raise AssertionError("Network must not be used")
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(unexpected, enabled=False).infer(request()))
    assert error.value.code == "real_provider_disabled"
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(unexpected).infer(request()))
    assert error.value.code == "provider_credentials_missing"


def test_simulated_timeout_is_bounded_even_with_custom_transport(monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    async def handler(_):
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion())
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(handler).infer(request(.01)))
    assert error.value.code == "model_timeout"


def test_simulated_connection_failure(monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    def handler(incoming):
        raise httpx.ConnectError("fake-test-secret", request=incoming)
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(handler).infer(request()))
    assert error.value.code == "provider_connection_failed"
    assert "fake-test-secret" not in str(error.value)


@pytest.mark.parametrize("base_url", ["http://external.invalid/v1", "https://user:secret@model.invalid/v1", "https://model.invalid/v1?key=secret"])
def test_unsafe_model_base_urls_rejected(base_url):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider(base_url)


def test_simulated_truncation_and_oversized_body(monkeypatch):
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-test-secret")
    payload = completion()
    payload["choices"][0]["finish_reason"] = "length"
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(lambda _: httpx.Response(200, json=payload)).infer(request()))
    assert error.value.code == "provider_incomplete"
    with pytest.raises(RuntimeFault) as error:
        asyncio.run(adapter(lambda _: httpx.Response(200, content=b"x" * 1_048_577)).infer(request()))
    assert error.value.code == "provider_response_invalid"


def test_loopback_http_transport_against_simulated_model_server(monkeypatch):
    """Real loopback HTTP, fake key and fake model response; NOT real inference."""
    monkeypatch.setenv("TEST_RUNTIME_KEY", "fake-local-only")
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            observed.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps(completion()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = OpenAICompatibleProvider(f"http://127.0.0.1:{server.server_port}/v1", "TEST_RUNTIME_KEY", True)
        result = asyncio.run(provider.infer(request()))
        assert result.content == "[SIMULATED HTTP] response"
        assert observed[0][0] == "/v1/chat/completions"
        assert observed[0][1]["model"] == "test-model"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
