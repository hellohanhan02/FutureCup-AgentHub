"""HTTP contract tests through ASGI; model execution is explicitly Mock only."""

from dataclasses import replace
import time

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from futurecup.api.workflows import definition_response
from runtime_support import response, service, ScriptedMockProvider
from verification_support import definition
from test_workflow_api import SETTINGS, AGENT, completed

POLICY = {"id": "answer-policy", "name": "Explicit Mock verification", "checks": [
    {"kind": "json_schema", "config": {"schema": {"type": "object", "required": ["answer"],
     "properties": {"answer": {"const": 42}}}}}]}


def prepare(client, flow=None):
    assert client.post("/api/v1/agents", json=AGENT).status_code == 201
    assert client.post("/api/v1/verification-policies", json=POLICY).status_code == 201
    flow = flow or definition(max_attempts=2, max_feedback_loops=1, on_failure="prompt_retry")
    assert client.post("/api/v1/workflows", json=definition_response(flow).model_dump(mode="json")).status_code == 201
    submitted = client.post("/api/v1/workflows/test-flow/executions", json={"input": "Explicit Mock HTTP test"})
    assert submitted.status_code == 202
    return submitted.json()["run"]["id"]


def test_policy_execution_audit_health_and_input_errors():
    runtime = service(ScriptedMockProvider(response('{"bad":0}'), response('{"answer":42}')))
    with TestClient(create_app(SETTINGS, runtime)) as client:
        run_id = prepare(client)
        report = completed(client, run_id)
        assert report["verified"] and report["simulated"] and report["run"]["state"] == "succeeded"
        node = report["nodes"][0]
        base = "/api/v1/self-loops/" + node["id"]
        assert client.get(base).json()["state"] == "succeeded"
        assert len(client.get(base + "/attempts").json()) == 2
        assert [v["status"] for v in client.get(base + "/verifications").json()] == ["FAILED", "PASSED"]
        assert client.get(base + "/feedback").json()[0]["category"] == "OUTPUT_FORMAT_ERROR"
        assert len(client.get("/api/v1/verification-policies").json()) == 1
        assert client.get("/api/v1/verification-policies/answer-policy").status_code == 200
        assert client.post("/api/v1/verification-policies", json=POLICY).status_code == 409
        assert client.get("/api/v1/self-loops/missing").status_code == 404
        assert client.get("/api/v1/self-loops/missing/attempts").status_code == 404
        assert client.get("/api/v1/verification-policies/missing").status_code == 404
        health = client.get("/api/v1/health")
        assert health.status_code == 200 and health.json() == {"status": "ok", "scope": "process"}
        assert "system_prompt" not in str(client.get(base + "/attempts").json())


@pytest.mark.parametrize("patch", [
    {"checks": []}, {"checks": [{"kind": "shell", "config": {}}]},
    {"checks": [{"kind": "json_schema", "config": {"schema": {"$ref": "http://invalid.example"}}}]},
    {"checks": [{"kind": "numeric", "config": {"path": "x", "expected": "bad"}}]},
    {"checks": [{"kind": "assertion", "config": {"path": "x", "op": "exec", "value": 1}}]},
    {"timeout_seconds": 0}, {"api_key": "never-reflect-this"},
])
def test_invalid_policy_http_validation(patch):
    with TestClient(create_app(SETTINGS)) as client:
        result = client.post("/api/v1/verification-policies", json={**POLICY, **patch})
        assert result.status_code == 422
        assert "never-reflect-this" not in result.text


@pytest.mark.parametrize("recovery", [{"max_attempts": 0}, {"max_feedback_loops": -1}, {"on_failure": "allow_anything"},
                                      {"on_failure": "fallback", "fallback_node": "missing"}, {"timeout_seconds": 1000}])
def test_invalid_recovery_http_validation(recovery):
    with TestClient(create_app(SETTINGS)) as client:
        assert client.post("/api/v1/verification-policies", json=POLICY).status_code == 201
        result = client.post("/api/v1/workflows", json=definition_response(definition(**recovery)).model_dump(mode="json"))
        assert result.status_code == 422


def test_human_authorization_retry_rejects_stale_and_cannot_override_verifier(monkeypatch):
    monkeypatch.setenv("FUTURECUP_APPROVAL_TOKEN", "local-test-authorization-not-a-real-secret")
    runtime = service(ScriptedMockProvider(response('{"bad":0}'), response('{"answer":42}')))
    with TestClient(create_app(SETTINGS, runtime)) as client:
        run_id = prepare(client, definition(max_attempts=2, max_feedback_loops=1, on_failure="human", timeout_seconds=3))
        for _ in range(200):
            tickets = client.get("/api/v1/human-interventions").json()
            if tickets:
                break
            time.sleep(.005)
        assert len(tickets) == 1
        ticket = tickets[0]
        snapshot = client.get("/api/v1/workflow-runs/" + run_id).json()
        assert snapshot["nodes"][0]["state"] == "waiting_human" and snapshot["run"]["state"] == "paused"
        payload = {"revision": 0, "attempt_id": ticket["attempt_id"], "artifact_ref": ticket["artifact_ref"],
                   "decision": "retry", "actor": "local-reviewer"}
        endpoint = "/api/v1/human-interventions/" + ticket["id"] + "/resolve"
        assert client.post(endpoint, json=payload).status_code == 403
        headers = {"X-Harness-Approval-Token": "local-test-authorization-not-a-real-secret"}
        assert client.post(endpoint, json={**payload, "decision": "PASSED"}, headers=headers).status_code == 422
        assert client.post(endpoint, json={**payload, "attempt_id": "old"}, headers=headers).status_code == 409
        assert client.post(endpoint, json=payload, headers=headers).status_code == 200
        assert client.post(endpoint, json=payload, headers=headers).status_code == 409
        report = completed(client, run_id)
        assert report["verified"] and len(report["nodes"][0]["attempt_ids"]) == 2


@pytest.mark.parametrize("action", ["cancel", "timeout", "abort"])
def test_human_pause_can_terminate_without_publishing(action, monkeypatch):
    monkeypatch.setenv("FUTURECUP_APPROVAL_TOKEN", "test-only")
    runtime = service(ScriptedMockProvider(response('{"bad":0}')))
    with TestClient(create_app(SETTINGS, runtime)) as client:
        run_id = prepare(client, definition(max_attempts=2, max_feedback_loops=1, on_failure="human", timeout_seconds=.1 if action == "timeout" else 2))
        for _ in range(200):
            tickets = client.get("/api/v1/human-interventions").json()
            if tickets:
                break
            time.sleep(.001)
        ticket = tickets[0]
        if action == "cancel":
            assert client.post("/api/v1/workflow-runs/" + run_id + "/cancel").status_code == 200
        elif action == "abort":
            result = client.post("/api/v1/human-interventions/" + ticket["id"] + "/resolve", json={
                "revision": 0, "attempt_id": ticket["attempt_id"], "artifact_ref": ticket["artifact_ref"], "decision": "abort", "actor": "test"},
                headers={"X-Harness-Approval-Token": "test-only"})
            assert result.status_code == 200
        report = completed(client, run_id)
        assert report["run"]["state"] in {"failed", "cancelled"} and not report["verified"]
        assert report["nodes"][0]["output"] is None
        assert client.get("/api/v1/human-interventions").json()[0]["state"] in {"expired", "abort"}
