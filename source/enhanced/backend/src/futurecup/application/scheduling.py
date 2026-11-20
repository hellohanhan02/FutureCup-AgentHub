"""CAAS and fair baselines over the same constraints and atomic Runtime admission."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from futurecup.application.ports import AgentSelectionPolicy, SchedulingRepository
from futurecup.application.runtime import AgentRuntimeService
from futurecup.application.adaptive import adapt, constrain, classify
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.verification import ResourceBudget
from futurecup.domain.models import ExecutionState, RunLimits, RunReport, VersionRef
from futurecup.domain.scheduling import (
    CAASConfig, AdaptiveConfig, CandidateAssessment, CapabilityObservation, CapabilityProfile,
    SchedulingAssignment, SchedulingDecision, SchedulingTask,
)


def effective_priority(task: SchedulingTask, now: datetime, config: CAASConfig) -> float:
    waiting = max(0, (now - task.ready_at).total_seconds())
    return task.priority + min(64, max(0, task.dependent_count)) * .1 + waiting * config.aging_points_per_second


def assess(task: SchedulingTask, profile: CapabilityProfile, config: CAASConfig) -> CandidateAssessment:
    reasons = []
    if not task.dependencies_satisfied:
        reasons.append("dependencies_not_ready")
    if not profile.enabled:
        reasons.append("agent_disabled")
    if not profile.available:
        reasons.append("agent_unavailable")
    if not profile.provider_available:
        reasons.append("provider_unavailable")
    if not profile.tools_available:
        reasons.append("tools_unavailable")
    if task.task_type not in profile.task_types and "*" not in profile.task_types:
        reasons.append("task_type_mismatch")
    if not set(task.required_skills).issubset(profile.skills):
        reasons.append("skills_mismatch")
    if (not set(task.required_tools).issubset(profile.tools) or
            (task.tool_subset is not None and (not set(task.tool_subset).issubset(profile.tools)
             or not set(task.required_tools).issubset(task.tool_subset)))):
        reasons.append("tool_permission_mismatch")
    capacity = profile.capacity
    if capacity.active >= capacity.capacity:
        reasons.append("agent_capacity")
    if capacity.runtime_active >= capacity.runtime_capacity:
        reasons.append("runtime_capacity")
    if reasons:
        return CandidateAssessment(profile.agent_ref.id, profile.agent_ref.version, False, tuple(reasons), {}, None, profile)
    preferred = len(set(task.preferred_skills) & set(profile.skills)) / len(set(task.preferred_skills)) if task.preferred_skills else .5
    match = .5 * (1 if task.task_type in profile.task_types else .5) + .5 * preferred
    stats = profile.stats
    count = stats.sample_count
    reliability = (stats.successes + config.prior_samples * .5) / (count + config.prior_samples)
    latency = profile.declared_latency_ms
    if count and stats.mean_duration_ms is not None:
        confidence = count / (count + config.prior_samples)
        latency = confidence * stats.mean_duration_ms + (1 - confidence) * (latency if latency is not None else config.latency_scale_ms)
    components = {
        "match": match, "reliability": reliability,
        "load": max(0, 1 - capacity.active / capacity.capacity),
        "cost": 1 / (1 + profile.estimated_cost / config.cost_scale) if profile.estimated_cost is not None else .5,
        "latency": 1 / (1 + latency / config.latency_scale_ms) if latency is not None else .5,
    }
    score = sum(components[name] * getattr(config, name + "_weight") for name in components)
    return CandidateAssessment(profile.agent_ref.id, profile.agent_ref.version, True, (), components, score, profile)


def evaluate_candidate(task: SchedulingTask, profile: CapabilityProfile, config: CAASConfig,
                       history: tuple[dict, ...] = (), options: AdaptiveConfig | None = None) -> CandidateAssessment:
    candidate = constrain(task, assess(task, profile, config))
    return adapt(task, candidate, config, history, options or AdaptiveConfig()) if task.strategy == 'adaptive_caas' else candidate


class FixedSelection:
    def select(self, candidates: tuple[CandidateAssessment, ...]) -> str:
        return min(candidate.agent_id for candidate in candidates)


class RoundRobinSelection:
    def __init__(self) -> None:
        self.last: str | None = None
    def select(self, candidates: tuple[CandidateAssessment, ...]) -> str:
        ids = sorted(candidate.agent_id for candidate in candidates)
        selected = next((key for key in ids if self.last is None or key > self.last), ids[0])
        self.last = selected
        return selected


class CAASSelection:
    def select(self, candidates: tuple[CandidateAssessment, ...]) -> str:
        return min(candidates, key=lambda item: (-item.score, item.agent_id, item.agent_version)).agent_id


@dataclass
class _Pending:
    task: SchedulingTask
    input: str
    limits: RunLimits
    future: asyncio.Future[SchedulingAssignment]
    signature: tuple | None = None
    shared_budget: ResourceBudget | None = None
    replay_scope: str | None = None


class SchedulingCoordinator:
    def __init__(self, runtime: AgentRuntimeService, repository: SchedulingRepository,
                 config: CAASConfig | None = None, history_source: str = "auto", adaptive_config: AdaptiveConfig | None = None) -> None:
        if history_source not in {"auto", "simulation"}:
            raise ValueError("Unknown scheduling history source")
        self.runtime, self.repository = runtime, repository
        self.config = config or CAASConfig()
        self.adaptive_config = adaptive_config or AdaptiveConfig()
        self.history_source = history_source
        self.policies: dict[str, AgentSelectionPolicy] = {
            "fixed": FixedSelection(), "round_robin": RoundRobinSelection(), "caas": CAASSelection(), "adaptive_caas": CAASSelection(),
        }
        self._pending: dict[str, _Pending] = {}
        self._assignments: dict[str, SchedulingAssignment] = {}
        self._tasks: dict[str, SchedulingTask] = {}
        self._inputs: dict[str, tuple[str, RunLimits, str | None]] = {}
        self._pump_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.planning_ms = 0.0
        self.planning_passes = 0

    async def profile(self, ref: VersionRef, task_type: str) -> CapabilityProfile:
        current = await self.runtime.registry.get_current(ref.id)
        agent = await self.runtime.registry.get(ref)
        capacity = await self.runtime.capacity(ref.id)
        capacity = replace(capacity, capacity=min(capacity.capacity, agent.max_concurrency))
        provider = self.runtime.providers.get(agent.provider)
        source = self.history_source if self.history_source != "auto" else (
            "observed_mock" if provider is not None and provider.is_simulated else "observed_real")
        stats = await self.repository.stats(ref, task_type, source)
        tools_available = True
        try:
            await self.runtime.tools.definitions(agent.tool_refs)
        except RuntimeFault:
            tools_available = False
        return CapabilityProfile(ref, agent.task_types, agent.skills, agent.tool_refs,
            current.enabled, current.available, provider is not None, tools_available, capacity,
            agent.estimated_cost, agent.estimated_latency_ms, stats,
            ref.version + ":" + source + ":" + str(stats.revision) + ":" + str(capacity.revision)
            + ":" + str(current.enabled) + ":" + str(current.available) + ":" + current.ref.version
            + ":" + str(tools_available) + ":" + str(provider is not None),
            datetime.now(timezone.utc))

    async def candidates(self, task: SchedulingTask) -> tuple[CandidateAssessment, ...]:
        refs = {ref.id: ref for ref in task.candidate_refs}
        result = []
        for agent_id in sorted(set(task.candidate_ids)):
            if agent_id not in refs:
                result.append(CandidateAssessment(agent_id, None, False, ("agent_not_registered_at_submission",), {}, None, None))
                continue
            try:
                profile = await self.profile(refs[agent_id], task.task_type)
                history = await self.repository.adaptive_history(profile.agent_ref, task.task_type, profile.stats.source) if task.strategy == 'adaptive_caas' else ()
                result.append(evaluate_candidate(task, profile, self.config, history, self.adaptive_config))
            except RuntimeFault as fault:
                if fault.code != "agent_not_found":
                    raise
                result.append(CandidateAssessment(agent_id, refs[agent_id].version, False, ("agent_not_found",), {}, None, None))
        return tuple(result)

    async def dispatch(self, task: SchedulingTask, task_input: str, limits: RunLimits, *, shared_budget: ResourceBudget | None = None, replay_scope: str | None = None) -> SchedulingAssignment:
        if (task.strategy not in self.policies or task.no_candidate_policy not in {"fail", "wait"}
                or not 0 < task.timeout_seconds <= 300 or task.ready_at.tzinfo is None
                or type(task.priority) is not int or not 0 <= task.priority <= 100):
            raise RuntimeFault("scheduling_request_invalid", "Invalid scheduling request", 422)
        async with self._lock:
            if self._closed:
                raise RuntimeFault("scheduler_closed", "Scheduler is closed", 503)
            if task.task_id in self._tasks and (self._tasks[task.task_id] != task or self._inputs[task.task_id] != (task_input, limits, replay_scope)):
                raise RuntimeFault("task_identity_conflict", "Task ID cannot be reused for a different request", 409)
            if task.task_id in self._assignments:
                return self._assignments[task.task_id]
            pending = self._pending.get(task.task_id)
            if pending is None:
                pending = _Pending(task, task_input, limits, asyncio.get_running_loop().create_future(), shared_budget=shared_budget, replay_scope=replay_scope)
                self._pending[task.task_id] = pending
                self._tasks[task.task_id] = task
                self._inputs[task.task_id] = (task_input, limits, replay_scope)
            if self._pump_task is None or self._pump_task.done():
                self._pump_task = asyncio.create_task(self._pump())
        try:
            remaining = max(0, task.timeout_seconds - (datetime.now(timezone.utc) - task.ready_at).total_seconds())
            return await asyncio.wait_for(asyncio.shield(pending.future), remaining)
        except TimeoutError:
            async with self._lock:
                if task.task_id in self._assignments:
                    return self._assignments[task.task_id]
                await self._abort(pending, "scheduling_timeout")
            raise RuntimeFault("scheduling_timeout", "No assignment before scheduling deadline", 504) from None
        except asyncio.CancelledError:
            async with self._lock:
                assignment = self._assignments.get(task.task_id)
                if assignment:
                    await self.runtime.cancel(assignment.execution_id)
                    await self.complete(task.task_id)
                elif task.task_id in self._pending:
                    await self._abort(pending, "scheduling_cancelled")
            raise

    async def _record(self, pending: _Pending, assessments: tuple[CandidateAssessment, ...], selected: VersionRef | None,
                      outcome: str, started: float) -> SchedulingDecision:
        now = datetime.now(timezone.utc)
        task = pending.task
        decision = SchedulingDecision(uuid4().hex, task.task_id, task.workflow_run_id, task.node_id,
            task.strategy, self.adaptive_config.revision if task.strategy == 'adaptive_caas' else self.config.revision, task.priority, effective_priority(task, now, self.config),
            max(0, (now - task.ready_at).total_seconds() * 1000), assessments, selected, outcome,
            (perf_counter() - started) * 1000, now, self.config, requirements={"task_type":task.task_type,"required_skills":task.required_skills,"preferred_skills":task.preferred_skills,"required_tools":[asdict(t) for t in task.required_tools]})
        await self.repository.record(decision)
        self.planning_ms += (perf_counter() - started) * 1000
        self.planning_passes += 1
        return decision

    async def _abort(self, pending: _Pending, code: str) -> None:
        if self._pending.pop(pending.task.task_id, None) is None:
            return
        started = perf_counter()
        await self._record(pending, await self.candidates(pending.task), None, code, started)
        if not pending.future.done():
            pending.future.cancel()

    async def _pump(self) -> None:
        try:
            while self._pending:
                async with self._lock:
                    now = datetime.now(timezone.utc)
                    ordered = sorted(self._pending.values(), key=lambda item: (
                        -effective_priority(item.task, now, self.config), item.task.ready_at, item.task.task_id))
                    for pending in ordered:
                        started = perf_counter()
                        task = pending.task
                        if (datetime.now(timezone.utc) - task.ready_at).total_seconds() >= task.timeout_seconds:
                            await self._record(pending, await self.candidates(task), None, "scheduling_timeout", started)
                            self._pending.pop(task.task_id, None)
                            pending.future.set_exception(RuntimeFault("scheduling_timeout", "No assignment before scheduling deadline", 504))
                            continue
                        assessments = await self.candidates(task)
                        eligible = tuple(item for item in assessments if item.eligible)
                        if eligible:
                            selected_id = self.policies[task.strategy].select(eligible)
                            selected = next(item.profile.agent_ref for item in eligible if item.agent_id == selected_id)
                            try:
                                report = await self.runtime.submit(selected.id, pending.input, pending.limits,
                                    agent_version=selected.version, task_id=task.task_id,
                                    shared_budget=pending.shared_budget, tool_subset=task.tool_subset, replay_scope=pending.replay_scope)
                            except RuntimeFault as fault:
                                if fault.code in {"agent_busy", "agent_unavailable", "agent_disabled"}:
                                    await self._record(pending, assessments, None, "claim_changed:" + fault.code, started)
                                    continue
                                await self._record(pending, assessments, None, fault.code, started)
                                self._pending.pop(task.task_id, None)
                                pending.future.set_exception(fault)
                                continue
                            try:
                                decision = await self._record(pending, assessments, selected, "claimed", started)
                            except Exception:
                                await self.runtime.cancel(report.execution.id)
                                raise
                            assignment = SchedulingAssignment(task.task_id, report.execution.id, selected, decision.id,
                                                              decision.created_at, decision.waiting_ms)
                            self._assignments[task.task_id] = assignment
                            self._pending.pop(task.task_id, None)
                            pending.future.set_result(assignment)
                            continue
                        busy_only = any(item.reasons and set(item.reasons).issubset({"agent_capacity", "runtime_capacity"} | ({"adaptive_cooldown"} if task.strategy == "adaptive_caas" else set())) for item in assessments)
                        outcome = "waiting_capacity" if busy_only else "no_candidate"
                        signature = tuple((item.agent_id, item.reasons, item.profile.profile_version if item.profile else None) for item in assessments)
                        if pending.signature != signature:
                            await self._record(pending, assessments, None, outcome, started)
                            pending.signature = signature
                        else:
                            self.planning_ms += (perf_counter() - started) * 1000
                            self.planning_passes += 1
                        if not busy_only and task.no_candidate_policy == "fail":
                            self._pending.pop(task.task_id, None)
                            pending.future.set_exception(RuntimeFault("scheduling_no_candidate", "No Agent satisfies the candidate constraints", 409))
                if self._pending:
                    await asyncio.sleep(.005)
        except asyncio.CancelledError:
            raise
        except Exception:
            for pending in tuple(self._pending.values()):
                if not pending.future.done():
                    pending.future.set_exception(RuntimeFault("scheduling_internal", "Scheduling service failed", 500))
            self._pending.clear()

    async def complete(self, task_id: str, succeeded: bool | None = None) -> bool:
        assignment = self._assignments.get(task_id)
        if assignment is None:
            return False
        report = await self.runtime.get(assignment.execution_id)
        if report.execution.state not in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.TIMED_OUT, ExecutionState.CANCELLED}:
            return False
        return await self.observe(report, self._tasks[task_id].task_type, succeeded)

    async def observe(self, report: RunReport, task_type: str, succeeded: bool | None = None) -> bool:
        # Future feedback integration point; quality validation must use a separate channel.
        actual = await self.runtime.get(report.execution.id)
        if actual.execution.state not in {ExecutionState.SUCCEEDED, ExecutionState.FAILED, ExecutionState.TIMED_OUT, ExecutionState.CANCELLED}:
            raise RuntimeFault("observation_not_terminal", "Cannot learn from unfinished executions", 409)
        source = self.history_source if self.history_source != "auto" else ("observed_mock" if actual.simulated else "observed_real")
        success = actual.execution.state == ExecutionState.SUCCEEDED and succeeded is not False
        observation = CapabilityObservation(actual.execution.id, actual.execution.agent_ref, task_type, source,
            success, actual.execution.state == ExecutionState.CANCELLED, actual.elapsed_ms, datetime.now(timezone.utc),
            classify(actual.execution.state.value, actual.execution.error_code, success,
                actual.model_calls[-1].http_error_category if actual.model_calls else None))
        return await self.repository.observe(observation)

    async def close(self) -> None:
        self._closed = True
        if self._pump_task is not None and not self._pump_task.done():
            self._pump_task.cancel()
            with_cancel = self._pump_task
            try:
                await with_cancel
            except asyncio.CancelledError:
                pass
        for pending in tuple(self._pending.values()):
            await self._abort(pending, "scheduler_closed")
