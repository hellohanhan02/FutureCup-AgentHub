"""Task 07 console API acceptance. Generation is explicitly MOCK SIMULATED."""

from pathlib import Path
import json

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from test_workflow_api import SETTINGS, AGENT, completed

ROOT = Path(__file__).resolve().parents[2]


def initialize(client):
    for identifier, role, model in (("fc-researcher", "research", "mock-echo"), ("fc-writer", "writing", "mock-echo"),
                                     ("fc-reviewer", "review", "mock-echo"), ("fc-repair", "repair", "mock-repair-demo")):
        result = client.post("/api/v1/agents", json={**AGENT, "id": identifier, "role": role, "model": model})
        assert result.status_code == 201
    assert client.post("/api/v1/verification-policies", json={"id": "fc-demo-answer", "name": "Mock exact answer policy", "checks": [
        {"kind": "json_schema", "config": {"schema": {"type": "object", "required": ["answer"],
         "properties": {"answer": {"type": "integer", "const": 42}}, "additionalProperties": False}}}]}).status_code == 201


def demo(name):
    return json.loads((ROOT / "frontend" / "src" / "examples" / (name + ".json")).read_text("utf-8"))


def test_console_empty_state_and_bounded_query():
    with TestClient(create_app(SETTINGS)) as client:
        result = client.get("/api/v1/console/overview")
        assert result.status_code == 200
        data = result.json()
        assert data["agents"]["total"] == data["sample_size"] == data["workflows"]["total"] == 0
        assert data["recent"] == data["exceptions"] == []
        assert not any(data["verification"].values()) and not any(data["strategies"].values())
        assert client.get("/api/v1/workflow-runs").json() == []
        assert client.get("/api/v1/workflow-runs?limit=201").status_code == 422
        assert client.get("/api/v1/workflow-runs?limit=0").status_code == 422


def test_edit_agent_preserves_private_prompt_and_can_reenable():
    app = create_app(SETTINGS)
    with TestClient(app) as client:
        assert client.post("/api/v1/agents", json=AGENT).status_code == 201
        assert client.post("/api/v1/agents/analyst/disable").status_code == 200
        values = {k: v for k, v in AGENT.items() if k not in {"id", "system_prompt"}}
        edited = client.patch("/api/v1/agents/analyst", json={**values, "version": "2", "expected_version": "1", "enabled": True})
        assert edited.status_code == 200 and edited.json()["enabled"]
        assert "system_prompt" not in edited.json()
        current = client.portal.call(app.state.runtime.registry.get_current, "analyst")
        assert current.system_prompt == AGENT["system_prompt"]
        stale = client.patch("/api/v1/agents/analyst", json={**values, "version": "3", "expected_version": "1"})
        assert stale.status_code == 409
        explicit = client.patch("/api/v1/agents/analyst", json={**values, "version": "3", "expected_version": "2", "system_prompt": "Explicit replacement Mock prompt"})
        assert explicit.status_code == 200 and "system_prompt" not in explicit.json()
        assert client.portal.call(app.state.runtime.registry.get_current, "analyst").system_prompt == "Explicit replacement Mock prompt"


def test_preflight_checks_semantics_without_saving_or_execution():
    with TestClient(create_app(SETTINGS)) as client:
        initialize(client)
        value = demo("sequential")
        result = client.post("/api/v1/workflows/validate", json=value)
        assert result.status_code == 200 and result.json() == {"valid": True, "nodes": 3, "edges": 2, "persisted": False}
        assert client.get("/api/v1/workflows").json() == [] and client.get("/api/v1/workflow-runs").json() == []
        value["edges"].append({"source": "review", "target": "research"})
        assert client.post("/api/v1/workflows/validate", json=value).status_code == 422
        missing = demo("sequential")
        missing["nodes"][0]["config"]["agent_id"] = "missing"
        assert client.post("/api/v1/workflows/validate", json=missing).status_code == 404
        missing = demo("selfloop")
        missing["nodes"][0]["config"]["verification"]["id"] = "missing"
        assert client.post("/api/v1/workflows/validate", json=missing).status_code == 404


