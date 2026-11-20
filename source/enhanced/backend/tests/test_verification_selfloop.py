"""Real verifier/recovery/SQLite logic; all generation uses explicit Mock providers."""

import asyncio
from dataclasses import replace
import json

import pytest

from futurecup.adapters.verification import StructuredVerificationBackend
from futurecup.application.feedback import FeedbackEngine
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import NodeKind, TaskState, VersionRef, WorkflowNode, WorkflowState
from futurecup.domain.verification import ArtifactVersion, CheckEvidence, ErrorCategory, ResourceBudget, VerificationCheck, VerificationPolicy
from runtime_support import agent, response, ScriptedMockProvider, tool_call
from workflow_support import task, workflow
from verification_support import REF, CHECK, close, definition, finish, safety_engine, setup


@pytest.mark.parametrize("kind,config,value,status", [
    ("json_schema", {"schema": {"type": "integer"}}, 3, "PASSED"),
    ("json_schema", {"schema": {"type": "integer"}}, "3", "FAILED"),
    ("assertion", {"path": "x", "op": "eq", "value": 4}, {"x": 4}, "PASSED"),
    ("assertion", {"path": "x", "op": "gte", "value": 4}, {"x": 3}, "FAILED"),
    ("assertion", {"path": "missing", "op": "eq", "value": 4}, {}, "FAILED"),
    ("assertion", {"path": "x", "op": "lt", "value": 4}, {"x": "bad"}, "FAILED"),
    ("numeric", {"path": "x", "expected": 2.1, "tolerance": .11}, {"x": 2}, "PASSED"),
    ("numeric", {"path": "x", "expected": 2}, {"x": True}, "FAILED"),
    ("numeric", {"path": "x", "expected": 0, "other_path": "y"}, {"x": 2, "y": 2}, "PASSED"),
    ("numeric", {"path": "x", "expected": 0, "other_path": "y"}, {"x": 2, "y": 3}, "FAILED"),
    ("external_tests", {"test_id": "unregistered"}, {"passed": True}, "INCONCLUSIVE"),
])
def test_deterministic_checks(kind, config, value, status):
    async def scenario():
        backend = StructuredVerificationBackend()
        check = VerificationCheck(kind, config)
        backend.validate_policy(VerificationPolicy(REF, "test", (check,)))
        artifact = ArtifactVersion(VersionRef("artifact", "1"), backend.digest(value), value, True)
        result = await backend.check(check, artifact)
        assert result.status == status
    asyncio.run(scenario())


@pytest.mark.parametrize("check", [
    VerificationCheck("shell", {"command": "anything"}),
    VerificationCheck("json_schema", {"schema": {"$ref": "https://invalid.example/schema"}}),
    VerificationCheck("assertion", {"path": "x", "op": "eval", "value": "1"}),
    VerificationCheck("numeric", {"path": "x", "expected": True}),
    VerificationCheck("numeric", {"path": "x", "expected": 1, "tolerance": -1}),
    VerificationCheck("external_tests", {"test_id": "safe", "command": "forbidden"}),
    VerificationCheck("json_schema", {"schema": {"type": "string", "pattern": "(a+)+$"}}),
    VerificationCheck("json_schema", {"schema": {"type": "string", "format": "email"}}),
])
def test_invalid_policy_rejected(check):
    with pytest.raises(RuntimeFault, match="Invalid verification policy"):
        StructuredVerificationBackend().validate_policy(VerificationPolicy(REF, "invalid", (check,)))


