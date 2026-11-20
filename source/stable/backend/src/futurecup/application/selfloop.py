"""Bounded node recovery, delegating all candidate selection to the existing scheduler."""

import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from typing import Awaitable, Callable
from uuid import uuid4

from futurecup.application.feedback import FeedbackEngine
from futurecup.application.runtime import AgentRuntimeService
from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.application.verification import VerifierEngine
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import ExecutionState, JsonValue, RunLimits, TaskState, VerificationVerdict, VersionRef
from futurecup.domain.scheduling import SchedulingTask
from futurecup.domain.verification import ArtifactVersion, AttemptRecord, RecoveryLimits, ResourceBudget


def reject_constant(value: str):
    raise ValueError("Non-finite JSON number")


def check_cancellation() -> None:
    # A completed wait_for future can race with outer cancellation on Python
    # 3.11. Do not turn a still-cancelling attempt into verification/completion.
    current = asyncio.current_task()
    if current is not None and current.cancelling():
        raise asyncio.CancelledError


def recovery_from(value: dict) -> RecoveryLimits:
    if not isinstance(value, dict):
        raise ValueError("Recovery must be an object")
    value = dict(value)
    if "tool_options" in value:
        value["tool_options"] = tuple(tuple(VersionRef(**ref) for ref in option) for option in value["tool_options"])
    return RecoveryLimits(**value)


