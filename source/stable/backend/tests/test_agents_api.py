"""API integration tests using explicitly selected Mock model providers."""

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from futurecup.config import Settings
from runtime_support import response, service

SETTINGS = Settings(environment="test", database_url="sqlite:///:memory:")


def config(identifier="researcher", **changes):
    return {"id": identifier, "name": identifier, "role": "researcher",
            "responsibilities": "Research task input", "system_prompt": "You are a researcher",
            "provider": "mock", "model": "mock-echo", **changes}


def completed(client, execution_id):
    for _ in range(200):
        response_ = client.get("/api/v1/executions/" + execution_id)
        assert response_.status_code == 200
        body = response_.json()
        if body["execution"]["state"] in {"succeeded", "failed", "timed_out", "cancelled"}:
            return body
        time.sleep(.005)
    raise AssertionError("Mock runtime did not complete within test deadline")


def test_mock_management_execution_and_query():
    with TestClient(create_app(SETTINGS)) as client:
        assert client.post("/api/v1/agents", json=config()).status_code == 201
        assert client.post("/api/v1/agents", json=config("reviewer", role="reviewer")).status_code == 201
        assert len(client.get("/api/v1/agents").json()) == 2
        assert client.get("/api/v1/agents/reviewer").json()["role"] == "reviewer"
        assert client.post("/api/v1/agents", json=config()).status_code == 409
        submitted = client.post("/api/v1/agents/researcher/executions", json={"input": "hello"})
        assert submitted.status_code == 202
        key = submitted.json()["execution"]["id"]
        assert submitted.headers["Location"] == "/api/v1/executions/" + key
        result = completed(client, key)
        assert result["execution"]["state"] == "succeeded"
        assert result["simulated"] and result["output"] == "[MOCK SIMULATED] hello"
        assert result["call_count"] == 1 and not result["usage_complete"]
        assert client.get("/api/v1/health").json() == {"status": "ok", "scope": "process"}
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        assert "/api/v1/agents/{agent_id}/executions" in schema.json()["paths"]


def test_mock_api_tool_execution():
    with TestClient(create_app(SETTINGS)) as client:
        created = client.post("/api/v1/agents", json=config(model="mock-calculator", tools=[{"id": "calculator"}]))
        assert created.status_code == 201
        submitted = client.post("/api/v1/agents/researcher/executions", json={"input": '{"operation":"multiply","a":6,"b":7}'})
        result = completed(client, submitted.json()["execution"]["id"])
        assert result["execution"]["state"] == "succeeded"
        assert result["simulated"] and result["tool_calls"][0]["output"] == {"result": 42}
        assert result["call_count"] == 3


def test_version_update_disable_and_historical_query():
    with TestClient(create_app(SETTINGS)) as client:
        client.post("/api/v1/agents", json=config())
        updated = config(version="2", role="reviewer", expected_version="1")
        updated.pop("id")
        assert client.put("/api/v1/agents/researcher", json=updated).status_code == 200
        assert client.get("/api/v1/agents/researcher?version=1").json()["role"] == "researcher"
        assert client.put("/api/v1/agents/researcher", json=updated).status_code == 409
        assert client.post("/api/v1/agents/researcher/disable").json()["enabled"] is False
        result = client.post("/api/v1/agents/researcher/executions", json={"input": "blocked"})
        assert result.status_code == 409 and result.json()["error"]["code"] == "agent_disabled"


@pytest.mark.parametrize("patch", [
    {"name": " "}, {"id": "bad/id"}, {"provider": "arbitrary"},
    {"api_key": "must-not-echo-secret"}, {"max_concurrency": 0},
    {"tools": [{"id": "calculator"}, {"id": "calculator"}]},
    {"tools": [{"id": "shell"}]},
])
def test_api_agent_validation_and_safe_errors(patch):
    with TestClient(create_app(SETTINGS)) as client:
        result = client.post("/api/v1/agents", json=config(**patch))
        assert result.status_code == 422
        assert "must-not-echo-secret" not in result.text
        assert "error" in result.json()


@pytest.mark.parametrize("payload", [{"input": ""}, {"input": "x", "limits": {"max_calls": 0}},
                                      {"input": "x", "limits": {"max_calls": True}},
                                      {"input": "x", "limits": {"timeout_seconds": "NaN"}},
                                      {"input": "x", "api_key": "must-not-echo-secret"}])
def test_api_execution_input_validation(payload):
    with TestClient(create_app(SETTINGS)) as client:
        client.post("/api/v1/agents", json=config())
        result = client.post("/api/v1/agents/researcher/executions", json=payload)
        assert result.status_code == 422
        assert "must-not-echo-secret" not in result.text


def test_api_missing_resources_and_disabled_real_provider(monkeypatch):
    monkeypatch.setenv("FUTURECUP_LLM_API_KEY", "must-not-use-or-echo")
    with TestClient(create_app(SETTINGS)) as client:
        assert client.get("/api/v1/agents/missing").status_code == 404
        assert client.get("/api/v1/executions/missing").status_code == 404
        assert client.post("/api/v1/agents/missing/executions", json={"input": "x"}).status_code == 404
        assert client.post("/api/v1/agents", json=config(provider="openai_compatible", model="unverified-model")).status_code == 201
        result = client.post("/api/v1/agents/researcher/executions", json={"input": "no network"})
        assert result.status_code == 409 and result.json()["error"]["code"] == "provider_unavailable"
        assert "must-not-use-or-echo" not in client.get("/api/v1/agents").text + result.text


def test_mock_api_cancellation_and_app_isolation():
    class SlowMock:
        is_simulated = True
        async def infer(self, request):
            await asyncio.sleep(10)
            return response()
    with TestClient(create_app(SETTINGS, runtime=service(SlowMock()))) as client:
        client.post("/api/v1/agents", json=config())
        key = client.post("/api/v1/agents/researcher/executions", json={"input": "cancel"}).json()["execution"]["id"]
        result = client.post("/api/v1/executions/" + key + "/cancel")
        assert result.status_code == 200 and result.json()["execution"]["state"] == "cancelled"
    with TestClient(create_app(SETTINGS)) as other:
        assert other.get("/api/v1/agents").json() == []
