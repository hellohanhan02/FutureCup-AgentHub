"""Scheduling diagnostics and WorkflowEngine integration using explicit Mock LLMs."""

import time

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from futurecup.config import Settings

SETTINGS = Settings(environment="test", database_url="sqlite:///:memory:", enable_real_llm=False)


def create_agents(client):
    for identifier, skills in (("a", []), ("b", ["analysis"])):
        result = client.post("/api/v1/agents", json={"id": identifier, "name": identifier, "role": "analysis",
            "responsibilities": "Test scheduling", "system_prompt": "private-prompt-not-for-diagnostics",
            "skills": skills, "task_types": ["analysis"], "provider": "mock", "model": "mock-echo"})
        assert result.status_code == 201 and "system_prompt" not in result.json()


def definition(strategy="caas", **config):
    return {"id": "scheduled", "name": "Scheduled flow", "version": "1", "scheduling_strategy": strategy,
            "nodes": [{"id": "work", "kind": "task", "config": {"candidate_agents": ["a", "b"],
                "task_type": "analysis", "required_skills": ["analysis"], **config}}]}


def finish(client, run_id):
    for _ in range(200):
        result = client.get("/api/v1/workflow-runs/" + run_id)
        assert result.status_code == 200
        if result.json()["run"]["state"] in {"succeeded", "failed", "cancelled"}:
            return result.json()
        time.sleep(.005)
    raise AssertionError("Scheduling did not reach a terminal state")


@pytest.mark.parametrize("strategy", ["fixed", "round_robin", "caas"])
def test_dynamic_workflow_selection_and_public_diagnostics(strategy):
    with TestClient(create_app(SETTINGS)) as client:
        create_agents(client)
        cold = client.get("/api/v1/scheduling/agents/b?task_type=analysis").json()
        assert cold["stats"]["sample_count"] == 0 and cold["stats"]["empirical_success_rate"] is None
        assert cold["estimated_reliability"] == .5 and cold["low_samples"]
        assert client.post("/api/v1/workflows", json=definition(strategy)).status_code == 201
        accepted = client.post("/api/v1/workflows/scheduled/executions", json={"input": "task-sensitive-input"})
        assert accepted.status_code == 202
        run_id = accepted.json()["run"]["id"]
        result = finish(client, run_id)
        node = result["nodes"][0]
        assert result["run"]["state"] == "succeeded" and result["simulated"] and not result["verified"]
        assert node["agent_ref"]["id"] == "b" and node["scheduling_decision_id"]
        assert node["assigned_at"] and node["ready_at"] and node["waiting_ms"] >= 0
        decisions = client.get(f"/api/v1/scheduling/decisions?run_id={run_id}")
        assert decisions.status_code == 200
        record = decisions.json()[0]
        assert record["strategy"] == strategy and record["selected"]["id"] == "b"
        assert record["candidates"][0]["reasons"] == ["skills_mismatch"]
        assert record["candidates"][1]["profile"]["stats"]["successes"] == 0
        history = client.get("/api/v1/scheduling/agents/b/history").json()
        assert history[0]["successes"] == 1 and history[0]["source"] == "observed_mock"
        assert "private-prompt-not-for-diagnostics" not in decisions.text
        assert "task-sensitive-input" not in decisions.text
        assert "system_prompt" not in decisions.text + str(history)
        assert set(client.get("/api/v1/scheduling/strategies").json()["strategies"]) == {"fixed", "round_robin", "caas"}
        assert len(client.get("/api/v1/scheduling/agents").json()) == 2


@pytest.mark.parametrize("config", [{"priority": True}, {"priority": 101}, {"schedule_timeout_seconds": 0},
                                     {"candidate_agents": "a"}, {"required_skills": [12]}, {"no_candidate_policy": "forever"},
                                     {"required_tools": [{"id": "calculator"}]}])
def test_scheduling_workflow_input_validation(config):
    with TestClient(create_app(SETTINGS)) as client:
        assert client.post("/api/v1/workflows", json=definition(**config)).status_code == 422


def test_no_candidate_and_unknown_diagnostic_resources():
    with TestClient(create_app(SETTINGS)) as client:
        create_agents(client)
        client.post("/api/v1/workflows", json=definition(required_skills=["unavailable-skill"]))
        accepted = client.post("/api/v1/workflows/scheduled/executions", json={"input": "x"})
        result = finish(client, accepted.json()["run"]["id"])
        assert result["run"]["state"] == "failed" and result["output"] is None
        assert result["nodes"][0]["error_code"] == "scheduling_no_candidate"
        assert client.get("/api/v1/scheduling/agents/missing").status_code == 404
        assert client.get("/api/v1/scheduling/decisions?limit=0").status_code == 422


def test_configuration_updates_create_new_version_with_cold_history():
    with TestClient(create_app(SETTINGS)) as client:
        create_agents(client)
        updated = {"name": "b", "role": "analysis", "responsibilities": "Updated manual profile",
            "system_prompt": "private-prompt-not-for-diagnostics", "skills": ["analysis"], "task_types": ["analysis"],
            "provider": "mock", "model": "mock-echo", "version": "2", "expected_version": "1",
            "estimated_cost": 2, "estimated_latency_ms": 100, "available": False}
        assert client.put("/api/v1/agents/b", json=updated).status_code == 200
        profile = client.get("/api/v1/scheduling/agents/b?task_type=analysis").json()
        assert profile["agent_ref"]["version"] == "2" and profile["configuration_source"] == "agent_definition"
        assert profile["estimated_cost"] == 2 and not profile["available"]
        assert profile["stats"]["sample_count"] == 0


@pytest.mark.parametrize("values", [{"estimated_cost": -1}, {"estimated_latency_ms": "NaN"}, {"task_types": []}])
def test_invalid_manual_capability_configuration_is_rejected(values):
    with TestClient(create_app(SETTINGS)) as client:
        result = client.post("/api/v1/agents", json={"id": "invalid", "name": "invalid", "role": "test",
            "responsibilities": "Test", "system_prompt": "Test", **values})
        assert result.status_code == 422
