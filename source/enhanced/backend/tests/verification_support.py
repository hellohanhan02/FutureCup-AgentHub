"""Task 06 helpers: all model output is explicitly simulated."""

from futurecup.adapters.safety_store import SQLiteSafetyRepository
from futurecup.adapters.scheduling_store import SQLiteSchedulingRepository
from futurecup.adapters.verification import StructuredVerificationBackend
from futurecup.adapters.workflow_store import SQLiteWorkflowRepository, JsonSchemaWorkflowValidator
from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.application.selfloop import SelfLoopEngine
from futurecup.application.verification import VerifierEngine
from futurecup.application.workflow import WorkflowEngine
from futurecup.domain.models import VersionRef
from futurecup.domain.verification import VerificationPolicy, VerificationCheck
from runtime_support import agent, service
from workflow_support import task, workflow

REF = VersionRef("answer-policy", "1")
CHECK = VerificationCheck("json_schema", {"schema": {"type": "object", "required": ["answer"],
    "properties": {"answer": {"type": "integer", "const": 42}}, "additionalProperties": False}})


def safety_engine(provider=None, path=":memory:", external=None):
    runtime = service(provider)
    store = SQLiteWorkflowRepository(str(path))
    scheduler = SchedulingCoordinator(runtime, SQLiteSchedulingRepository(store))
    verifier = VerifierEngine(StructuredVerificationBackend(external), SQLiteSafetyRepository(store))
    loop = SelfLoopEngine(runtime, scheduler, verifier)
    return WorkflowEngine(runtime, store, JsonSchemaWorkflowValidator(), scheduler=scheduler, selfloop=loop)


async def setup(engine, checks=(CHECK,), agents=None):
    await engine.selfloop.verifier.register(VerificationPolicy(REF, "Explicit Mock test policy", checks))
    for config in agents or [agent()]:
        await engine.runtime.registry.register(config)


def definition(**recovery):
    return workflow([task("work", verification={"id": REF.id, "version": REF.version}, output_mode="json",
                          recovery=recovery)])


async def finish(engine, flow=None, value="Explicit Mock input"):
    flow = flow or definition()
    await engine.register(flow)
    report = await engine.run(flow.ref, value)
    node = report.nodes[0]
    repo = engine.selfloop.repository
    return report, await repo.attempts(node.id), await repo.results(node.id), await repo.feedbacks(node.id)


async def close(engine):
    await engine.close()
    await engine.runtime.close()
