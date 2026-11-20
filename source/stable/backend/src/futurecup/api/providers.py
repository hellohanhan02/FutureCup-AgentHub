"""Read-only secret-free model catalog. No ping/probe endpoint sends model requests."""

from dataclasses import asdict
import os

from fastapi import APIRouter
from pydantic import Field

from futurecup.api.agents import StrictModel, Identifier
from futurecup.application.runtime import AgentRuntimeService
from futurecup.domain.errors import RuntimeFault


class EstimateInput(StrictModel):
    model: str = Field(min_length=1, max_length=128)
    input_tokens_hint: int = Field(ge=0, le=10000000, strict=True)
    max_output_tokens: int = Field(ge=1, le=4096, strict=True)
    calls: int = Field(default=1, ge=1, le=128, strict=True)


def build_provider_router(runtime: AgentRuntimeService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/model-providers", tags=["models"])

    @router.get("")
    async def catalog():
        result = [{"id": "mock", "kind": "mock", "mode": "mock", "enabled": True, "configured": True,
            "connection": {"state": "mock_local", "error_code": None, "at": None}, "credentials_configured": False,
            "base_url": None, "models": [{"id": name, "supports_tools": name == "mock-calculator", "response_formats": ["text", "prompt_json"] if name in {"mock-repair-demo", "mock-scenario08"} else ["text"],
            "max_output_tokens": 4096, "max_context_bytes": 131072, "currency": None, "input_price_per_million": None, "output_price_per_million": None}
            for name in ("mock-echo", "mock-calculator", "mock-repair-demo", "mock-scenario08")]}]
        for profile in runtime.provider_profiles:
            active = runtime.providers.get(profile.id)
            configured = bool(profile.base_url and profile.models)
            credentials = bool(os.environ.get(profile.key_env, "").strip())
            if not runtime.real_calls_enabled or not profile.enabled:
                state, reason = "disabled", "real_calls_disabled"
            elif not configured:
                state, reason = "unconfigured", "provider_configuration_missing"
            elif not credentials:
                state, reason = "missing_credentials", "provider_credentials_missing"
            else:
                state, reason = "ready_not_tested", None
            observed = getattr(active, "last_outcome", None)
            if state == "ready_not_tested" and observed and observed["state"] != "not_checked":
                connection = observed
            else:
                connection = {"state": state, "error_code": reason, "at": None}
            result.append({"id": profile.id, "kind": profile.kind, "mode": "real", "enabled": active is not None,
                "configured": configured, "base_url": profile.base_url or None, "key_env": profile.key_env,
                "credentials_configured": credentials, "connection": connection, "models": [asdict(m) for m in profile.models],
                "timeout_seconds": profile.timeout_seconds, "retry": asdict(profile.retry), "revision": profile.revision,
                "capabilities_source": "server_declared_not_live_verified"})
        runtime.check_sensitive(result)
        return {"real_calls_enabled": runtime.real_calls_enabled, "providers": result, "network_probe_performed": False,
                "preparation_safety": ({k:v for k,v in asdict(runtime.real_safety).items()
                    if k!='profile' or runtime.real_safety.profile!='preparation'} if runtime.real_safety else None)}

    @router.post("/{provider_id}/estimate")
    async def estimate(provider_id: Identifier, data: EstimateInput):
        profile = next((p for p in runtime.provider_profiles if p.id == provider_id), None)
        model = next((m for m in profile.models if m.id == data.model), None) if profile else None
        if model is None:
            raise RuntimeFault("model_not_configured", "Model is not configured on the server", 404)
        if data.max_output_tokens > model.max_output_tokens:
            raise RuntimeFault("model_output_limit", "Output limit exceeds model policy", 422)
        value = model.estimate(data.input_tokens_hint, data.max_output_tokens)
        return {"estimated_cost": value * data.calls if value is not None else None,
            "currency": model.currency if value is not None else None, "basis": "configured_estimate_not_invoice",
            "input_tokens_are_user_hint": True, "exact_cost_hard_budget": False, "network_request_performed": False}

    return router