def test_prompt_repair_versions_and_quarantine():
    async def scenario():
        provider = ScriptedMockProvider(response('{"wrong":0}'), response('{"answer":42}'))
        engine = safety_engine(provider)
        try:
            await setup(engine)
            report, attempts, results, feedback = await finish(engine, definition(max_attempts=2, max_feedback_loops=1, on_failure="prompt_retry"))
            assert report.run.state == WorkflowState.SUCCEEDED and report.verified and report.simulated
            assert report.output == {"work": {"answer": 42}}
            assert len(attempts) == len(results) == 2 and len(feedback) == 1
            assert attempts[0]["id"] != attempts[1]["id"]
            assert attempts[0]["input_digest"] != attempts[1]["input_digest"]
            assert attempts[0]["policy_digest"] == attempts[1]["policy_digest"]
            assert [r["record"]["verdict"] for r in results] == ["fail", "pass"]
            assert feedback[0]["category"] == "OUTPUT_FORMAT_ERROR"
            assert (await engine.selfloop.repository.session(report.nodes[0].id))["accepted_artifact"]["version"] == "2"
            assert report.resource_usage["calls"] == 2 and report.resource_usage["unknown_usage_calls"] == 2
            assert provider.requests[0].messages[0] == provider.requests[1].messages[0]
            assert "Repair the result" in provider.requests[1].messages[-1]["content"]
            events = [e.type for e in report.events]
            assert "node.verifying" in events and "node.recovering" in events
            assert report.nodes[0].started_at < report.nodes[0].ended_at
            assert any(e.payload.get("verification_id") == results[-1]["record"]["id"] for e in report.events)
            # Generation success is recorded independently, even when validation fails.
            stats = await engine.scheduler.repository.history(VersionRef("analyst", "1"))
            assert sum(s.successes for s in stats) == 2
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("strategy", ["fixed", "round_robin", "caas"])
def test_reassign_obeys_candidates_and_records_decisions(strategy):
    class ModelMock:
        is_simulated = True
        async def infer(self, request):
            return response('{"wrong":0}' if request.model == "primary" else '{"answer":42}')
    async def scenario():
        engine = safety_engine(ModelMock())
        try:
            await setup(engine, agents=[agent("a", model="primary"), agent("b", model="backup")])
            flow = workflow([WorkflowNode("work", NodeKind.TASK, {"candidate_agents": ["a", "b"],
                "verification": {"id": REF.id, "version": REF.version}, "output_mode": "json",
                "recovery": {"max_attempts": 3, "max_feedback_loops": 2, "max_replans": 1,
                             "reassign_after": 2, "on_failure": "reassign"}})], scheduling_strategy=strategy)
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.SUCCEEDED
            if strategy != "round_robin":
                assert [a["agent_ref"]["id"] for a in attempts] == ["a", "a", "b"]
            assert attempts[-1]["agent_ref"]["id"] == "b"
            decisions = await engine.scheduler.repository.decisions(run_id=report.run.id)
            assert len(decisions) == len(attempts) and all(a["decision_id"] for a in attempts)
            assert all(r["simulated_generation"] for r in results)
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_failure_limit_and_downstream_never_receives_invalid_output():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(*(response('{"bad":1}') for _ in range(3))))
        try:
            await setup(engine)
            original = definition(max_attempts=3, max_feedback_loops=2, on_failure="prompt_retry")
            flow = workflow([original.nodes[0], task("downstream", input={"$ref": "nodes.work.output"})], [("work", "downstream")])
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.FAILED and not report.verified and report.output is None
            assert report.nodes[0].output is None and report.nodes[1].state == TaskState.SKIPPED
            assert len(attempts) == len(results) == len(feedback) == 3
            assert report.nodes[0].error_code == "recovery_limit"
            assert all(a["state"] == "completed" for a in attempts)
            assert (await engine.selfloop.repository.session(report.nodes[0].id))["accepted_artifact"] is None
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["exception", "unbound", "unavailable", "timeout"])
def test_external_verifier_cannot_forge_pass(kind):
    async def callback(artifact):
        if kind == "exception":
            raise ValueError("private-secret-must-not-leak")
        if kind == "timeout":
            await asyncio.sleep(1)
        return CheckEvidence("external_tests", "PASSED", "unsafe", reference="wrong-digest")
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')), external={} if kind == "unavailable" else {"safe": callback})
        try:
            await setup(engine, checks=(VerificationCheck("external_tests", {"test_id": "safe"}),))
            # Pin a short timeout via a second immutable policy version.
            if kind == "timeout":
                policy = VerificationPolicy(VersionRef(REF.id, "2"), "timeout", (VerificationCheck("external_tests", {"test_id": "safe"}),), .01)
                await engine.selfloop.verifier.register(policy)
                flow = definition()
                flow.nodes[0].config["verification"]["version"] = "2"
            else:
                flow = definition()
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.FAILED and not report.verified
            assert results[0]["record"]["verdict"] == ("inconclusive" if kind == "unavailable" else "error")
            assert feedback[0]["origin"] == "verifier" and feedback[0]["category"] == "UNKNOWN"
            assert "private-secret" not in json.dumps(results)
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_trusted_external_test_is_bound_to_exact_artifact():
    async def callback(artifact):
        return CheckEvidence("external_tests", "PASSED", "preset_suite_passed", reference=artifact.digest)
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')), external={"safe": callback})
        try:
            await setup(engine, checks=(VerificationCheck("external_tests", {"test_id": "safe"}),))
            report, _, results, _ = await finish(engine)
            assert report.verified and results[0]["evidence"][0]["reference"] == results[0]["artifact_digest"]
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("error,category", [("model_timeout", "TIMEOUT"), ("tool_execution_failed", "TOOL_EXECUTION_ERROR"),
                                           ("provider_unavailable", "RESOURCE_UNAVAILABLE"), ("model_call_failed", "UNKNOWN")])