@pytest.mark.parametrize("name", ["sequential", "parallel", "selfloop"])
def test_frontend_shared_demo_definitions_execute_real_engine(name):
    with TestClient(create_app(SETTINGS)) as client:
        initialize(client)
        value = demo(name)
        assert client.post("/api/v1/workflows/validate", json=value).status_code == 200
        assert client.post("/api/v1/workflows", json=value).status_code == 201
        submitted = client.post("/api/v1/workflows/" + value["id"] + "/executions", json={"input": {"topic": "Explicit Mock integration task"}})
        assert submitted.status_code == 202
        report = completed(client, submitted.json()["run"]["id"])
        assert report["run"]["state"] == "succeeded" and report["simulated"]
        overview = client.get("/api/v1/console/overview").json()
        assert overview["sample_size"] == overview["runs"]["succeeded"] == 1
        assert overview["strategies"]["caas"] == 1
        assert len(client.get("/api/v1/workflow-runs").json()) == 1
        assert client.get("/api/v1/workflow-runs").json()[0]["id"] == report["run"]["id"]
        if name == "selfloop":
            node_id = report["nodes"][0]["id"]
            assert report["verified"] and report["output"] == {"repair": {"answer": 42}}
            assert len(client.get(f"/api/v1/self-loops/{node_id}/attempts").json()) == 2
            assert [v["status"] for v in client.get(f"/api/v1/self-loops/{node_id}/verifications").json()] == ["FAILED", "PASSED"]
            assert overview["verification"]["PASSED"] == overview["verification"]["FAILED"] == 1
        elif name == "parallel":
            nodes = {n["node_id"]: n for n in report["nodes"]}
            assert set(nodes["join"]["output"]) == {"research", "analysis"}
            assert nodes["join"]["started_at"] >= max(nodes["research"]["ended_at"], nodes["analysis"]["ended_at"])
            assert not report["verified"]
        else:
            nodes = report["nodes"]
            assert nodes[1]["input"] == nodes[0]["output"] and nodes[2]["input"] == nodes[1]["output"]
            assert not report["verified"]


def test_overview_counts_root_runs_once_but_includes_child_verifications():
    with TestClient(create_app(SETTINGS)) as client:
        initialize(client)
        child = demo("selfloop")
        assert client.post("/api/v1/workflows", json=child).status_code == 201
        parent = {"id": "parent", "name": "Mock nested", "nodes": [{"id": "child", "kind": "subworkflow", "config": {"workflow": {"id": child["id"], "version": "1"}}}]}
        assert client.post("/api/v1/workflows", json=parent).status_code == 201
        submitted = client.post("/api/v1/workflows/parent/executions", json={"input": "Explicit Mock nested"})
        result = completed(client, submitted.json()["run"]["id"])
        assert result["verified"]
        overview = client.get("/api/v1/console/overview").json()
        assert overview["sample_size"] == overview["runs"]["succeeded"] == 1
        assert overview["verification"]["PASSED"] == overview["verification"]["FAILED"] == 1


def test_overview_reports_real_failure_and_no_false_verification():
    with TestClient(create_app(SETTINGS)) as client:
        initialize(client)
        value = demo("selfloop")
        value["nodes"][0]["config"]["recovery"] = {"max_attempts": 1, "max_feedback_loops": 0}
        assert client.post("/api/v1/workflows", json=value).status_code == 201
        submitted = client.post("/api/v1/workflows/" + value["id"] + "/executions", json={"input": "Explicit Mock first-attempt failure"})
        result = completed(client, submitted.json()["run"]["id"])
        assert result["run"]["state"] == "failed"
        overview = client.get("/api/v1/console/overview").json()
        assert overview["runs"]["succeeded"] == 0 and overview["runs"]["terminal"] == 1
        assert overview["verification"]["PASSED"] == 0 and overview["verification"]["FAILED"] == 1
        assert overview["exceptions"][0]["id"] == result["run"]["id"]
