"""Observed Mock statistics persist and duplicate observations never add samples."""

import asyncio
from dataclasses import replace

from futurecup.adapters.scheduling_store import SQLiteSchedulingRepository
from futurecup.adapters.workflow_store import SQLiteWorkflowRepository
from futurecup.application.scheduling import SchedulingCoordinator
from runtime_support import agent, service


def test_sqlite_profile_history_and_decisions_persist(tmp_path):
    async def scenario():
        path = str(tmp_path / "history.sqlite3")
        runtime = service()
        await runtime.registry.register(agent())
        workflows = SQLiteWorkflowRepository(path)
        first = SchedulingCoordinator(runtime, SQLiteSchedulingRepository(workflows))
        result = await runtime.run("analyst", "actual Mock execution")
        assert await first.observe(result, "analysis")
        assert not await first.observe(result, "another-type")
        from scheduling_support import request
        from futurecup.domain.models import RunLimits
        assignment = await first.dispatch(request(task_type="analysis"), "scheduled execution", RunLimits())
        await runtime.wait(assignment.execution_id)
        await first.complete(assignment.task_id)
        decisions = await first.repository.decisions()
        profile = await first.profile(result.execution.agent_ref, "analysis")
        assert profile.stats.sample_count == 2 and profile.stats.successes == 2
        await first.close()
        await workflows.close()
        second_store = SQLiteSchedulingRepository(SQLiteWorkflowRepository(path))
        second = SchedulingCoordinator(runtime, second_store)
        try:
            recovered = await second.profile(result.execution.agent_ref, "analysis")
            assert recovered.stats == profile.stats
            assert await second_store.decisions() == decisions
            assert not await second.observe(result, "analysis")
            assert len(await second_store.history(result.execution.agent_ref)) == 1
        finally:
            await second.close()
            await second_store.workflows.close()
            await runtime.close()
    asyncio.run(scenario())


def test_runtime_failure_and_structural_failure_are_observed_separately_from_manual_configuration():
    async def scenario():
        from scheduling_support import coordinator, close
        scheduler = coordinator()
        try:
            await scheduler.runtime.registry.register(agent(estimated_cost=2, estimated_latency_ms=100))
            actual = await scheduler.runtime.run("analyst", "valid text but not JSON")
            assert actual.execution.state.value == "succeeded"
            assert await scheduler.observe(actual, "json-task", succeeded=False)
            profile = await scheduler.profile(actual.execution.agent_ref, "json-task")
            assert profile.stats.failures == 1 and profile.stats.successes == 0
            assert profile.estimated_cost == 2 and profile.declared_latency_ms == 100
            assert profile.configuration_source == "agent_definition"
            # Future success injection cannot rewrite an existing outcome.
            assert not await scheduler.observe(actual, "json-task", succeeded=True)
            assert (await scheduler.profile(actual.execution.agent_ref, "json-task")).stats.failures == 1
        finally:
            await close(scheduler)
    asyncio.run(scenario())
