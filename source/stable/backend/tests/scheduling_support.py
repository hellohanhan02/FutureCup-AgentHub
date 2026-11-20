"""Explicit Mock scheduling fixtures; never a real LLM evaluation."""

from datetime import datetime, timezone

from futurecup.adapters.scheduling_store import SQLiteSchedulingRepository
from futurecup.adapters.workflow_store import SQLiteWorkflowRepository
from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.domain.models import VersionRef
from futurecup.domain.scheduling import SchedulingTask
from runtime_support import service


def coordinator(runtime=None):
    repository = SQLiteWorkflowRepository()
    return SchedulingCoordinator(runtime or service(), SQLiteSchedulingRepository(repository))


def request(identifier="task", ids=("analyst",), **changes):
    values = dict(task_id=identifier, workflow_run_id="test-run", node_id=identifier,
                  candidate_ids=tuple(ids), candidate_refs=tuple(VersionRef(key, "1") for key in ids),
                  ready_at=datetime.now(timezone.utc), timeout_seconds=2)
    values.update(changes)
    return SchedulingTask(**values)


async def close(scheduler):
    await scheduler.runtime.close()
    await scheduler.close()
    await scheduler.repository.workflows.close()
