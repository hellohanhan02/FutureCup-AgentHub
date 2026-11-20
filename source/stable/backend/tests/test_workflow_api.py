"""ASGI workflow API tests. Every model call explicitly uses a Mock provider."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from futurecup.api.workflows import definition_response
from futurecup.config import Settings
from runtime_support import response, service
from workflow_support import task, workflow

SETTINGS = Settings(environment="test", database_url="sqlite:///:memory:", enable_real_llm=False)
AGENT = {"id": "analyst", "name": "Analyst", "role": "analysis", "responsibilities": "Test input",
         "system_prompt": "Explicit Mock API test", "provider": "mock", "model": "mock-echo"}


def payload(definition=None):
    return definition_response(definition or workflow()).model_dump(mode="json")


def completed(client, run_id):
    for _ in range(300):
        result = client.get("/api/v1/workflow-runs/" + run_id)
        assert result.status_code == 200
        body = result.json()
        if body["run"]["state"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(.005)
    raise AssertionError("Workflow did not reach a terminal state")


def test_workflow_create_list_query_execute_nodes_and_events():
    with TestClient(create_app(SETTINGS)) as client:
        assert client.post("/api/v1/agents", json=AGENT).status_code == 201
        definition = payload()
        assert client.post("/api/v1/workflows", json=definition).status_code == 201
        assert client.post("/api/v1/workflows", json=definition).status_code == 409
        assert client.get("/api/v1/workflows").json()[0]["id"] == "test-flow"
        assert client.get("/api/v1/workflows/test-flow?version=1").json() == definition
        created = client.post("/api/v1/workflows/test-flow/executions", json={"input": "API workflow"})
        assert created.status_code == 202
        run_id = created.json()["run"]["id"]
        assert created.headers["Location"] == "/api/v1/workflow-runs/" + run_id
        report = completed(client, run_id)
        assert report["run"]["state"] == "succeeded" and not report["verified"] and report["simulated"]
        assert report["output"] == {"work": "[MOCK SIMULATED] API workflow"}
        node = client.get(f"/api/v1/workflow-runs/{run_id}/nodes/work").json()
        assert node["execution_id"] and node["state"] == "succeeded" and not node["verified"]
        assert client.get(f"/api/v1/workflow-runs/{run_id}/nodes").json() == [node]
        events = client.get(f"/api/v1/workflow-runs/{run_id}/events?after=2").json()
        assert events and all(event["sequence"] > 2 for event in events)
        assert client.get(f"/api/v1/workflow-runs/{run_id}/events?after=-1").status_code == 422
        assert client.post(f"/api/v1/workflow-runs/{run_id}/cancel").json()["run"]["state"] == "succeeded"
        assert client.get(f"/api/v1/workflow-runs/{run_id}/nodes/missing").status_code == 404
        assert client.get("/api/v1/health").json() == {"status": "ok", "scope": "process"}
        assert "/api/v1/workflows" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("patch", [
    {"nodes": []}, {"edges": [{"source": "missing", "target": "work"}]},
    {"edges": [{"source": "work", "target": "work"}]}, {"max_concurrency": True},
    {"error_policy": "ignore_all_errors"}, {"api_key": "fake-must-not-echo"},
    {"nodes": [{"id": "work", "kind": "task", "config": {"agent_id": "analyst", "output_mode": {}}}]},
])
def test_invalid_api_definitions_return_422_not_internal_errors(patch):
    with TestClient(create_app(SETTINGS)) as client:
        result = client.post("/api/v1/workflows", json={**payload(), **patch})
        assert result.status_code == 422
        assert "fake-must-not-echo" not in result.text


def test_api_input_validation_and_unknown_resources():
    with TestClient(create_app(SETTINGS)) as client:
        client.post("/api/v1/agents", json=AGENT)
        definition = payload(workflow(input_schema={"type": "object", "required": ["value"]}))
        client.post("/api/v1/workflows", json=definition)
        assert client.post("/api/v1/workflows/test-flow/executions", json={"input": {}}).status_code == 422
        assert client.post("/api/v1/workflows/test-flow/executions", json={}).status_code == 422
        assert client.get("/api/v1/workflows/missing").status_code == 404
        assert client.get("/api/v1/workflow-runs/missing").status_code == 404
        assert client.post("/api/v1/workflow-runs/missing/cancel").status_code == 404


def test_api_reports_execution_failure_and_cancellation():
    class SlowMock:
        is_simulated = True
        async def infer(self, request):
            await asyncio.sleep(10)
            return response()
    with TestClient(create_app(SETTINGS, runtime=service(SlowMock()))) as client:
        client.post("/api/v1/agents", json=AGENT)
        client.post("/api/v1/workflows", json=payload())
        accepted = client.post("/api/v1/workflows/test-flow/executions", json={"input": "cancel"})
        run_id = accepted.json()["run"]["id"]
        result = client.post(f"/api/v1/workflow-runs/{run_id}/cancel")
        assert result.status_code == 200 and result.json()["run"]["state"] == "cancelled"
    with TestClient(create_app(SETTINGS)) as client:
        client.post("/api/v1/agents", json=AGENT)
        client.post("/api/v1/workflows", json=payload(workflow([task("work", output_mode="json")])))
        accepted = client.post("/api/v1/workflows/test-flow/executions", json={"input": "not JSON"})
        report = completed(client, accepted.json()["run"]["id"])
        assert report["run"]["state"] == "failed" and report["output"] is None
        assert report["nodes"][0]["error_code"] == "workflow_node_output_invalid"


def test_all_three_json_examples_through_api():
    root = Path(__file__).resolve().parents[2] / "examples/workflows"
    with TestClient(create_app(SETTINGS)) as client:
        for agent in json.loads((root / "agents.json").read_text(encoding="utf-8")):
            assert client.post("/api/v1/agents", json=agent).status_code == 201
        for name in ("increment-body", "sequential", "parallel", "branch-loop"):
            assert client.post("/api/v1/workflows", json=json.loads((root / f"{name}.json").read_text(encoding="utf-8"))).status_code == 201
        for workflow_id, value, expected in [
            ("sequential-demo", {"topic": "API example"}, "succeeded"),
            ("parallel-demo", {"topic": "API example"}, "succeeded"),
            ("branch-loop-demo", {"score": 0}, "failed"),
            ("branch-loop-demo", {"score": 3}, "succeeded"),
        ]:
            accepted = client.post(f"/api/v1/workflows/{workflow_id}/executions", json={"input": value})
            assert accepted.status_code == 202
            report = completed(client, accepted.json()["run"]["id"])
            assert report["run"]["state"] == expected and report["verified"] is False