def test_execution_failure_diagnosis_does_not_poison_capability(error, category):
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(RuntimeFault(error, "sanitized")))
        try:
            await setup(engine)
            report, attempts, results, feedback = await finish(engine)
            assert report.run.state == WorkflowState.FAILED and results[0]["origin"] == "execution"
            assert feedback[0]["category"] == category
            assert not await engine.scheduler.repository.history(VersionRef("analyst", "1"))
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("limit,value", [("max_calls", 0), ("max_resource_units", 0), ("max_checks", 0),
                                        ("max_attempts", 0), ("max_feedback_loops", 0), ("max_replans", 0)])
def test_shared_budget_stops_recovery(limit, value):
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"bad":0}'), response('{"answer":42}')))
        try:
            await setup(engine)
            flow = replace(definition(max_attempts=2, max_feedback_loops=1, max_replans=1, on_failure="reassign", reassign_after=1), resource_limits={limit: value})
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.FAILED and not report.verified
            assert report.resource_usage[limit.removeprefix("max_")] <= value
            assert (await engine.selfloop.repository.session(report.nodes[0].id))["state"] == "failed"
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [True, False])
def test_cancellation_and_overall_timeout_propagate(cancel):
    class BlockingMock:
        is_simulated = True
        def __init__(self):
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()
        async def infer(self, request):
            self.started.set()
            try:
                await asyncio.sleep(5)
            finally:
                self.stopped.set()
    async def scenario():
        provider = BlockingMock()
        engine = safety_engine(provider)
        try:
            await setup(engine)
            flow = definition(timeout_seconds=.03 if not cancel else 1)
            await engine.register(flow)
            initial = await engine.submit(flow.ref, "Mock cancellation input")
            await provider.started.wait()
            report = await engine.cancel(initial.run.id) if cancel else await engine.wait(initial.run.id)
            assert provider.stopped.is_set()
            assert report.run.state == (WorkflowState.CANCELLED if cancel else WorkflowState.FAILED)
            attempts = await engine.selfloop.repository.attempts(report.nodes[0].id)
            assert attempts[0]["state"] in {"failed", "cancelled"}
            assert (await engine.runtime.capacity("analyst")).active == 0
            assert not report.verified and report.output is None
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_stale_feedback_and_artifact_promotion_rejected():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"bad":0}'), response('{"answer":42}')))
        try:
            await setup(engine)
            report, attempts, results, feedback = await finish(engine, definition(max_attempts=2, max_feedback_loops=1, on_failure="prompt_retry"))
            repo = engine.selfloop.repository
            with pytest.raises(RuntimeFault) as stale:
                await repo.apply_feedback(feedback[0]["record"]["id"], attempts[0]["id"], VersionRef(**attempts[0]["artifact"]["ref"]))
            assert stale.value.code == "stale_feedback"
            with pytest.raises(RuntimeFault):
                await repo.publish(report.nodes[0].id, attempts[0]["id"], results[0]["record"]["id"])
            with pytest.raises(RuntimeFault):
                await engine.selfloop.verifier.register(VerificationPolicy(REF, "changed criteria", (CHECK,)))
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_explicit_fallback_branch_and_normal_path_skipped():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"wrong":0}'), response('{"answer":42}')))
        try:
            await setup(engine)
            first = definition(max_attempts=1, max_feedback_loops=1, max_replans=1, on_failure="fallback", fallback_node="backup").nodes[0]
            backup = replace(definition().nodes[0], id="backup")
            flow = workflow([first, backup, task("normal")], [("work", "backup", "fallback"), ("work", "normal")])
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.SUCCEEDED
            assert report.nodes[0].state == TaskState.FAILED and report.nodes[0].output is None
            assert report.nodes[1].verified and report.nodes[2].state == TaskState.SKIPPED
            assert report.output == {"backup": {"answer": 42}}
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_tool_switch_uses_whitelist_subset_and_shared_calls():
    async def scenario():
        provider = ScriptedMockProvider(response('{"bad":0}'), response("", (tool_call(),)), response('{"answer":42}'))
        engine = safety_engine(provider)
        try:
            await setup(engine, agents=[agent(tool_refs=(VersionRef("calculator", "1"),))])
            flow = definition(max_attempts=2, max_feedback_loops=1, max_replans=1, on_failure="tool_switch",
                              tool_options=[[{"id": "calculator", "version": "1"}]])
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.verified and report.resource_usage["tool_calls"] == 1 and report.resource_usage["calls"] == 4
            assert attempts[1]["tools"] == [{"id": "calculator", "version": "1"}]
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_tool_switch_cannot_escalate_permissions():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"bad":0}')))
        try:
            await setup(engine)
            flow = definition(max_attempts=2, max_feedback_loops=1, max_replans=1, on_failure="tool_switch",
                              tool_options=[[{"id": "calculator", "version": "1"}]])
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.run.state == WorkflowState.FAILED
            assert attempts[1]["artifact"]["execution_error"] == "scheduling_no_candidate"
            assert report.resource_usage["model_calls"] == 1
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_concurrent_roots_isolate_feedback_and_artifacts():
    class IndependentMock:
        is_simulated = True
        async def infer(self, request):
            text = request.messages[-1]["content"]
            await asyncio.sleep(.005)
            return response('{"answer":42}' if "recovery" in text else '{"bad":0}')
    async def scenario():
        engine = safety_engine(IndependentMock())
        try:
            await setup(engine, agents=[agent(max_concurrency=4)])
            flow = definition(max_attempts=2, max_feedback_loops=1, on_failure="prompt_retry")
            await engine.register(flow)
            reports = await asyncio.gather(*(engine.run(flow.ref, {"input": n}) for n in range(4)))
            all_ids = []
            for report in reports:
                assert report.verified and report.resource_usage["calls"] == 2
                attempts = await engine.selfloop.repository.attempts(report.nodes[0].id)
                assert len(attempts) == 2 and all(a["run_id"] == report.run.id for a in attempts)
                assert all(a["resources"]["model_calls"] == 1 and a["resources"]["checks"] == 1 for a in attempts)
                all_ids.extend(a["id"] for a in attempts)
            assert len(set(all_ids)) == 8
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_sqlite_records_survive_reopen(tmp_path):
    async def scenario():
        path = tmp_path / "safety.sqlite3"
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')), path)
        await setup(engine)
        report, attempts, results, feedback = await finish(engine)
        await close(engine)
        reopened = safety_engine(path=path)
        try:
            assert await reopened.selfloop.repository.attempts(report.nodes[0].id) == attempts
            assert await reopened.selfloop.repository.results(report.nodes[0].id) == results
            loaded = await reopened.get(report.run.id)
            assert loaded.verified and loaded.nodes[0].attempt_ids == (attempts[0]["id"],)
        finally:
            await close(reopened)
    asyncio.run(scenario())


