"""Real SQLite snapshot, event atomicity and restart-query tests."""

import asyncio
from dataclasses import replace

import pytest

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import TaskState, WorkflowState
from runtime_support import agent
from workflow_support import engine, workflow


def test_sqlite_history_survives_repository_restart(tmp_path):
    async def scenario():
        path = tmp_path / "workflow.sqlite3"
        first = engine(path=path)
        await first.runtime.registry.register(agent())
        definition = workflow()
        await first.register(definition)
        report = await first.run(definition.ref, "persisted")
        await first.close()
        await first.runtime.close()
        second = engine(path=path)
        try:
            # Agent registry is empty after restart; historical output remains queryable.
            recovered = await second.get(report.run.id)
            assert recovered == report
            assert (await second.repository.get_definition(definition.ref)) == definition
            assert (await second.repository.get_run(report.run.id)).state == WorkflowState.SUCCEEDED
            count = second.repository.db.execute("SELECT COUNT(*) FROM workflow_events WHERE run_id=?", (report.run.id,)).fetchone()[0]
            assert count == len(report.events)
        finally:
            await second.close()
            await second.runtime.close()
    asyncio.run(scenario())


def test_interrupted_runs_fail_explicitly_instead_of_resuming(tmp_path):
    async def scenario():
        path = tmp_path / "interrupted.sqlite3"
        first = engine(path=path)
        await first.runtime.registry.register(agent())
        definition = workflow()
        await first.register(definition)
        # Persist an accepted snapshot without launching a worker, simulating a crash.
        from futurecup.application.workflow import _Budget, _Context
        definitions, bindings = await first._prepare(definition)
        context = _Context(definitions, bindings, asyncio.Semaphore(1), (_Budget(100),), 4)
        accepted = await first._create(definition, {}, context)
        await first.repository.close()
        await first.runtime.close()
        second = engine(path=path)
        try:
            recovered = await second.get(accepted.run.id)
            assert recovered.run.state == WorkflowState.FAILED
            assert recovered.error_code == "workflow_interrupted" and recovered.output is None
            assert recovered.nodes[0].state == TaskState.SKIPPED
            assert recovered.events[-1].type == "workflow.failed"
            assert recovered.events[-1].payload["error_code"] == "workflow_interrupted"
        finally:
            await second.close()
            await second.runtime.close()
    asyncio.run(scenario())


def test_event_conflict_rolls_back_snapshot_and_stale_revision_rejected():
    async def scenario():
        service_ = engine()
        try:
            await service_.runtime.registry.register(agent())
            definition = workflow()
            await service_.register(definition)
            report = await service_.run(definition.ref, "safe")
            revision = report.run.revision
            forged = replace(report, run=replace(report.run, revision=revision + 1), output="must-not-commit",
                             events=(replace(report.events[0], payload={"modified": True}),) + report.events[1:])
            with pytest.raises(RuntimeFault) as error:
                await service_.repository.save_report(forged, revision)
            assert error.value.code == "workflow_event_conflict"
            assert await service_.get(report.run.id) == report
            with pytest.raises(RuntimeFault) as error:
                await service_.repository.save_report(replace(report, run=replace(report.run, revision=revision + 1)), revision - 1)
            assert error.value.code == "workflow_revision_conflict"
            assert await service_.get(report.run.id) == report
        finally:
            await service_.close()
            await service_.runtime.close()
    asyncio.run(scenario())