class SelfLoopEngine:
    feedback_context_version = 2

    def __init__(self, runtime: AgentRuntimeService, scheduler: SchedulingCoordinator | None,
                 verifier: VerifierEngine) -> None:
        self.runtime, self.scheduler, self.verifier = runtime, scheduler, verifier
        self.repository, self.backend = verifier.repository, verifier.backend
        self.feedback = FeedbackEngine()

    async def run(self, task: SchedulingTask, value: JsonValue, mode: str, limits: RunLimits,
                  policy_ref: VersionRef, recovery: RecoveryLimits, budget: ResourceBudget,
                  notify: Callable[[dict], Awaitable[None]]) -> tuple[JsonValue, bool]:
        if self.runtime.real_safety:
            ceiling = self.runtime.real_safety.max_feedback_loops
            recovery = replace(recovery, max_feedback_loops=min(recovery.max_feedback_loops, ceiling),
                               max_attempts=min(recovery.max_attempts, ceiling + 1))
            budget = budget.fork()
            budget.task_model_call_ceiling = self.runtime.real_safety.max_task_model_calls
        policy = await self.repository.policy(policy_ref)
        node_id = task.task_id
        await self.repository.create_session(node_id, {"run_id": task.workflow_run_id, "node_key": task.node_id,
                                                     "policy_ref": asdict(policy_ref)})
        attempts, verifications, feedbacks = [], [], []
        current: AttemptRecord | None = None
        execution_id = None
        error, final = None, "failed"
        used_loops = used_replans = 0
        excluded: set[str] = set()
        pinned: VersionRef | None = None
        next_input = value
        tools = None
        started_at = datetime.now(timezone.utc)
        async def update(**changes):
            nonlocal current
            old_revision = current.revision
            current = replace(current, revision=old_revision + 1, **changes)
            await self.repository.save_attempt(current, old_revision)
        async def status(state, **extra):
            await notify({"state": state, "attempt_ids": tuple(attempts), "verification_ids": tuple(verifications),
                          "feedback_ids": tuple(feedbacks), "started_at": started_at, **extra})
        try:
            async with asyncio.timeout(recovery.timeout_seconds):
                for ordinal in range(1, recovery.max_attempts + 1):
                    attempt_budget = budget.fork()
                    attempt_budget.consume("attempts")
                    attempt_id = uuid4().hex
                    current = AttemptRecord(attempt_id, task.workflow_run_id, node_id, ordinal,
                        VersionRef(node_id + ":input", str(ordinal)), self.backend.digest(next_input), next_input,
                        policy.ref, self.backend.digest(asdict(policy)), started_at=datetime.now(timezone.utc), tools=tools)
                    await self.repository.begin_attempt(current)
                    attempts.append(attempt_id)
                    await status(TaskState.RECOVERING if ordinal > 1 else TaskState.RUNNING)
                    attempt_task = replace(task, task_id=attempt_id, ready_at=datetime.now(timezone.utc),
                        candidate_ids=(pinned.id,) if pinned else tuple(i for i in task.candidate_ids if i not in excluded),
                        candidate_refs=(pinned,) if pinned else tuple(r for r in task.candidate_refs if r.id not in excluded), tool_subset=tools)
                    text = next_input if isinstance(next_input, str) else json.dumps(next_input, ensure_ascii=False, allow_nan=False)
                    output, format_error, execution_error, simulated = None, False, None, None
                    execution_id = None
                    try:
                        if self.scheduler:
                            assignment = await self.scheduler.dispatch(attempt_task, text, limits, shared_budget=attempt_budget)
                            execution_id = assignment.execution_id
                            await update(state="running", agent_ref=assignment.agent_ref, execution_id=execution_id,
                                         decision_id=assignment.decision_id)
                            check_cancellation()
                            await status(TaskState.RUNNING, execution_id=execution_id, agent_ref=assignment.agent_ref,
                                         scheduling_decision_id=assignment.decision_id,
                                         assigned_at=assignment.assigned_at, waiting_ms=assignment.waiting_ms)
                        else:
                            if len(attempt_task.candidate_refs) != 1:
                                raise RuntimeFault("scheduler_required", "Dynamic recovery requires scheduler")
                            ref = attempt_task.candidate_refs[0]
                            initial = await self.runtime.submit(ref.id, text, limits, agent_version=ref.version,
                                task_id=attempt_id, shared_budget=attempt_budget, tool_subset=tools)
                            execution_id = initial.execution.id
                            await update(state="running", agent_ref=ref, execution_id=execution_id)
                        result = await self.runtime.wait(execution_id)
                        check_cancellation()
                        simulated = result.simulated
                        await update(provider=result.provider, model=result.model, simulated=simulated, model_calls=result.model_calls,retrieval_records=result.retrieval_records,
                            input_tokens=result.input_tokens, output_tokens=result.output_tokens, usage_complete=result.usage_complete,
                            resolved_model=result.resolved_model, provider_config_ref=result.provider_config_ref,
                            estimated_cost=result.estimated_cost, cost_currency=result.cost_currency, cost_complete=result.cost_complete)
                        history = await self.repository.attempts(node_id)
                        priced = all(a.get("cost_complete", False) for a in history)
                        currencies = {a.get("cost_currency") for a in history}
                        priced = priced and len(currencies) == 1 and None not in currencies
                        await status(TaskState.RUNNING, provider=result.provider, model=result.model, resolved_model=result.resolved_model,
                            model_calls=result.model_calls,
                            input_tokens=sum(a.get("input_tokens", 0) for a in history),
                            output_tokens=sum(a.get("output_tokens", 0) for a in history),
                            usage_complete=all(a.get("usage_complete") is True for a in history),
                            estimated_cost=sum(a.get("estimated_cost") or 0 for a in history) if priced else None,
                            cost_currency=next(iter(currencies)) if priced else None, cost_complete=priced)
                        if result.execution.state != ExecutionState.SUCCEEDED:
                            execution_error = result.execution.error_code or "agent_execution_failed"
                            if (recovery.retry_truncated_output and execution_error=='provider_incomplete'
                                    and result.model_calls and result.model_calls[-1].finish_reason=='length'
                                    and result.incomplete_output is not None):
                                # A confirmed length cutoff is an unverified output-format failure,
                                # never a successful artifact or a generic network retry.
                                output=result.incomplete_output;format_error=True;execution_error=None
                        else:
                            # Execution reliability is separate from verified quality; no verifier blame.
                            if self.scheduler:
                                await self.scheduler.complete(attempt_id)
                            output = result.output
                            if mode == "json":
                                try:
                                    output = json.loads(result.output, parse_constant=reject_constant)
                                except (TypeError, ValueError, RecursionError):
                                    format_error = True
                            elif mode == "tool_result":
                                if not result.tool_calls or result.tool_calls[-1].state != ExecutionState.SUCCEEDED:
                                    format_error = True
                                else:
                                    output = result.tool_calls[-1].output
                    except RuntimeFault as fault:
                        execution_error = fault.code
                    artifact = ArtifactVersion(VersionRef(node_id + ":artifact", str(ordinal)),
                        self.backend.digest(output), output, simulated, format_error, execution_error)
                    await update(artifact=artifact)
                    await status(TaskState.VERIFYING, simulated=simulated)
                    verification = await self.verifier.verify(policy, artifact, run_id=task.workflow_run_id,
                        node_id=node_id, attempt_id=attempt_id, execution_id=execution_id or attempt_id, budget=attempt_budget)
                    check_cancellation()
                    verifications.append(verification.record.id)
                    await update(state="completed", verification_id=verification.record.id, error_code=execution_error,
                                 ended_at=datetime.now(timezone.utc), resources=attempt_budget.snapshot())
                    if verification.record.verdict == VerificationVerdict.PASS:
                        await self.repository.publish(node_id, attempt_id, verification.record.id)
                        await status(TaskState.RUNNING, verified=True, current_artifact_ref=artifact.ref)
                        final = "succeeded"
                        return output, simulated
                    action = self.feedback.choose_action(verification, recovery, ordinal)
                    # All retries, including manual decisions, consume the same finite budget.
                    exhausted = action != "abort" and (used_loops >= recovery.max_feedback_loops or (ordinal >= recovery.max_attempts and action != "fallback"))
                    if exhausted:
                        action = "abort"
                    feedback = self.feedback.create(verification, action)
                    await self.repository.save_feedback(feedback)
                    feedbacks.append(feedback.record.id)
                    await status(TaskState.RECOVERING)
                    if action == "human":
                        ticket_id = uuid4().hex
                        await self.repository.create_ticket({"id": ticket_id, "node_id": node_id, "attempt_id": attempt_id,
                            "artifact_ref": asdict(artifact.ref), "state": "pending", "revision": 0, "actor": None})
                        await status(TaskState.WAITING_HUMAN)
                        while (ticket := await self.repository.ticket(ticket_id))["state"] == "pending":
                            await asyncio.sleep(.02)
                        if ticket["state"] != "retry":
                            action = "abort"
                        else:
                            action = "prompt_retry"
                    if action == "abort":
                        error = "recovery_limit" if exhausted else "verification_" + verification.record.verdict.value
                        raise RuntimeFault(error, "Verified execution did not pass")
                    await self.repository.apply_feedback(feedback.record.id, attempt_id, artifact.ref)
                    before_feedback = budget.snapshot()
                    try:
                        budget.consume("feedback_loops")
                        used_loops += 1
                        if action in {"reassign", "tool_switch", "fallback"}:
                            if used_replans >= recovery.max_replans:
                                raise RuntimeFault("replan_limit", "Recovery replan limit exhausted")
                            budget.consume("replans")
                            used_replans += 1
                    finally:
                        await self.repository.record_feedback_usage(feedback.record.id,
                            {key: budget.snapshot()[key] - before_feedback[key] for key in ("feedback_loops", "replans")})
                    if action in {"reassign", "tool_switch", "fallback"}:
                        if action == "reassign":
                            pinned = None
                            if current.agent_ref:
                                excluded.add(current.agent_ref.id)
                        elif action == "tool_switch":
                            if used_replans > len(recovery.tool_options):
                                raise RuntimeFault("tool_options_exhausted", "No approved tool subset remains")
                            tools = recovery.tool_options[used_replans-1]
                        else:
                            await status(TaskState.RECOVERING, fallback_activated=True)
                            raise RuntimeFault("recovery_fallback", "Explicit fallback path requested")
                    if action in {"prompt_retry", "tool_switch"}:
                        pinned = current.agent_ref
                    next_input = {"original_input": value, "recovery": {"attempt": ordinal + 1,
                        "category": feedback.category.value, "reason_codes": list(verification.record.reason_codes),
                        "instruction": "Repair the result to satisfy the unchanged verification policy.",
                        "policy_ref": asdict(policy.ref), "checks": [asdict(c) for c in policy.checks],
                        "previous_attempt_id": attempt_id,
                        "previous_artifact": {"ref": asdict(artifact.ref), "digest": artifact.digest,
                            "content": artifact.content, "format_error": artifact.format_error,
                            "execution_error": artifact.execution_error},
                        "verification": self.feedback.verification_context(verification, recovery.feedback_mode),
                        "feedback": {"id": feedback.record.id, "action": feedback.repair_action,
                            "category": feedback.category.value}}}
                    if recovery.feedback_mode == "errors_only":
                        summary = next_input["recovery"]
                        summary.pop("checks")  # Original task retains all constraints; policy_ref anchors the audit.
                        summary["reason_codes"] = list(dict.fromkeys(
                            e["reason"] for e in summary["verification"]["evidence"]))
                raise RuntimeFault("recovery_limit", "Recovery attempt limit exhausted")
        except asyncio.CancelledError:
            final, error = "cancelled", "cancelled"
            raise
        except TimeoutError:
            error = "selfloop_timeout"
            raise RuntimeFault(error, "Overall recovery timeout exceeded") from None
        except RuntimeFault as fault:
            error = fault.code
            raise
        except Exception:
            error = "selfloop_internal"
            raise RuntimeFault(error, "Recovery engine failed; see structured audit records") from None
        finally:
            if execution_id:
                result = await self.runtime.get(execution_id)
                if result.execution.state not in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.TIMED_OUT, ExecutionState.CANCELLED}:
                    await self.runtime.cancel(execution_id)
            if current and current.state not in {"completed", "failed", "cancelled"}:
                await update(state="cancelled" if final == "cancelled" else "failed", error_code=error or "recovery_internal",
                             ended_at=datetime.now(timezone.utc), resources=attempt_budget.snapshot())
            await self.repository.finish_session(node_id, final, error, budget.snapshot())
