"""Server-only policy parsing, exact-origin allowlists and credential guards."""

import asyncio
from dataclasses import fields, asdict
import ipaddress
import json
import os
import re
import socket
from urllib.parse import urlsplit

from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.llm import ModelPolicy, ProviderProfile, RetryPolicy, RealCallSafety

OFFICIAL = {"deepseek": {"api.deepseek.com"}, "qwen": {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com", "cn-hongkong.dashscope.aliyuncs.com"}}


def safety_from(settings: Settings) -> RealCallSafety | None:
    if settings.real_call_safety_json is None:
        return None
    try:
        if len(settings.real_call_safety_json) > 4096:
            raise ValueError
        value = json.loads(settings.real_call_safety_json)
        if not isinstance(value, dict) or not value:
            raise ValueError
        return RealCallSafety(**value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Invalid real-call preparation safety configuration") from None


def validate_origin(profile: ProviderProfile) -> None:
    try:
        parsed = urlsplit(profile.base_url)
        host = parsed.hostname
        loopback = host in {"127.0.0.1", "::1"}
        if (not host or parsed.username or parsed.password or parsed.query or parsed.fragment
                or "\\" in profile.base_url or "%" in profile.base_url or any(ord(c) < 33 for c in profile.base_url)
                or not re.fullmatch(r"[A-Za-z0-9/._:-]*", parsed.path)):
            raise ValueError
        if loopback:
            if not profile.allow_loopback or profile.kind != "openai_compatible" or host not in profile.allowed_hosts:
                raise ValueError
            if parsed.scheme not in {"http", "https"}:
                raise ValueError
        else:
            if parsed.scheme != "https" or parsed.port not in (None, 443):
                raise ValueError
            if profile.kind == "deepseek":
                if host not in OFFICIAL["deepseek"] or parsed.path.rstrip("/") not in ("", "/v1"):
                    raise ValueError
            elif profile.kind == "qwen":
                if (host not in OFFICIAL["qwen"] and not re.fullmatch(r"[a-z0-9-]+\.(cn-beijing|ap-southeast-1|cn-hongkong|ap-northeast-1|us-east-1)\.maas\.aliyuncs\.com", host)) or parsed.path.rstrip("/") != "/compatible-mode/v1":
                    raise ValueError
            elif profile.kind == "openai_compatible":
                if host not in profile.allowed_hosts or host == "localhost" or "." not in host:
                    raise ValueError
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    pass
                else:
                    raise ValueError
            else:
                raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid provider endpoint or server allowlist") from None


async def validate_dns(profile: ProviderProfile) -> None:
    host = urlsplit(profile.base_url).hostname
    if profile.allow_loopback and host in {"127.0.0.1", "::1"}:
        return
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError):
        raise RuntimeFault("provider_address_denied", "Endpoint DNS is unavailable or resolves to a non-public address", 502) from None


def profiles_from(settings: Settings) -> tuple[ProviderProfile, ...]:
    profiles = [ProviderProfile("deepseek", "deepseek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
                               (ModelPolicy("deepseek-flash", response_formats=("text", "prompt_json", "json_object"), thinking_disabled=True),)),
                ProviderProfile("qwen", "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", (ModelPolicy("qwen-plus"),)),
                ProviderProfile("openai_compatible", "openai_compatible", settings.llm_base_url or "", settings.llm_api_key_env, (), settings.llm_allowed_hosts)]
    if settings.provider_config_json:
        try:
            raw = json.loads(settings.provider_config_json)
            if not isinstance(raw, list) or len(raw) > 16 or len(settings.provider_config_json) > 65536:
                raise ValueError
            seen = set()
            for value in raw:
                if not isinstance(value, dict) or set(value) - {f.name for f in fields(ProviderProfile)} or value["id"] in seen:
                    raise ValueError
                seen.add(value["id"])
                value = dict(value)
                value["models"] = tuple(ModelPolicy(**{**m, "response_formats": tuple(m.get("response_formats", ("text", "prompt_json")))}) for m in value["models"])
                value["allowed_hosts"] = tuple(value.get("allowed_hosts", ()))
                value["retry"] = RetryPolicy(**value.get("retry", {}))
                profile = ProviderProfile(**value)
                if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile.id) or profile.id == "mock"
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", profile.key_env)
                        or type(profile.enabled) is not bool or type(profile.allow_loopback) is not bool
                        or isinstance(profile.timeout_seconds, bool) or not 0 < profile.timeout_seconds <= 300
                        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", profile.revision)
                        or not 1 <= len(profile.models) <= 32 or len({m.id for m in profile.models}) != len(profile.models)
                        or any(not isinstance(h, str) for h in profile.allowed_hosts)):
                    raise ValueError
                validate_origin(profile)
                profiles = [p for p in profiles if p.id != profile.id] + [profile]
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            # Do not echo JSON: an operator may accidentally include a literal credential.
            raise ValueError("Invalid secret-free provider configuration; check server configuration schema") from None
    elif settings.enable_real_llm and settings.llm_base_url:
        # Legacy base URL remains server-only, but now requires an explicit host allowlist/model policy.
        raise ValueError("Real LLM enablement requires FUTURECUP_LLM_PROVIDERS_JSON with explicit model policies")
    for profile in profiles:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", profile.key_env):
            raise ValueError("Invalid credential environment variable name")
        if profile.base_url:
            validate_origin(profile)
    EnvironmentSecretGuard(tuple(p.key_env for p in profiles)).check([asdict(p) for p in profiles])
    return tuple(profiles)


class EnvironmentSecretGuard:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names

    def check(self, value: object) -> None:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
        for name in self.names:
            key = os.environ.get(name, "").strip()
            if key and len(key) >= 8 and (key in serialized or json.dumps(key, ensure_ascii=False)[1:-1] in serialized):
                raise RuntimeFault("sensitive_data_rejected", "Configured credential detected in input or response", 422)
