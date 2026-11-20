"""Runtime integration tests. ALL model calls in this file are explicit Mock calls."""

import asyncio
from dataclasses import replace
import json

import pytest

from futurecup.adapters.tools import ToolDefinition
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import ExecutionState, RunLimits, VersionRef
from runtime_support import agent, response, service, ScriptedMockProvider, tool_call


def test_mock_execution_records_real_runtime_statistics_and_artifact():
    async def scenario():
        runtime = service()
        await runtime.registry.register(agent())
        result = await runtime.run("analyst", "hello")
        assert result.execution.state == ExecutionState.SUCCEEDED
        assert result.simulated and result.output == "[MOCK SIMULATED] hello"
        assert result.call_count == 1 and len(result.model_calls) == 1
        assert result.elapsed_ms >= 0 and not result.usage_complete
        assert result.execution.started_at.tzinfo and result.execution.ended_at.tzinfo
        assert await runtime.artifacts.get(result.execution.output_refs[0]) == result.output.encode()
        await runtime.close()
    asyncio.run(scenario())


def test_mock_tool_round_trip_builds_correct_conversation():
    async def scenario():
        provider = ScriptedMockProvider(response("", (tool_call(),)), response())
        runtime = service(provider)
        await runtime.registry.register(agent(tool_refs=(VersionRef("calculator", "1"),)))
        result = await runtime.run("analyst", "compute")
        assert result.execution.state == ExecutionState.SUCCEEDED
        assert result.call_count == 3
        assert result.tool_calls[0].output == {"result": 5}
        assert result.tool_calls[0].state == ExecutionState.SUCCEEDED
        assert [m["role"] for m in provider.requests[1].messages] == ["system", "user", "assistant", "tool"]
        assert provider.requests[1].messages[-1]["tool_call_id"] == "call_1"
        assert json.loads(provider.requests[1].messages[-1]["content"]) == {"result": 5}
        assert provider.requests[0].tools[0]["function"]["name"] == "calculator"
        assert provider.requests[0].messages[0]["content"] == "You are analyst"
        stored = await runtime.get(result.execution.id)
        stored.tool_calls[0].arguments["a"] = 99
        assert (await runtime.get(result.execution.id)).tool_calls[0].arguments["a"] == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("failure,code", [
    (RuntimeFault("provider_http_error", "Rejected", 502), "provider_http_error"),
    (ValueError("secret-do-not-expose"), "model_call_failed"),
])
def test_mock_model_failure_is_sanitized(failure, code):
    async def scenario():
        runtime = service(ScriptedMockProvider(failure))
        await runtime.registry.register(agent())
        result = await runtime.run("analyst", "fail")
        assert result.execution.state == ExecutionState.FAILED
        assert result.execution.error_code == code
        assert result.model_calls[0].error_code == code
        assert "secret-do-not-expose" not in repr(result)
        assert not result.execution.output_refs
    asyncio.run(scenario())


