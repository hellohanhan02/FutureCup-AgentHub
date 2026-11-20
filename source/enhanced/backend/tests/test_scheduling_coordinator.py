"""Real Runtime admission/statistics with explicitly Mock model execution."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import RunLimits, VersionRef
from runtime_support import agent, response, service
from scheduling_support import coordinator, request, close


class DelayedMock:
    is_simulated = True
    def __init__(self):
        self.calls = 0
    async def infer(self, request):
        self.calls += 1
        await asyncio.sleep(.01)
        return response()


@pytest.mark.parametrize("strategy", ["fixed", "round_robin", "caas"])
def test_strategies_share_constraints_and_release_capacity(strategy):
    async def scenario():
        scheduler = coordinator(service(DelayedMock()))
        try:
            await scheduler.runtime.registry.register(agent("bad", skills=()))
            await scheduler.runtime.registry.register(agent("good"))
            task = request(ids=("bad", "good"), required_skills=("analysis",), strategy=strategy)
            assignment = await scheduler.dispatch(task, "input", RunLimits())
            assert assignment.agent_ref.id == "good"
            assert (await scheduler.runtime.get(assignment.execution_id)).execution.task_id == task.task_id
            await scheduler.runtime.wait(assignment.execution_id)
            assert await scheduler.complete(task.task_id)
            assert not await scheduler.complete(task.task_id)
            profile = await scheduler.profile(assignment.agent_ref, "default")
            assert profile.capacity.active == 0 and profile.stats.successes == 1
            assert profile.stats.source == "observed_mock"
            records = await scheduler.repository.decisions(task_id=task.task_id)
            assert records[0]["selected"]["id"] == "good"
            assert records[0]["configuration"]["match_weight"] == .30
            rejected = next(item for item in records[0]["candidates"] if item["agent_id"] == "bad")
            assert "skills_mismatch" in rejected["reasons"]
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_concurrent_duplicate_dispatch_is_one_execution_and_identity_cannot_change():
    async def scenario():
        provider = DelayedMock()
        scheduler = coordinator(service(provider))
        try:
            await scheduler.runtime.registry.register(agent())
            task = request()
            assignments = await asyncio.gather(*(scheduler.dispatch(task, "same input", RunLimits()) for _ in range(12)))
            assert len({item.execution_id for item in assignments}) == 1
            await scheduler.runtime.wait(assignments[0].execution_id)
            assert provider.calls == 1
            with pytest.raises(RuntimeFault) as error:
                await scheduler.dispatch(task, "different input", RunLimits())
            assert error.value.code == "task_identity_conflict"
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_two_coordinators_cannot_claim_the_same_runtime_task_twice():
    async def scenario():
        provider = DelayedMock()
        first = coordinator(service(provider))
        second = SchedulingCoordinator(first.runtime, first.repository)
        try:
            await first.runtime.registry.register(agent(max_concurrency=2))
            task = request()
            outcomes = await asyncio.gather(first.dispatch(task, "input", RunLimits()),
                                            second.dispatch(task, "input", RunLimits()), return_exceptions=True)
            accepted = [item for item in outcomes if not isinstance(item, Exception)]
            rejected = [item for item in outcomes if isinstance(item, RuntimeFault)]
            assert len(accepted) == len(rejected) == 1 and rejected[0].code == "task_already_claimed"
            await first.runtime.wait(accepted[0].execution_id)
            assert provider.calls == 1
        finally:
            await second.close()
            await close(first)
    asyncio.run(scenario())


def test_runtime_admission_capacity_is_atomic_under_competition():
    async def scenario():
        runtime = service(DelayedMock())
        await runtime.registry.register(agent(max_concurrency=2))
        outcomes = await asyncio.gather(*(runtime.submit("analyst", "race", task_id=str(index)) for index in range(40)), return_exceptions=True)
        accepted = [item for item in outcomes if not isinstance(item, Exception)]
        assert len(accepted) == 2
        assert (await runtime.capacity("analyst")).peak_active == 2
        assert all(isinstance(item, RuntimeFault) and item.code == "agent_busy" for item in outcomes if isinstance(item, Exception))
        await runtime.close()
        assert (await runtime.capacity("analyst")).active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("policy,code", [("fail", "scheduling_no_candidate"), ("wait", "scheduling_timeout")])
def test_no_qualified_agent_fails_or_times_out_without_hanging(policy, code):
    async def scenario():
        scheduler = coordinator()
        try:
            await scheduler.runtime.registry.register(agent(skills=()))
            task = request(required_skills=("missing",), no_candidate_policy=policy, timeout_seconds=.02)
            with pytest.raises(RuntimeFault) as error:
                await scheduler.dispatch(task, "input", RunLimits())
            assert error.value.code == code
            assert (await scheduler.runtime.capacity("analyst")).active == 0
            assert await scheduler.repository.decisions(task_id=task.task_id)
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_waiting_task_observes_live_availability_changes_with_pinned_configuration():
    async def scenario():
        scheduler = coordinator()
        try:
            original = agent(available=False)
            await scheduler.runtime.registry.register(original)
            task = request(no_candidate_policy="wait")
            pending = asyncio.create_task(scheduler.dispatch(task, "input", RunLimits()))
            await asyncio.sleep(.01)
            await scheduler.runtime.registry.update(replace(original, ref=VersionRef("analyst", "2"), available=True), "1")
            assigned = await pending
            assert assigned.agent_ref.version == "1"
            await scheduler.runtime.wait(assigned.execution_id)
            records = await scheduler.repository.decisions(task_id=task.task_id)
            assert any(record["outcome"] == "no_candidate" for record in records)
            assert any(record["outcome"] == "claimed" for record in records)
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_aging_task_precedes_new_high_priority_when_capacity_becomes_available():
    class BlockingMock:
        is_simulated = True
        def __init__(self):
            self.release = asyncio.Event()
        async def infer(self, request):
            if request.messages[-1]["content"] == "background":
                await self.release.wait()
            return response()
    async def scenario():
        provider = BlockingMock()
        scheduler = coordinator(service(provider))
        try:
            await scheduler.runtime.registry.register(agent())
            background = await scheduler.runtime.submit("analyst", "background")
            old = request("old", priority=0, ready_at=datetime.now(timezone.utc) - timedelta(seconds=22), timeout_seconds=60)
            high = request("high", priority=100)
            jobs = [asyncio.create_task(scheduler.dispatch(task, "input", RunLimits())) for task in (high, old)]
            await asyncio.sleep(.01)
            provider.release.set()
            assignments = await asyncio.gather(*jobs)
            for assignment in assignments:
                await scheduler.runtime.wait(assignment.execution_id)
            records = await scheduler.repository.decisions()
            # Windows wall-clock timestamps may tie; SQLite insertion sequence is causal.
            claimed = sorted([record for record in records if record["outcome"] == "claimed"], key=lambda item: item["sequence"])
            assert [record["task_id"] for record in claimed] == ["old", "high"]
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_observations_are_terminal_idempotent_and_do_not_mutate_decision_snapshots():
    async def scenario():
        scheduler = coordinator(service(DelayedMock()))
        try:
            await scheduler.runtime.registry.register(agent())
            assigned = await scheduler.dispatch(request(), "input", RunLimits())
            original = (await scheduler.repository.decisions())[0]
            assert original["candidates"][0]["profile"]["stats"]["successes"] == 0
            with pytest.raises(RuntimeFault) as error:
                await scheduler.observe(await scheduler.runtime.get(assigned.execution_id), "default")
            assert error.value.code == "observation_not_terminal"
            await scheduler.runtime.wait(assigned.execution_id)
            assert await scheduler.complete("task") and not await scheduler.complete("task")
            profile = await scheduler.profile(assigned.agent_ref, "default")
            assert profile.stats.sample_count == 1 and profile.stats.mean_duration_ms is not None
            assert profile.stats.revision == 1
            assert (await scheduler.repository.decisions())[0] == original
            assert (await scheduler.profile(assigned.agent_ref, "another-type")).stats.sample_count == 0
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_cancellation_releases_capacity_and_is_not_a_reliability_sample():
    async def scenario():
        scheduler = coordinator(service(DelayedMock()))
        try:
            await scheduler.runtime.registry.register(agent())
            assigned = await scheduler.dispatch(request(), "input", RunLimits())
            await scheduler.runtime.cancel(assigned.execution_id)
            await scheduler.complete("task")
            profile = await scheduler.profile(assigned.agent_ref, "default")
            assert profile.capacity.active == 0
            assert profile.stats.cancellations == 1 and profile.stats.sample_count == 0
            assert profile.stats.empirical_success_rate is None
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_many_completions_do_not_lose_profile_updates():
    async def scenario():
        scheduler = coordinator(service(DelayedMock()))
        try:
            await scheduler.runtime.registry.register(agent(max_concurrency=4))
            assignments = await asyncio.gather(*(scheduler.dispatch(request(str(index)), "input", RunLimits()) for index in range(12)))
            for assignment in assignments:
                await scheduler.runtime.wait(assignment.execution_id)
            results = await asyncio.gather(*(scheduler.complete(assignment.task_id) for assignment in assignments))
            assert all(results)
            profile = await scheduler.profile(VersionRef("analyst", "1"), "default")
            assert profile.stats.successes == profile.stats.sample_count == 12
            assert profile.stats.revision == 12 and profile.capacity.peak_active <= 4
        finally:
            await close(scheduler)
    asyncio.run(scenario())


def test_cancelled_queued_request_cannot_later_acquire_capacity():
    async def scenario():
        scheduler = coordinator()
        try:
            original = agent(available=False)
            await scheduler.runtime.registry.register(original)
            pending = asyncio.create_task(scheduler.dispatch(request(no_candidate_policy="wait"), "input", RunLimits()))
            await asyncio.sleep(.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await scheduler.runtime.registry.update(replace(original, ref=VersionRef("analyst", "2"), available=True), "1")
            await asyncio.sleep(.01)
            assert (await scheduler.runtime.capacity("analyst")).peak_active == 0
            assert any(record["outcome"] == "scheduling_cancelled" for record in await scheduler.repository.decisions())
        finally:
            await close(scheduler)
    asyncio.run(scenario())
