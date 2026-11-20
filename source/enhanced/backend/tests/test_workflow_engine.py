"""Real orchestration/Runtime/tool logic with explicitly simulated models."""

import asyncio
from dataclasses import replace
import json

import pytest

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import NodeKind, TaskState, VersionRef, WorkflowNode, WorkflowState
from runtime_support import agent, response, service
from workflow_support import engine, example, nodes, task, workflow


def test_sequential_dependencies_mapping_and_event_history():
    async def scenario():
        service_ = engine()
        try:
            for identifier, role in (("researcher", "research"), ("writer", "writing"), ("reviewer", "review")):
                await service_.runtime.registry.register(agent(identifier, role=role))
            definition = example("sequential")
            await service_.register(definition)
            result = await service_.run(definition.ref, {"topic": "actual runtime input"})
            assert result.run.state == WorkflowState.SUCCEEDED
            records = nodes(result)
            assert records["draft"].input == records["research"].output
            assert records["review"].input == records["draft"].output
            assert records["research"].ended_at <= records["draft"].started_at
            assert records["draft"].ended_at <= records["review"].started_at
            assert result.output == {"review": "[MOCK SIMULATED] " * 3 + "actual runtime input"}
            assert result.simulated and not result.verified and all(not n.verified for n in result.nodes)
            assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
            for record in result.nodes:
                states = [event.type for event in result.events if event.aggregate_id == record.id]
                assert states.index("node.ready") < states.index("node.running") < states.index("node.succeeded")
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_parallel_barrier_proves_overlap_and_join_waits_for_both():
    class BarrierMock:
        is_simulated = True
        def __init__(self):
            self.both_started = asyncio.Event()
            self.release = asyncio.Event()
            self.started = 0
        async def infer(self, request):
            text = request.messages[-1]["content"]
            if text in {"left", "right"}:
                self.started += 1
                if self.started == 2:
                    self.both_started.set()
                await self.release.wait()
            return response(text)
    async def scenario():
        provider = BarrierMock()
        service_ = engine(service(provider))
        try:
            for identifier in ("left-agent", "right-agent", "reviewer"):
                await service_.runtime.registry.register(agent(identifier))
            definition = workflow([
                task("left", "left-agent", input="left"), task("right", "right-agent", input="right"),
                WorkflowNode("join", NodeKind.PARALLEL, {"join": "all"}),
                task("review", "reviewer", input={"$ref": "nodes.join.output"}),
            ], [("left", "join"), ("right", "join"), ("join", "review")], max_concurrency=2)
            await service_.register(definition)
            initial = await service_.submit(definition.ref, {})
            await asyncio.wait_for(provider.both_started.wait(), 2)
            snapshot = nodes(await service_.get(initial.run.id))
            assert snapshot["left"].state == snapshot["right"].state == TaskState.RUNNING
            assert snapshot["join"].state == snapshot["review"].state == TaskState.PENDING
            provider.release.set()
            result = await service_.wait(initial.run.id)
            record = nodes(result)
            assert result.run.state == WorkflowState.SUCCEEDED
            assert record["join"].output == {"left": "left", "right": "right"}
            assert record["join"].started_at >= max(record["left"].ended_at, record["right"].ended_at)
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_concurrency_cap_one_and_same_agent_admission_do_not_fail():
    class CountingMock:
        is_simulated = True
        def __init__(self):
            self.active = self.peak = 0
        async def infer(self, request):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(.01)
                return response(request.messages[-1]["content"])
            finally:
                self.active -= 1
    async def scenario():
        provider = CountingMock()
        service_ = engine(service(provider))
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow([task("a", input="a"), task("b", input="b")], max_concurrency=1)
            await service_.register(definition)
            assert (await service_.run(definition.ref, {})).run.state == WorkflowState.SUCCEEDED
            assert provider.peak == 1
            concurrent = replace(definition, ref=VersionRef("concurrent", "1"), max_concurrency=2)
            await service_.register(concurrent)
            assert (await service_.run(concurrent.ref, {})).run.state == WorkflowState.SUCCEEDED
            assert provider.peak == 1  # Agent max_concurrency=1; engine waits, does not retry model calls.
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("selected", [True, False])
def test_conditional_skip_selected_join_and_safe_default_mapping(selected):
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow([
                WorkflowNode("pick", NodeKind.CONDITION, {"predicate": {"left": {"$ref": "input.flag"}, "op": "eq", "right": True}}),
                task("yes", input="yes"), task("no", input="no"),
                WorkflowNode("join", NodeKind.PARALLEL, {"join": "all_selected", "input": {
                    "yes": {"$ref": "nodes.yes.output", "default": None}, "no": {"$ref": "nodes.no.output", "default": None}}}),
            ], [("pick", "yes", "true"), ("pick", "no", "false"), ("yes", "join"), ("no", "join")])
            await service_.register(definition)
            result = await service_.run(definition.ref, {"flag": selected})
            record = nodes(result)
            active, skipped = ("yes", "no") if selected else ("no", "yes")
            assert result.run.state == WorkflowState.SUCCEEDED
            assert record[active].state == record["join"].state == TaskState.SUCCEEDED
            assert record[skipped].state == TaskState.SKIPPED and record[skipped].execution_id is None
            assert record["join"].output[skipped] is None
            assert record["join"].output[active] == "[MOCK SIMULATED] " + active
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("threshold,expected", [(2, WorkflowState.SUCCEEDED), (3, WorkflowState.FAILED)])
def test_bounded_loop_real_arithmetic_child_runs_and_limit(threshold, expected):
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent("counter", model="mock-calculator", tool_refs=(VersionRef("calculator", "1"),)))
            await service_.register(example("increment-body"))
            loop = WorkflowNode("loop", NodeKind.LOOP, {"workflow": {"id": "increment-body", "version": "1"},
                "input": {"result": 0}, "max_iterations": 2,
                "until": {"left": {"$ref": "result.result"}, "op": "gte", "right": threshold}})
            definition = workflow([loop])
            await service_.register(definition)
            result = await service_.run(definition.ref, {})
            assert result.run.state == expected
            record = result.nodes[0]
            assert len(record.child_run_ids) == 2
            for iteration, child_id in enumerate(record.child_run_ids, 1):
                child = await service_.get(child_id)
                assert child.output == {"result": iteration}
                assert child.run.parent_run_id == result.run.id and child.parent_node_id == record.id
                assert child.depth == 1 and child.simulated and not child.verified
            assert result.node_executions == 3
            if expected == WorkflowState.SUCCEEDED:
                assert record.output == {"value": {"result": 2}, "iterations": 2}
            else:
                assert record.error_code == "workflow_loop_limit" and record.output is None and result.output is None
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_subworkflow_and_shared_budget_cannot_reset_limits():
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            child = workflow(identifier="child")
            parent = workflow([WorkflowNode("child", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child", "version": "1"}})])
            await service_.register(child)
            await service_.register(parent)
            result = await service_.run(parent.ref, "payload")
            assert result.run.state == WorkflowState.SUCCEEDED and result.node_executions == 2
            assert result.nodes[0].output == {"work": "[MOCK SIMULATED] payload"}
            constrained = replace(parent, ref=VersionRef("budget", "1"), max_node_executions=1)
            await service_.register(constrained)
            failed = await service_.run(constrained.ref, "not executed")
            assert failed.run.state == WorkflowState.FAILED and failed.node_executions == 1
            nested = await service_.get(failed.nodes[0].child_run_ids[0])
            assert nested.nodes[0].error_code == "workflow_node_budget"
            assert nested.nodes[0].execution_id is None
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("recursive", [True, False])
def test_recursive_and_excessive_subworkflow_depth_rejected(recursive):
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            leaf = workflow(identifier="leaf")
            child = workflow([WorkflowNode("nested", NodeKind.SUBWORKFLOW, {"workflow": {"id": "root" if recursive else "leaf", "version": "1"}})], identifier="child")
            root = workflow([WorkflowNode("nested", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child", "version": "1"}})], identifier="root", max_subworkflow_depth=1)
            for definition in (leaf, child, root):
                await service_.register(definition)
            with pytest.raises(RuntimeFault) as error:
                await service_.submit(root.ref, {})
            assert error.value.code == ("workflow_recursion" if recursive else "workflow_depth_exceeded")
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_agent_failure_blocks_descendants_but_independent_branch_finishes():
    class FailingMock:
        is_simulated = True
        async def infer(self, request):
            if request.messages[-1]["content"] == "bad":
                raise RuntimeFault("test_agent_failure", "Intentional Mock failure")
            return response("independent")
    async def scenario():
        service_ = engine(service(FailingMock()))
        try:
            await service_.runtime.registry.register(agent("a"))
            await service_.runtime.registry.register(agent("b"))
            definition = workflow([task("bad", "a", input="bad"), task("good", "b", input="good"),
                                  task("blocked", "a", input={"$ref": "nodes.bad.output"})], [("bad", "blocked")])
            await service_.register(definition)
            result = await service_.run(definition.ref, {})
            record = nodes(result)
            assert result.run.state == WorkflowState.FAILED and result.output is None
            assert record["bad"].state == TaskState.FAILED and record["bad"].output is None
            assert record["blocked"].state == TaskState.SKIPPED and record["blocked"].error_code == "upstream_failed"
            assert record["blocked"].execution_id is None and record["good"].state == TaskState.SUCCEEDED
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["node_timeout", "workflow_timeout", "cancel_early", "cancel_running"])
def test_timeout_and_cancellation_drain_runtime_and_make_all_nodes_terminal(mode):
    class SlowMock:
        is_simulated = True
        def __init__(self):
            self.started = asyncio.Event()
        async def infer(self, request):
            self.started.set()
            await asyncio.sleep(10)
            return response()
    async def scenario():
        provider = SlowMock()
        service_ = engine(service(provider))
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow([task("a", timeout_seconds=.02 if mode == "node_timeout" else 2), task("b")],
                                  [("a", "b")], timeout_seconds=.02 if mode == "workflow_timeout" else 2)
            await service_.register(definition)
            initial = await service_.submit(definition.ref, {})
            if mode.startswith("cancel"):
                if mode == "cancel_running":
                    await asyncio.wait_for(provider.started.wait(), 2)
                result = await service_.cancel(initial.run.id)
                assert result.run.state == WorkflowState.CANCELLED
            else:
                result = await service_.wait(initial.run.id)
                assert result.run.state == WorkflowState.FAILED
                if mode == "node_timeout":
                    assert result.nodes[0].error_code == "workflow_node_timeout"
                else:
                    assert result.error_code == "workflow_timeout"
            assert all(node.state in {TaskState.FAILED, TaskState.CANCELLED, TaskState.SKIPPED} for node in result.nodes)
            for node in result.nodes:
                if node.execution_id:
                    assert (await service_.runtime.get(node.execution_id)).execution.state.value == "cancelled"
            revision = result.run.revision
            await asyncio.sleep(0)
            assert (await service_.get(result.run.id)).run.revision == revision
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_parallel_runs_and_report_snapshots_are_isolated():
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow()
            await service_.register(definition)
            a, b = await asyncio.gather(service_.run(definition.ref, "one"), service_.run(definition.ref, "two"))
            assert a.run.id != b.run.id and a.nodes[0].id != b.nodes[0].id
            assert a.output == {"work": "[MOCK SIMULATED] one"}
            assert b.output == {"work": "[MOCK SIMULATED] two"}
            assert all(event.workflow_run_id == a.run.id for event in a.events)
            a.output["work"] = "mutated caller"
            assert (await service_.get(a.run.id)).output == {"work": "[MOCK SIMULATED] one"}
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())