@pytest.mark.parametrize("overall,model,code", [(1, .01, "model_timeout"), (.01, 1, "execution_timeout")])
def test_mock_model_and_execution_timeouts(overall, model, code):
    class SlowMock:
        is_simulated = True
        async def infer(self, request):
            await asyncio.sleep(10)
            return response()
    async def scenario():
        runtime = service(SlowMock())
        await runtime.registry.register(agent())
        result = await runtime.run("analyst", "wait", RunLimits(timeout_seconds=overall, model_timeout_seconds=model))
        assert result.execution.state == ExecutionState.TIMED_OUT
        assert result.execution.error_code == code
        assert len(result.model_calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("allowed,call,code", [
    ((), tool_call(), "tool_unauthorized"),
    ((VersionRef("calculator", "1"),), tool_call(arguments={"operation": "add", "a": "2", "b": 3}), "tool_arguments_invalid"),
    ((VersionRef("calculator", "1"),), tool_call(arguments={"operation": "add", "a": 2, "b": 3, "extra": 0}), "tool_arguments_invalid"),
    ((VersionRef("calculator", "1"),), tool_call(arguments={"operation": "divide", "a": 2, "b": 0}), "tool_execution_failed"),
])
def test_mock_tool_failures_are_recorded(allowed, call, code):
    async def scenario():
        runtime = service(ScriptedMockProvider(response("", (call,))))
        await runtime.registry.register(agent(tool_refs=allowed))
        result = await runtime.run("analyst", "call tool")
        assert result.execution.state == ExecutionState.FAILED
        assert result.execution.error_code == code
        assert result.tool_calls[0].error_code == code
        assert result.call_count == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("limits,model_count,tool_count", [
    (RunLimits(max_calls=1), 1, 0),
    (RunLimits(max_model_calls=1), 1, 2),
    (RunLimits(max_tool_calls=1), 1, 1),
])
def test_mock_call_limits_stop_further_calls(limits, model_count, tool_count):
    async def scenario():
        provider = ScriptedMockProvider(response("", (tool_call(identifier="a"), tool_call(identifier="b"))), response())
        runtime = service(provider)
        await runtime.registry.register(agent(tool_refs=(VersionRef("calculator", "1"),)))
        result = await runtime.run("analyst", "loop", limits)
        assert result.execution.state == ExecutionState.FAILED
        assert result.execution.error_code == "call_limit_exceeded"
        assert len(result.model_calls) == model_count and len(result.tool_calls) == tool_count
        assert len(provider.requests) == model_count
    asyncio.run(scenario())


def test_mock_tool_timeout():
    async def slow():
        await asyncio.sleep(10)
    async def scenario():
        runtime = service(ScriptedMockProvider(response("", (tool_call("slow", {}),))))
        runtime.tools.register(ToolDefinition(VersionRef("slow", "1"), "Test only", {"type": "object"}), slow)
        await runtime.registry.register(agent(tool_refs=(VersionRef("slow", "1"),)))
        result = await runtime.run("analyst", "wait", RunLimits(tool_timeout_seconds=.01))
        assert result.execution.state == ExecutionState.TIMED_OUT
        assert result.tool_calls[0].error_code == "tool_timeout"
    asyncio.run(scenario())


@pytest.mark.parametrize("before_start", [True, False])
def test_mock_cancel_before_start_and_during_call_releases_capacity(before_start):
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
        runtime = service(provider)
        await runtime.registry.register(agent())
        initial = await runtime.submit("analyst", "cancel")
        if not before_start:
            await provider.started.wait()
        result = await runtime.cancel(initial.execution.id)
        assert result.execution.state == ExecutionState.CANCELLED
        assert (await runtime.cancel(initial.execution.id)).execution.state == ExecutionState.CANCELLED
        another = await runtime.submit("analyst", "second")
        await runtime.close()
        assert (await runtime.get(another.execution.id)).execution.state == ExecutionState.CANCELLED
        with pytest.raises(RuntimeFault) as error:
            await runtime.submit("analyst", "closed")
        assert error.value.code == "runtime_closed"
    asyncio.run(scenario())


def test_mock_agents_have_isolated_prompts_outputs_errors_and_counters():
    class IsolatedMock:
        is_simulated = True
        async def infer(self, request):
            await asyncio.sleep(.01)
            if request.messages[-1]["content"] == "fail":
                raise RuntimeFault("test_failure", "Intentional Mock failure")
            return response(request.messages[0]["content"] + ":" + request.messages[-1]["content"])
    async def scenario():
        runtime = service(IsolatedMock())
        await runtime.registry.register(agent("a"))
        await runtime.registry.register(agent("b", role="reviewer"))
        a, b = await asyncio.gather(runtime.run("a", "fail"), runtime.run("b", "success"))
        assert a.execution.state == ExecutionState.FAILED and a.output is None
        assert b.execution.state == ExecutionState.SUCCEEDED and b.output == "You are b:success"
        assert a.call_count == b.call_count == 1
        assert a.execution.id != b.execution.id
        assert a.execution.agent_ref.id == "a" and b.execution.agent_ref.id == "b"
    asyncio.run(scenario())


def test_mock_busy_agent_and_config_snapshot_on_update_disable():
    class WaitingMock:
        is_simulated = True
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
        async def infer(self, request):
            self.started.set()
            await self.release.wait()
            return response(request.messages[0]["content"])
    async def scenario():
        provider = WaitingMock()
        runtime = service(provider)
        original = agent()
        await runtime.registry.register(original)
        initial = await runtime.submit("analyst", "snapshot")
        await provider.started.wait()
        with pytest.raises(RuntimeFault) as error:
            await runtime.submit("analyst", "busy")
        assert error.value.code == "agent_busy"
        await runtime.registry.update(replace(original, ref=VersionRef("analyst", "2"), system_prompt="new"), "1")
        await runtime.registry.set_enabled("analyst", False)
        with pytest.raises(RuntimeFault) as error:
            await runtime.submit("analyst", "disabled")
        assert error.value.code == "agent_disabled"
        provider.release.set()
        result = await runtime.wait(initial.execution.id)
        assert result.execution.agent_ref.version == "1" and result.output == "You are analyst"
        await runtime.registry.set_enabled("analyst", True)
        assert (await runtime.run("analyst", "updated")).output == "new"
    asyncio.run(scenario())


def test_execution_retention_limit_does_not_overwrite_results():
    async def scenario():
        runtime = service(capacity=1)
        await runtime.registry.register(agent())
        first = await runtime.run("analyst", "retained")
        with pytest.raises(RuntimeFault) as error:
            await runtime.run("analyst", "overflow")
        assert error.value.code == "execution_capacity"
        assert (await runtime.get(first.execution.id)).output == "[MOCK SIMULATED] retained"
    asyncio.run(scenario())


def test_mock_python_caller_cancellation_stops_underlying_execution():
    class SlowMock:
        is_simulated = True
        def __init__(self):
            self.started = asyncio.Event()
            self.execution_id = None
        async def infer(self, request):
            self.execution_id = request.trace_id
            self.started.set()
            await asyncio.sleep(10)
            return response()
    async def scenario():
        provider = SlowMock()
        runtime = service(provider)
        await runtime.registry.register(agent())
        job = asyncio.create_task(runtime.run("analyst", "cancel Python caller"))
        await provider.started.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        result = await runtime.get(provider.execution_id)
        assert result.execution.state == ExecutionState.CANCELLED
        assert result.model_calls[0].error_code == "cancelled"
        await runtime.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("calls", [
    (tool_call(identifier="duplicate"), tool_call(identifier="duplicate")),
    ({"id": "a", "name": "calculator", "arguments": []},),
])
def test_mock_malformed_tool_calls_are_rejected_before_tools_run(calls):
    async def scenario():
        runtime = service(ScriptedMockProvider(response("", calls)))
        await runtime.registry.register(agent(tool_refs=(VersionRef("calculator", "1"),)))
        result = await runtime.run("analyst", "invalid")
        assert result.execution.state == ExecutionState.FAILED
        assert result.execution.error_code == "provider_response_invalid"
        assert result.tool_calls == ()
    asyncio.run(scenario())


@pytest.mark.parametrize("limits", [{"timeout_seconds": float("nan")}, {"max_calls": True}, {"max_tool_calls": 0}])
def test_runtime_limits_validate_python_callers(limits):
    with pytest.raises(ValueError):
        RunLimits(**limits)