def test_shared_budget_propagates_through_subworkflows():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')))
        try:
            await setup(engine)
            child = replace(definition(), ref=VersionRef("child", "1"))
            await engine.register(child)
            flow = workflow([WorkflowNode("first", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child", "version": "1"}}),
                             WorkflowNode("second", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child", "version": "1"}})],
                            [("first", "second")], resource_limits={"max_calls": 1})
            await engine.register(flow)
            report = await engine.run(flow.ref, "Explicit Mock nested budget test")
            assert report.run.state == WorkflowState.FAILED and report.resource_usage["calls"] == 1
            second = await engine.get(report.nodes[1].child_run_ids[0])
            attempts = await engine.selfloop.repository.attempts(second.nodes[0].id)
            assert attempts[0]["artifact"]["execution_error"] == "shared_call_budget"
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_tool_failure_evidence_does_not_penalize_agent_quality():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response("", (tool_call(arguments={"operation": "divide", "a": 2, "b": 0}),))))
        try:
            await setup(engine, agents=[agent(tool_refs=(VersionRef("calculator", "1"),))])
            report, attempts, results, feedback = await finish(engine)
            assert report.run.state == WorkflowState.FAILED and feedback[0]["category"] == "TOOL_EXECUTION_ERROR"
            assert attempts[0]["artifact"]["execution_error"] == "tool_execution_failed"
            assert report.resource_usage["calls"] == 2 and not await engine.scheduler.repository.history(VersionRef("analyst", "1"))
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_duplicate_feedback_application_rejected_while_session_still_running():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"bad":0}'), response('{"answer":42}')))
        try:
            await setup(engine)
            original = engine.selfloop.repository.apply_feedback
            async def double_apply(*args):
                await original(*args)
                with pytest.raises(RuntimeFault) as duplicate:
                    await original(*args)
                assert duplicate.value.code == "safety_record_conflict"
            engine.selfloop.repository.apply_feedback = double_apply
            report, attempts, results, feedback = await finish(engine, definition(max_attempts=2, max_feedback_loops=1, on_failure="prompt_retry"))
            assert report.verified and feedback[0]["actual_budget_cost"] == {"feedback_loops": 1, "replans": 0}
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_cancel_during_verification_propagates_to_external_callback():
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()
        async def external(artifact):
            started.set()
            try:
                await asyncio.sleep(3)
            finally:
                stopped.set()
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')), external={"safe": external})
        try:
            await setup(engine, checks=(VerificationCheck("external_tests", {"test_id": "safe"}),))
            flow = definition()
            await engine.register(flow)
            initial = await engine.submit(flow.ref, "Mock verifier cancellation")
            await asyncio.wait_for(started.wait(), 1)
            report = await engine.cancel(initial.run.id)
            assert stopped.is_set() and report.run.state == WorkflowState.CANCELLED and not report.verified
            assert not await engine.selfloop.repository.results(report.nodes[0].id)
            assert (await engine.selfloop.repository.session(report.nodes[0].id))["accepted_artifact"] is None
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_restart_marks_paused_run_and_pending_intervention_interrupted(tmp_path):
    import sqlite3
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"bad":0}')))
        await setup(engine)
        flow = definition(max_attempts=2, max_feedback_loops=1, on_failure="human")
        await engine.register(flow)
        initial = await engine.submit(flow.ref, "Explicit Mock interrupted run")
        for _ in range(200):
            pending = await engine.selfloop.repository.tickets()
            if pending:
                break
            await asyncio.sleep(.001)
        path = tmp_path / "crash-snapshot.sqlite3"
        with sqlite3.connect(path) as target:
            engine.repository.db.backup(target)
        await close(engine)
        restarted = safety_engine(path=path)
        try:
            report = await restarted.get(initial.run.id)
            assert report.run.state == WorkflowState.FAILED and report.error_code == "workflow_interrupted"
            assert (await restarted.selfloop.repository.tickets())[0]["state"] == "expired"
            assert (await restarted.selfloop.repository.session(report.nodes[0].id))["error"] == "workflow_interrupted"
        finally:
            await close(restarted)
    asyncio.run(scenario())


