"""Cancellation, pinned versions and failure semantics with explicit Mock models."""

import asyncio
from dataclasses import replace

import pytest

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import NodeKind, TaskState, VersionRef, WorkflowNode, WorkflowState
from runtime_support import agent, response, service
from workflow_support import engine, nodes, task, workflow


def test_fail_fast_cancels_other_active_agent_without_publishing_failure_output():
    class FailFastMock:
        is_simulated = True
        def __init__(self):
            self.slow_started = asyncio.Event()
        async def infer(self, request):
            if request.messages[-1]["content"] == "bad":
                await self.slow_started.wait()
                raise RuntimeFault("mock_failure", "Intentional failure")
            self.slow_started.set()
            await asyncio.sleep(10)
            return response()
    async def scenario():
        service_ = engine(service(FailFastMock()))
        try:
            for identifier in ("bad-agent", "slow-agent"):
                await service_.runtime.registry.register(agent(identifier))
            definition = workflow([task("bad", "bad-agent", input="bad", on_error="fail_fast"),
                                   task("slow", "slow-agent", input="slow")])
            await service_.register(definition)
            result = await service_.run(definition.ref, {})
            record = nodes(result)
            assert result.run.state == WorkflowState.FAILED and result.output is None
            assert record["bad"].state == TaskState.FAILED
            assert record["slow"].state == TaskState.CANCELLED
            assert (await service_.runtime.get(record["slow"].execution_id)).execution.state.value == "cancelled"
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_parent_cancellation_reaches_child_workflow_and_leaf_runtime():
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
            child = workflow(identifier="child")
            parent = workflow([WorkflowNode("call", NodeKind.SUBWORKFLOW, {"workflow": {"id": "child", "version": "1"}})])
            for definition in (child, parent):
                await service_.register(definition)
            accepted = await service_.submit(parent.ref, "cancel tree")
            await asyncio.wait_for(provider.started.wait(), 2)
            result = await service_.cancel(accepted.run.id)
            child_report = await service_.get(result.nodes[0].child_run_ids[0])
            assert result.run.state == child_report.run.state == WorkflowState.CANCELLED
            assert result.nodes[0].state == child_report.nodes[0].state == TaskState.CANCELLED
            assert result.simulated and not result.verified
            assert (await service_.runtime.get(child_report.nodes[0].execution_id)).execution.state.value == "cancelled"
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_agent_versions_are_pinned_for_entire_workflow_before_nodes_start():
    class WaitingMock:
        is_simulated = True
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def infer(self, request):
            if request.messages[-1]["content"] == "first":
                self.started.set()
                await self.release.wait()
            return response(request.messages[0]["content"])
    async def scenario():
        provider = WaitingMock()
        service_ = engine(service(provider))
        try:
            await service_.runtime.registry.register(agent("first-agent"))
            original = agent("second-agent")
            await service_.runtime.registry.register(original)
            definition = workflow([task("first", "first-agent", input="first"), task("second", "second-agent", input="second")], [("first", "second")])
            await service_.register(definition)
            accepted = await service_.submit(definition.ref, {})
            await provider.started.wait()
            await service_.runtime.registry.update(replace(original, ref=VersionRef("second-agent", "2"), system_prompt="changed prompt"), "1")
            provider.release.set()
            result = await service_.wait(accepted.run.id)
            assert nodes(result)["second"].agent_ref.version == "1"
            assert nodes(result)["second"].output == "You are second-agent"
            future = await service_.run(definition.ref, {})
            assert nodes(future)["second"].agent_ref.version == "2"
            assert nodes(future)["second"].output == "changed prompt"
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mapping,schema,expected", [
    ({"missing": {"$ref": "input.missing"}}, {}, "workflow_mapping_missing"),
    ({}, {"type": "array"}, "workflow_output_invalid"),
])
def test_missing_data_or_output_schema_failure_never_publishes_root_success(mapping, schema, expected):
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow(output_mapping=mapping, output_schema=schema)
            await service_.register(definition)
            result = await service_.run(definition.ref, "input")
            assert result.nodes[0].state == TaskState.SUCCEEDED
            assert result.run.state == WorkflowState.FAILED and result.error_code == expected
            assert result.output is None and not result.verified
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_dispatch_boundary_rejects_policy_selecting_non_ready_nodes():
    class InvalidDispatch:
        def choose(self, ready_ids, capacity):
            return ("not-eligible",)
    async def scenario():
        service_ = engine(dispatch=InvalidDispatch())
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow()
            await service_.register(definition)
            report = await service_.run(definition.ref, {})
            assert report.run.state == WorkflowState.FAILED
            assert report.error_code == "workflow_dispatch_invalid"
            assert report.nodes[0].execution_id is None
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_strict_join_propagates_branch_skip_without_waiting_forever():
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow([
                WorkflowNode("choose", NodeKind.CONDITION, {"predicate": {"left": True, "op": "eq", "right": True}}),
                task("yes"), task("no"), WorkflowNode("join", NodeKind.PARALLEL, {"join": "all"}),
            ], [("choose", "yes", "true"), ("choose", "no", "false"), ("yes", "join"), ("no", "join")])
            await service_.register(definition)
            result = await service_.run(definition.ref, "input")
            assert result.run.state == WorkflowState.SUCCEEDED
            assert nodes(result)["no"].state == nodes(result)["join"].state == TaskState.SKIPPED
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())


def test_arbitrary_json_node_order_propagates_skips_to_a_fixed_point():
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            # Reverse-topological JSON order; the unselected chain must not deadlock.
            definition = workflow([
                task("last"), task("unselected"), task("selected"),
                WorkflowNode("pick", NodeKind.CONDITION, {"predicate": {"left": True, "op": "eq", "right": True}}),
            ], [("pick", "selected", "true"), ("pick", "unselected", "false"), ("unselected", "last")])
            await service_.register(definition)
            result = await service_.run(definition.ref, "input")
            assert result.run.state == WorkflowState.SUCCEEDED
            assert nodes(result)["selected"].state == TaskState.SUCCEEDED
            assert nodes(result)["last"].state == nodes(result)["unselected"].state == TaskState.SKIPPED
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())
