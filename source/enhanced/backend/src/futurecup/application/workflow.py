"""Definition-driven DAG engine with bounded control nodes and durable snapshots."""

import asyncio
from contextlib import suppress
from dataclasses import replace, asdict
from datetime import datetime, timedelta, timezone
import json
from time import perf_counter
from uuid import uuid4

from futurecup.application.ports import WorkflowDataValidator, WorkflowDispatchPolicy, WorkflowRepository
from futurecup.application.runtime import AgentRuntimeService
from futurecup.application.scheduling import SchedulingCoordinator, effective_priority
from futurecup.application.diagnosis import stop_diagnosis
from futurecup.application.selfloop import SelfLoopEngine, recovery_from
from futurecup.domain.verification import ResourceBudget
from futurecup.application.workflow_validation import bounded_json, predicate, resolve, validate_definition
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import (
    ExecutionState, JsonValue, NodeKind, RecoveryPolicy, RunLimits, RuntimeEvent, TaskState,
    VersionRef, WorkflowDefinition, WorkflowNode, WorkflowNodeRun, WorkflowReport, WorkflowRun, WorkflowState,
)
from futurecup.domain.scheduling import SchedulingTask, TaskFeatures

NODE_TERMINAL = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.SKIPPED, TaskState.CANCELLED}
RUN_TERMINAL = {WorkflowState.SUCCEEDED, WorkflowState.FAILED, WorkflowState.CANCELLED}


class DefinitionOrderDispatch:
    """Capacity-only baseline. Eligibility is computed by the engine, not this policy."""
    def choose(self, ready_ids: tuple[str, ...], capacity: int) -> tuple[str, ...]:
        return ready_ids[:capacity]


class _Budget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0


class _Context:
    def __init__(self, definitions: dict[VersionRef, WorkflowDefinition], bindings: dict[str, VersionRef],
                 semaphore: asyncio.Semaphore, budgets: tuple[_Budget, ...], remaining_depth: int) -> None:
        self.definitions, self.bindings = definitions, bindings
        self.semaphore, self.budgets = semaphore, budgets
        self.remaining_depth = remaining_depth
        self.strategy = "caas"
        self.resources = ResourceBudget()
        self.precise = False
        self.replay_scopes = {}
        self.recovery_audit = {}
        self.prior_failures = {}
        self.deadline = None

    def consume(self) -> None:
        if any(budget.used >= budget.maximum for budget in self.budgets):
            raise RuntimeFault("workflow_node_budget", "Shared workflow node budget exhausted")
        for budget in self.budgets:
            budget.used += 1


def binding_key(ref: VersionRef, node_id: str) -> str:
    return ref.id + "@" + ref.version + "/" + node_id