@pytest.mark.parametrize("content", ['NaN', '{"answer":Infinity}', '{"answer":true}', 'not-json'])
def test_invalid_json_or_schema_never_published(content):
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response(content)))
        try:
            await setup(engine)
            report, attempts, results, feedback = await finish(engine)
            assert report.run.state == WorkflowState.FAILED and not report.verified
            assert results[0]["record"]["verdict"] == "fail"
            assert (await engine.selfloop.repository.session(report.nodes[0].id))["accepted_artifact"] is None
        finally:
            await close(engine)
    asyncio.run(scenario())


@pytest.mark.parametrize("error", ["model_timeout", "tool_execution_failed", "provider_unavailable"])
def test_environment_and_tool_faults_retry_original_agent_without_replan(error):
    class FaultMock:
        is_simulated = True
        async def infer(self, request):
            if "recovery" not in request.messages[-1]["content"]:
                raise RuntimeFault(error, "Explicit Mock fault")
            return response('{"answer":42}')
    async def scenario():
        engine = safety_engine(FaultMock())
        try:
            await setup(engine, agents=[agent("a"), agent("b")])
            original = definition(max_attempts=2, max_feedback_loops=1, max_replans=0, reassign_after=1, on_failure="reassign")
            config = {**original.nodes[0].config, "candidate_agents": ["a", "b"]}
            del config["agent_id"]
            flow = workflow([WorkflowNode("work", NodeKind.TASK, config)], scheduling_strategy="round_robin")
            report, attempts, results, feedback = await finish(engine, flow)
            assert report.verified and [a["agent_ref"]["id"] for a in attempts] == ["a", "a"]
            assert feedback[0]["repair_action"] == "prompt_retry" and report.resource_usage["replans"] == 0
            assert report.resource_usage["unknown_usage_calls"] == 2
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_tampered_artifact_digest_is_error_and_cannot_be_promoted():
    from futurecup.domain.verification import AttemptRecord
    async def scenario():
        engine = safety_engine()
        try:
            await setup(engine)
            repo, verifier = engine.selfloop.repository, engine.selfloop.verifier
            policy = await repo.policy(REF)
            await repo.create_session("node", {"run_id": "run"})
            artifact = ArtifactVersion(VersionRef("artifact", "1"), "tampered", {"answer": 42}, True)
            attempt = AttemptRecord("attempt", "run", "node", 1, VersionRef("input", "1"), "input-digest", "input",
                                    REF, verifier.backend.digest(policy), artifact=artifact)
            await repo.begin_attempt(attempt)
            result = await verifier.verify(policy, artifact, run_id="run", node_id="node", attempt_id="attempt", execution_id="attempt", budget=ResourceBudget())
            assert result.record.verdict.value == "error" and result.record.reason_codes == ("artifact_digest_mismatch",)
            with pytest.raises(RuntimeFault):
                await verifier.verify(policy, artifact, run_id="run", node_id="node", attempt_id="attempt", execution_id="attempt", budget=ResourceBudget())
            completed_attempt = replace(attempt, state="completed", revision=1, verification_id=result.record.id)
            await repo.save_attempt(completed_attempt, 0)
            with pytest.raises(RuntimeFault):
                await repo.publish("node", "attempt", result.record.id)
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_no_candidate_does_not_claim_model_execution_or_simulated_generation():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider())
        try:
            await setup(engine)
            config = {**definition().nodes[0].config, "candidate_agents": ["missing"]}
            del config["agent_id"]
            report, attempts, results, feedback = await finish(engine, workflow([WorkflowNode("work", NodeKind.TASK, config)]))
            assert report.run.state == WorkflowState.FAILED and report.simulated is None
            assert attempts[0]["execution_id"] is None and attempts[0]["provider"] is None
            assert results[0]["simulated_generation"] is None and report.resource_usage["calls"] == 0
            assert feedback[0]["category"] == "RESOURCE_UNAVAILABLE"
        finally:
            await close(engine)
    asyncio.run(scenario())


def test_all_configured_checks_are_required_and_evidence_retained():
    async def scenario():
        engine = safety_engine(ScriptedMockProvider(response('{"answer":42}')))
        try:
            await setup(engine, checks=(CHECK, VerificationCheck("assertion", {"path": "answer", "op": "eq", "value": 43}),
                                       VerificationCheck("numeric", {"path": "answer", "expected": 42})))
            report, attempts, results, feedback = await finish(engine)
            assert not report.verified and results[0]["record"]["verdict"] == "fail"
            assert [v["status"] for v in results[0]["evidence"]] == ["PASSED", "FAILED", "PASSED"]
            assert results[0]["checks_used"] == attempts[0]["resources"]["checks"] == 3
        finally:
            await close(engine)
    asyncio.run(scenario())