class WorkflowEngine:
    def __init__(self, runtime: AgentRuntimeService, repository: WorkflowRepository,
                 validator: WorkflowDataValidator, dispatch: WorkflowDispatchPolicy | None = None,
                 scheduler: SchedulingCoordinator | None = None, selfloop: SelfLoopEngine | None = None) -> None:
        self.runtime, self.repository, self.validator = runtime, repository, validator
        self.dispatch = dispatch or DefinitionOrderDispatch()
        self.scheduler = scheduler
        self.selfloop = selfloop
        self._jobs: dict[str, asyncio.Task[None]] = {}
        self._roots: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._contexts: dict[str, _Context] = {}
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeFault("workflow_engine_closed", "Workflow engine is closed", 503)
        if self._started:
            return
        await self.repository.open()
        self._started = True
        for report in await self.repository.interrupted():
            await self._finish(report.run.id, WorkflowState.FAILED, "workflow_interrupted")
            if self.selfloop:
                for node in report.nodes:
                    try:
                        await self.selfloop.repository.finish_session(node.id, "failed", "workflow_interrupted", report.resource_usage)
                    except RuntimeFault as fault:
                        if fault.code != "safety_record_not_found":
                            raise

    async def register(self, definition: WorkflowDefinition) -> None:
        await self.start()
        self.runtime.check_sensitive(asdict(definition))
        validate_definition(definition, self.validator)
        for node in definition.nodes:
            if "verification" in node.config:
                if self.selfloop is None:
                    raise RuntimeFault("verification_unavailable", "Verification service is not configured", 409)
                await self.selfloop.repository.policy(VersionRef(**node.config["verification"]))
        await self.repository.register(definition)

    async def validate(self, definition: WorkflowDefinition) -> None:
        """Submission-equivalent preflight without persistence or execution."""
        await self.start()
        self.runtime.check_sensitive(asdict(definition))
        await self._prepare(definition)
        for node in definition.nodes:
            if "verification" in node.config:
                if self.selfloop is None:
                    raise RuntimeFault("verification_unavailable", "Verification service is not configured", 409)
                await self.selfloop.repository.policy(VersionRef(**node.config["verification"]))

    async def _prepare(self, definition: WorkflowDefinition) -> tuple[dict[VersionRef, WorkflowDefinition], dict[str, VersionRef]]:
        definitions: dict[VersionRef, WorkflowDefinition] = {}
        bindings: dict[str, VersionRef] = {}
        visited: set[tuple[VersionRef, int]] = set()

        async def visit(current: WorkflowDefinition, remaining: int, stack: tuple[VersionRef, ...]) -> None:
            if current.ref in stack:
                raise RuntimeFault("workflow_recursion", "Recursive workflow references are not allowed", 422)
            if remaining < 0:
                raise RuntimeFault("workflow_depth_exceeded", "Subworkflow depth limit exceeded", 422)
            remaining = min(remaining, current.max_subworkflow_depth)
            if (current.ref, remaining) in visited:
                return
            visited.add((current.ref, remaining))
            validate_definition(current, self.validator)
            definitions[current.ref] = current
            for node in current.nodes:
                if node.kind == NodeKind.TASK:
                    key = binding_key(current.ref, node.id)
                    if "agent_id" in node.config:
                        agent_id = str(node.config["agent_id"])
                        agent = await self.runtime.registry.get_current(agent_id)
                        version = node.config.get("agent_version")
                        if version is not None:
                            agent = await self.runtime.registry.get(VersionRef(agent_id, str(version)))
                        bindings[key] = agent.ref
                    else:
                        ids = node.config.get("candidate_agents")
                        if ids is None:
                            ids = [agent.ref.id for agent in await self.runtime.registry.list_agents()]
                        for agent_id in ids:
                            try:
                                agent = await self.runtime.registry.get_current(agent_id)
                                bindings[key + "#" + agent_id] = agent.ref
                            except RuntimeFault as fault:
                                if fault.code != "agent_not_found":
                                    raise
                elif node.kind in {NodeKind.SUBWORKFLOW, NodeKind.LOOP}:
                    child = await self.repository.get_definition(VersionRef(**node.config["workflow"]))
                    await visit(child, remaining - 1, stack + (current.ref,))
        await visit(definition, definition.max_subworkflow_depth, ())
        return definitions, bindings

    async def submit(self, ref: VersionRef, value: JsonValue, *, shared_budget: ResourceBudget | None = None, recovery_seed: tuple | None = None) -> WorkflowReport:
        await self.start()
        if len(self._roots) >= 16:
            raise RuntimeFault("workflow_capacity", "Too many active workflow roots", 409)
        definition = await self.repository.get_definition(ref)
        bounded_json(value)
        self.runtime.check_sensitive(value)
        self.validator.validate(definition.input_schema, value, "workflow_input_invalid")
        definitions, bindings = await self._prepare(definition)
        context = _Context(definitions, bindings, asyncio.Semaphore(definition.max_concurrency),
                           (_Budget(definition.max_node_executions),), definition.max_subworkflow_depth)
        context.strategy = definition.scheduling_strategy
        context.resources = ResourceBudget(**definition.resource_limits, parent=shared_budget)
        if recovery_seed:
            source, retained, audit, prior_failures = recovery_seed
            context.deadline = source.run.policy.deadline
            context.prior_failures = prior_failures
            context.recovery_audit = audit
            context.precise = True
            context.resources = shared_budget
            context.budgets[0].used = source.node_executions
            context.replay_scopes = {} if audit['effects_explicitly_authorized'] else {n.node_id:n.id for n in source.nodes}
        report = await self._create(definition, value, context)
        if recovery_seed:
            self._contexts[report.run.id] = context
            report = await self._change(report.run.id,'workflow.recovery_created',report_values={'recovery_audit':audit})
            for old in source.nodes:
                if old.node_id not in retained: continue
                report = await self._change(report.run.id,'node.reused',node_id=old.node_id,node_values={
                    'state':TaskState.SUCCEEDED,'input':old.input,'output':old.output,'agent_ref':old.agent_ref,
                    'verified':True,'simulated':old.simulated,'provider':old.provider,'model':old.model,
                    'reused_from':{'run_id':source.run.id,'node_id':old.id,'artifact_ref':asdict(old.current_artifact_ref),
                                   'verification_id':old.verification_ids[-1]},'ended_at':datetime.now(timezone.utc)})
        self._launch(report.run.id, context, root=True)
        return report

    async def run(self, ref: VersionRef, value: JsonValue) -> WorkflowReport:
        report = await self.submit(ref, value)
        try:
            return await self.wait(report.run.id)
        except asyncio.CancelledError:
            await self.cancel(report.run.id)
            raise

    async def get(self, run_id: str) -> WorkflowReport:
        await self.start()
        return await self.repository.get_report(run_id)

    async def wait(self, run_id: str) -> WorkflowReport:
        await self.get(run_id)
        job = self._jobs.get(run_id)
        if job is not None:
            await asyncio.shield(job)
        return await self.get(run_id)

    async def cancel(self, run_id: str) -> WorkflowReport:
        report = await self.get(run_id)
        if report.run.state in RUN_TERMINAL:
            return report
        job = self._jobs.get(run_id)
        if job is not None:
            job.cancel()
            with suppress(asyncio.CancelledError):
                await job
        report = await self.get(run_id)
        if report.run.state not in RUN_TERMINAL:
            await self._finish(run_id, WorkflowState.CANCELLED, "cancelled")
        return await self.get(run_id)

    async def close(self) -> None:
        for run_id in tuple(self._roots):
            await self.cancel(run_id)
        self._closed = True
        if self.scheduler:
            await self.scheduler.close()
        await self.repository.close()

    def _launch(self, run_id: str, context: _Context, root: bool = False) -> None:
        self._contexts[run_id] = context
        if root:
            self._roots.add(run_id)
        task = asyncio.create_task(self._drive(run_id, context))
        self._jobs[run_id] = task
        def done(completed: asyncio.Task[None]) -> None:
            self._jobs.pop(run_id, None)
            self._roots.discard(run_id)
            self._contexts.pop(run_id, None)
            self._locks.pop(run_id, None)
        task.add_done_callback(done)

    async def _create(self, definition: WorkflowDefinition, value: JsonValue, context: _Context,
                      parent: WorkflowReport | None = None, parent_node: str | None = None) -> WorkflowReport:
        self.validator.validate(definition.input_schema, value, "workflow_input_invalid")
        run_id = uuid4().hex
        if parent is None and self.runtime.durability and not self.runtime.durability.account_id(context.resources):
            self.runtime.durability.bind(context.resources,run_id+"-budget",run_id)
        deadline = datetime.now(timezone.utc) + timedelta(seconds=definition.timeout_seconds)
        if context.deadline is not None: deadline = min(deadline,context.deadline)
        if parent is not None:
            deadline = min(deadline, parent.run.policy.deadline)
        policy = RecoveryPolicy(1, 0, 0, deadline, 0, 0)
        metadata = WorkflowRun(run_id, definition.ref, WorkflowState.CREATED, policy,
                               parent.run.id if parent else None, 0)
        nodes = tuple(WorkflowNodeRun(uuid4().hex, node.id,
                      agent_ref=context.bindings.get(binding_key(definition.ref, node.id))) for node in definition.nodes)
        report = WorkflowReport(metadata, value, nodes, parent.root_run_id if parent else run_id,
                                parent_node_id=parent_node, depth=parent.depth + 1 if parent else 0,
                                agent_bindings=context.bindings, recovery_audit=context.recovery_audit if parent is None else {},
                                budget_account_id=self.runtime.durability.account_id(context.resources) if self.runtime.durability else None)
        event = RuntimeEvent(uuid4().hex, run_id, run_id, 1, "workflow.created", datetime.now(timezone.utc), {})
        report = replace(report, events=(event,))
        await self.repository.create_report(report)
        return report

    async def _change(self, run_id: str, event_type: str, *, node_id: str | None = None,
                      node_values: dict | None = None, report_values: dict | None = None) -> WorkflowReport:
        async with self._locks.setdefault(run_id, asyncio.Lock()):
            report = await self.repository.get_report(run_id)
            revision = report.run.revision
            payload: dict[str, JsonValue] = {}
            aggregate = run_id
            if node_id is not None:
                node = next(item for item in report.nodes if item.node_id == node_id)
                if node.state in NODE_TERMINAL:
                    return report
                values = node_values or {}
                node = replace(node, **values)
                aggregate = node.id
                payload = {"node_id": node_id, "state": node.state.value,
                           "error_code": node.error_code, "execution_id": node.execution_id}
                if node.attempt_ids:
                    payload.update(attempt_id=node.attempt_ids[-1], verified=node.verified,
                        verification_id=node.verification_ids[-1] if node.verification_ids else None,
                        feedback_id=node.feedback_ids[-1] if node.feedback_ids else None)
                report = replace(report, nodes=tuple(node if item.node_id == node_id else item for item in report.nodes))
            if report_values:
                report = replace(report, **report_values)
                payload.update(state=report.run.state.value, error_code=report.error_code)
            context = self._contexts.get(run_id)
            if context:
                report = replace(report, node_executions=context.budgets[-1].used, resource_usage=context.resources.snapshot())
            event = RuntimeEvent(uuid4().hex, run_id, aggregate, len(report.events) + 1,
                                 event_type, datetime.now(timezone.utc), payload)
            report = replace(report, run=replace(report.run, revision=revision + 1), events=report.events + (event,))
            await self.repository.save_report(report, revision)
            return report

    async def _finish(self, run_id: str, state: WorkflowState, error: str | None,
                      output: JsonValue = None, elapsed_ms: float = 0) -> None:
        report = await self.repository.get_report(run_id)
        if report.run.state in RUN_TERMINAL:
            return
        for node in report.nodes:
            if node.state not in NODE_TERMINAL:
                node_state = TaskState.CANCELLED if state == WorkflowState.CANCELLED or node.state == TaskState.RUNNING else TaskState.SKIPPED
                await self._change(run_id, "node." + node_state.value, node_id=node.node_id,
                    node_values={"state": node_state, "error_code": error, "ended_at": datetime.now(timezone.utc)})
        report = await self.repository.get_report(run_id)
        known = [node.simulated for node in report.nodes if node.simulated is not None]
        recovery_audit = report.recovery_audit
        usage = self._contexts[run_id].resources.snapshot() if run_id in self._contexts else report.resource_usage
        if recovery_audit:
            recovery_audit = {**recovery_audit, 'reexecuted_nodes':[n.node_id for n in report.nodes if not n.reused_from and (n.execution_id or n.attempt_ids or n.child_run_ids)],
                'final_verified':state==WorkflowState.SUCCEEDED and any(n.verified for n in report.nodes) and all(n.verified for n in report.nodes if n.simulated is not None and n.state==TaskState.SUCCEEDED),
                'final_state':state.value,'failed_nodes':{n.node_id:n.error_code for n in report.nodes if n.state==TaskState.FAILED},
                'usage_after':usage,
                'usage_delta':{k:v-recovery_audit['usage_before'].get(k,0) for k,v in usage.items()}}
        await self._change(run_id, "workflow." + state.value, report_values={
            'recovery_audit':recovery_audit,
            "run": replace(report.run, state=state), "error_code": error, "output": output,
            "ended_at": datetime.now(timezone.utc), "elapsed_ms": elapsed_ms,
            "simulated": all(known) if known else None,
            "verified": state == WorkflowState.SUCCEEDED and any(n.verified for n in report.nodes)
                and all(n.verified for n in report.nodes if n.simulated is not None and n.state == TaskState.SUCCEEDED),
        })

    @staticmethod
    def _context(report: WorkflowReport) -> dict[str, JsonValue]:
        # Failed/skipped/running outputs are never made available to mappings.
        return {"input": report.input, "nodes": {node.node_id: {"output": node.output}
                for node in report.nodes if node.state == TaskState.SUCCEEDED}}

    @staticmethod
    def _eligibility(definition: WorkflowDefinition, node: WorkflowNode, report: WorkflowReport) -> tuple[TaskState | None, str | None]:
        edges = [edge for edge in definition.edges if edge.target == node.id]
        if not edges:
            return TaskState.READY, None
        states = {item.node_id: item for item in report.nodes}
        if any(states[edge.source].state not in NODE_TERMINAL for edge in edges):
            return None, None
        selected = []
        inactive = False
        for edge in edges:
            parent = states[edge.source]
            if parent.state in {TaskState.FAILED, TaskState.CANCELLED} or parent.error_code == "upstream_failed":
                if edge.branch == "fallback" and parent.fallback_activated:
                    selected.append(parent)
                    continue
                return TaskState.SKIPPED, "upstream_failed"
            if parent.state == TaskState.SKIPPED or edge.branch == "fallback" or (edge.branch is not None and parent.output["branch"] != edge.branch):
                inactive = True
            else:
                selected.append(parent)
        if not selected:
            return TaskState.SKIPPED, "branch_not_selected"
        ignores_skips = node.kind == NodeKind.PARALLEL and node.config["join"] == "all_selected"
        if inactive and not ignores_skips:
            return TaskState.SKIPPED, "branch_not_selected"
        return TaskState.READY, None

    async def _graph(self, run_id: str, definition: WorkflowDefinition, context: _Context) -> None:
        running: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                report = await self.get(run_id)
                progressed = False
                for spec in definition.nodes:
                    state = next(item.state for item in report.nodes if item.node_id == spec.id)
                    if state == TaskState.PENDING:
                        eligible, reason = self._eligibility(definition, spec, report)
                        if eligible is not None:
                            progressed = True
                            report = await self._change(run_id, "node." + eligible.value, node_id=spec.id,
                                node_values={"state": eligible, "error_code": reason,
                                             **({"ready_at": datetime.now(timezone.utc)} if eligible == TaskState.READY else {}),
                                             **({"ended_at": datetime.now(timezone.utc)} if eligible == TaskState.SKIPPED else {})})
                failed = {item.node_id for item in report.nodes if item.state == TaskState.FAILED}
                if any(item.state == TaskState.CANCELLED for item in report.nodes):
                    # A cancelled node cannot make an otherwise terminal graph succeed.
                    raise asyncio.CancelledError
                if failed and any(node.config.get("on_error", definition.error_policy) == "fail_fast" for node in definition.nodes if node.id in failed):
                    raise RuntimeFault("workflow_node_failed", "A node failed with fail-fast policy")
                ready = tuple(item.node_id for item in report.nodes if item.state == TaskState.READY and item.node_id not in running)
                capacity = definition.max_concurrency - len(running)
                if ready and capacity and not any(n.state == TaskState.WAITING_HUMAN for n in report.nodes):
                    if self.scheduler:
                        now = datetime.now(timezone.utc)
                        ready = tuple(sorted(ready, key=lambda key: -effective_priority(
                            self._scheduling_task(report, next(spec for spec in definition.nodes if spec.id == key), context),
                            now, self.scheduler.config)))
                    chosen = self.dispatch.choose(ready, capacity)
                    if (not chosen or len(chosen) > capacity or len(set(chosen)) != len(chosen)
                            or not set(chosen).issubset(ready)):
                        raise RuntimeFault("workflow_dispatch_invalid", "Dispatch policy returned an invalid selection")
                    for key in chosen:
                        spec = next(item for item in definition.nodes if item.id == key)
                        running[key] = asyncio.create_task(self._node(run_id, definition, spec, context))
                if all(item.state in NODE_TERMINAL for item in report.nodes):
                    unrecovered = {key for key in failed if not any(edge.source == key and edge.branch == "fallback"
                        and next(n for n in report.nodes if n.node_id == key).fallback_activated
                        and next(n for n in report.nodes if n.node_id == edge.target).state == TaskState.SUCCEEDED
                        for edge in definition.edges)}
                    if unrecovered:
                        raise RuntimeFault("workflow_node_failed", "One or more nodes failed")
                    return
                if running:
                    done, _ = await asyncio.wait(tuple(running.values()), return_when=asyncio.FIRST_COMPLETED)
                    for key, task in tuple(running.items()):
                        if task in done:
                            task.result()
                            del running[key]
                elif not progressed:
                    raise RuntimeFault("workflow_deadlock", "No eligible nodes remain")
        finally:
            for task in running.values():
                task.cancel()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)

    def _scheduling_task(self, report: WorkflowReport, spec: WorkflowNode, context: _Context) -> SchedulingTask:
        key = binding_key(report.run.definition_ref, spec.id)
        refs = ((context.bindings[key],) if key in context.bindings else tuple(
            ref for name, ref in context.bindings.items() if name.startswith(key + "#")))
        ids = ([spec.config["agent_id"]] if "agent_id" in spec.config else spec.config.get("candidate_agents", [ref.id for ref in refs]))
        node = next(node for node in report.nodes if node.node_id == spec.id)
        definition = context.definitions[report.run.definition_ref]
        return SchedulingTask(node.id, report.run.id, spec.id, tuple(ids), refs,
            node.ready_at or datetime.now(timezone.utc), str(spec.config.get("task_type", "default")),
            tuple(spec.config.get("required_skills", [])), tuple(spec.config.get("preferred_skills", [])),
            tuple(VersionRef(**tool) for tool in spec.config.get("required_tools", [])),
            spec.config.get("priority", 50), len([edge for edge in definition.edges if edge.source == spec.id]),
            True, context.strategy, str(spec.config.get("no_candidate_policy", "fail")), spec.config.get("schedule_timeout_seconds", 10),
            features=TaskFeatures(**spec.config["scheduling_features"]) if "scheduling_features" in spec.config else None)

    async def _agent(self, run_id: str, spec: WorkflowNode, value: JsonValue, context: _Context) -> tuple[JsonValue, bool]:
        report = await self.get(run_id)
        ref = context.bindings.get(binding_key(report.run.definition_ref, spec.id))
        limits = RunLimits(**spec.config.get("limits", {}))
        task_input = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False)
        execution_id: str | None = None
        task_id = next(node.id for node in report.nodes if node.node_id == spec.id)
        output_succeeded = False
        dispatch_budget = context.resources
        if self.runtime.durability:
            dispatch_budget = context.resources.fork()
            dispatch_budget.ledger_run_id, dispatch_budget.ledger_node_id = run_id, task_id
        async with context.semaphore:
            if "verification" in spec.config:
                if self.selfloop is None:
                    raise RuntimeFault("verification_unavailable", "Verification service is not configured")
                async def notify(values):
                    snapshot = await self._change(run_id, "node." + values["state"].value, node_id=spec.id, node_values=values)
                    paused = any(n.state == TaskState.WAITING_HUMAN for n in snapshot.nodes)
                    state = WorkflowState.PAUSED if paused else WorkflowState.RUNNING
                    if snapshot.run.state != state:
                        await self._change(run_id, "workflow." + state.value, report_values={"run": replace(snapshot.run, state=state)})
                return await self.selfloop.run(self._scheduling_task(report, spec, context), value,
                    spec.config.get("output_mode", "text"), limits, VersionRef(**spec.config["verification"]),
                    replace(recovery_from(spec.config.get("recovery", {})),precise=True) if context.precise else recovery_from(spec.config.get("recovery", {})),
                    context.resources, notify, replay_scope=context.replay_scopes.get(spec.id),
                    prior_failure=context.prior_failures.get(binding_key(report.run.definition_ref,spec.id)+':'+self.selfloop.backend.digest(value)))
            try:
                if self.scheduler:
                    assignment = await self.scheduler.dispatch(self._scheduling_task(report, spec, context), task_input, limits, shared_budget=dispatch_budget)
                    execution_id = assignment.execution_id
                    ref = assignment.agent_ref
                    execution = await self.runtime.get(assignment.execution_id)
                    await self._change(run_id, "node.assigned", node_id=spec.id, node_values={
                        "state": TaskState.RUNNING, "started_at": assignment.assigned_at, "agent_ref": ref,
                        "assigned_at": assignment.assigned_at, "waiting_ms": assignment.waiting_ms,
                        "scheduling_decision_id": assignment.decision_id})
                else:
                    if ref is None:
                        raise RuntimeFault("scheduler_required", "Dynamic Agent nodes require a scheduling coordinator")
                    while True:
                        try:
                            execution = await self.runtime.submit(ref.id, task_input, limits, agent_version=ref.version, shared_budget=dispatch_budget)
                            break
                        except RuntimeFault as fault:
                            if fault.code != "agent_busy":
                                raise
                            await asyncio.sleep(.01)
                execution_id = execution.execution.id
                await self._change(run_id, "node.execution", node_id=spec.id,
                                   node_values={"execution_id": execution_id, "simulated": execution.simulated})
                result = await self.runtime.wait(execution_id)
                await self._change(run_id, "node.model_usage", node_id=spec.id, node_values={
                    "provider": result.provider, "model": result.model, "resolved_model": result.resolved_model,
                    "model_calls": result.model_calls,
                    "tool_calls": result.tool_calls,
                    "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "usage_complete": result.usage_complete,
                    "estimated_cost": result.estimated_cost, "cost_currency": result.cost_currency, "cost_complete": result.cost_complete})
                if result.execution.state != ExecutionState.SUCCEEDED:
                    raise RuntimeFault(result.execution.error_code or "agent_execution_failed", "Agent execution did not succeed")
                mode = spec.config.get("output_mode", "text")
                output: JsonValue = result.output
                if mode == "json":
                    try:
                        output = json.loads(result.output)
                    except (ValueError, TypeError, RecursionError):
                        raise RuntimeFault("workflow_node_output_invalid", "Agent output is not valid JSON") from None
                elif mode == "tool_result":
                    if not result.tool_calls or result.tool_calls[-1].state != ExecutionState.SUCCEEDED:
                        raise RuntimeFault("workflow_node_output_invalid", "No successful structured tool result")
                    output = result.tool_calls[-1].output
                bounded_json(output)
                output_succeeded = True
                return output, result.simulated
            except asyncio.CancelledError:
                if execution_id:
                    await self.runtime.cancel(execution_id)
                raise
            finally:
                if self.scheduler and execution_id:
                    await self.scheduler.complete(task_id, output_succeeded)

    async def _child(self, run_id: str, node: WorkflowNode, value: JsonValue, context: _Context) -> WorkflowReport:
        parent = await self.get(run_id)
        definition = context.definitions[VersionRef(**node.config["workflow"])]
        if context.remaining_depth <= 0:
            raise RuntimeFault("workflow_depth_exceeded", "Subworkflow depth limit exceeded")
        child_context = _Context(context.definitions, context.bindings, context.semaphore,
                                 context.budgets + (_Budget(definition.max_node_executions),),
                                 min(context.remaining_depth - 1, definition.max_subworkflow_depth))
        child_context.prior_failures = context.prior_failures
        child_context.precise = context.precise
        child_context.strategy = context.strategy
        child_context.resources = context.resources
        instance = next(item for item in parent.nodes if item.node_id == node.id)
        child = await self._create(definition, value, child_context, parent, instance.id)
        await self._change(run_id, "node.child_created", node_id=node.id,
                           node_values={"child_run_ids": instance.child_run_ids + (child.run.id,)})
        self._launch(child.run.id, child_context)
        try:
            result = await self.wait(child.run.id)
        except asyncio.CancelledError:
            result = await self.cancel(child.run.id)
            await self._child_simulation(run_id, node.id, result)
            raise
        await self._child_simulation(run_id, node.id, result)
        if result.run.state != WorkflowState.SUCCEEDED:
            raise RuntimeFault(result.error_code or "subworkflow_failed", "Child workflow did not succeed")
        return result

    async def _child_simulation(self, run_id: str, node_id: str, child: WorkflowReport) -> None:
        if child.simulated is None:
            return
        current = next(node for node in (await self.get(run_id)).nodes if node.node_id == node_id)
        simulated = child.simulated if current.simulated is None else current.simulated and child.simulated
        verified = child.verified if len(current.child_run_ids) == 1 else current.verified and child.verified
        await self._change(run_id, "node.child_finished", node_id=node_id, node_values={"simulated": simulated, "verified": verified})

    async def _node(self, run_id: str, definition: WorkflowDefinition, spec: WorkflowNode, context: _Context) -> None:
        started = perf_counter()
        try:
            context.consume()
            report = await self._change(run_id, "node.waiting_assignment" if self.scheduler and spec.kind == NodeKind.TASK else "node.running", node_id=spec.id,
                node_values={} if self.scheduler and spec.kind == NodeKind.TASK else
                {"state": TaskState.RUNNING, "started_at": datetime.now(timezone.utc)})
            mapping = self._context(report)
            value = resolve(spec.config.get("input", {"$ref": "input"}), mapping)
            await self._change(run_id, "node.input", node_id=spec.id, node_values={"input": value})
            simulated: bool | None = None
            async with asyncio.timeout(spec.config.get("timeout_seconds", definition.timeout_seconds)):
                if spec.kind == NodeKind.TASK:
                    output, simulated = await self._agent(run_id, spec, value, context)
                elif spec.kind == NodeKind.CONDITION:
                    selected = predicate(spec.config["predicate"], mapping)
                    output = {"branch": "true" if selected else "false", "condition_result": selected}
                elif spec.kind == NodeKind.PARALLEL:
                    parents = {edge.source for edge in definition.edges if edge.target == spec.id}
                    output = value if "input" in spec.config else {item.node_id: item.output for item in report.nodes
                              if item.node_id in parents and item.state == TaskState.SUCCEEDED}
                elif spec.kind == NodeKind.SUBWORKFLOW:
                    child = await self._child(run_id, spec, value, context)
                    output, simulated = child.output, child.simulated
                else:
                    current = value
                    simulation: list[bool] = []
                    for iteration in range(1, spec.config["max_iterations"] + 1):
                        child = await self._child(run_id, spec, {"value": current, "iteration": iteration}, context)
                        current = child.output
                        if child.simulated is not None:
                            simulation.append(child.simulated)
                        until_context = {**mapping, "result": current, "iteration": iteration}
                        if predicate(spec.config["until"], until_context):
                            break
                    else:
                        raise RuntimeFault("workflow_loop_limit", "Loop iteration limit reached without satisfying exit predicate")
                    output = {"value": current, "iterations": iteration}
                    simulated = all(simulation) if simulation else None
                bounded_json(output)
                await self._change(run_id, "node.succeeded", node_id=spec.id, node_values={
                    "state": TaskState.SUCCEEDED, "output": output, "simulated": simulated,
                    "ended_at": datetime.now(timezone.utc), "elapsed_ms": (perf_counter() - started) * 1000})
        except asyncio.CancelledError:
            current=next(n for n in (await self.get(run_id)).nodes if n.node_id==spec.id)
            stop=stop_diagnosis('cancelled',list(current.verification_ids)) if context.precise or spec.config.get('recovery',{}).get('precise') else None
            await self._change(run_id, "node.cancelled", node_id=spec.id, node_values={
                "state": TaskState.CANCELLED, **({"repair_diagnosis":stop} if stop else {}), "verified": False, "current_artifact_ref": None, "error_code": "cancelled", "ended_at": datetime.now(timezone.utc),
                "elapsed_ms": (perf_counter() - started) * 1000})
        except (RuntimeFault, TimeoutError) as fault:
            current=next(n for n in (await self.get(run_id)).nodes if n.node_id==spec.id)
            code=fault.code if isinstance(fault,RuntimeFault) else 'workflow_node_timeout'
            stop=stop_diagnosis(code,list(current.verification_ids)) if context.precise or spec.config.get('recovery',{}).get('precise') else None
            await self._change(run_id, "node.failed", node_id=spec.id, node_values={
                "state": TaskState.FAILED, **({"repair_diagnosis":stop} if stop else {}), "verified": False, "current_artifact_ref": None, "error_code": fault.code if isinstance(fault, RuntimeFault) else "workflow_node_timeout",
                "ended_at": datetime.now(timezone.utc), "elapsed_ms": (perf_counter() - started) * 1000})
        except Exception:
            await self._change(run_id, "node.failed", node_id=spec.id, node_values={
                "state": TaskState.FAILED, "error_code": "workflow_node_internal", "ended_at": datetime.now(timezone.utc),
                "elapsed_ms": (perf_counter() - started) * 1000})

    async def _drive(self, run_id: str, context: _Context) -> None:
        started = perf_counter()
        try:
            report = await self.get(run_id)
            definition = context.definitions[report.run.definition_ref]
            report = await self._change(run_id, "workflow.running", report_values={
                "run": replace(report.run, state=WorkflowState.RUNNING), "started_at": datetime.now(timezone.utc)})
            remaining = (report.run.policy.deadline - datetime.now(timezone.utc)).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                await self._graph(run_id, definition, context)
                report = await self.get(run_id)
                output = resolve(definition.output_mapping, self._context(report)) if definition.output_mapping else {
                    node.node_id: node.output for node in report.nodes if node.state == TaskState.SUCCEEDED}
                bounded_json(output)
                self.validator.validate(definition.output_schema, output, "workflow_output_invalid")
                await self._finish(run_id, WorkflowState.SUCCEEDED, None, output, (perf_counter() - started) * 1000)
        except asyncio.CancelledError:
            await self._finish(run_id, WorkflowState.CANCELLED, "cancelled", elapsed_ms=(perf_counter() - started) * 1000)
        except TimeoutError:
            await self._finish(run_id, WorkflowState.FAILED, "workflow_timeout", elapsed_ms=(perf_counter() - started) * 1000)
        except RuntimeFault as fault:
            await self._finish(run_id, WorkflowState.FAILED, fault.code, elapsed_ms=(perf_counter() - started) * 1000)
        except Exception:
            await self._finish(run_id, WorkflowState.FAILED, "workflow_internal", elapsed_ms=(perf_counter() - started) * 1000)
