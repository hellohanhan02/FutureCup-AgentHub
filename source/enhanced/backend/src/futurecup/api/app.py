"""Local development API for Agent Runtime and bounded workflow execution."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from futurecup.api.agents import build_router
from futurecup.api.workflows import build_workflow_router
from futurecup.api.scheduling import build_scheduling_router
from futurecup.api.verification import build_verification_router
from futurecup.api.console import build_console_router
from futurecup.api.providers import build_provider_router
from futurecup.application.runtime import AgentRuntimeService
from futurecup.bootstrap import build_runtime, build_workflows
from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault
from futurecup.application.planner import TaskPlanner
from futurecup.adapters.workspace_store import SQLiteWorkspaceRepository
from futurecup.adapters.workspace_validation import WorkspaceValidator, PLAN_SCHEMA, workspace_check
from futurecup.adapters.workspace_demo import template_plan
from futurecup.api.workspace import build_workspace_router
from futurecup.application.local_recovery import LocalRecoveryService
from futurecup.api.recovery import build_recovery_router
from futurecup.api.platform import build_platform_router
from futurecup.adapters.knowledge import KnowledgeStore,check_citations
from futurecup.adapters.coverage import knowledge_evidence
from futurecup.api.knowledge import build_knowledge_router
import os


class HealthResponse(BaseModel):
    status: str
    scope: str


def create_app(settings: Settings | None = None, runtime: AgentRuntimeService | None = None, *, knowledge_store: KnowledgeStore | None = None) -> FastAPI:
    configuration = settings or Settings.from_environment()
    service = runtime or build_runtime(configuration)
    knowledge=knowledge_store or KnowledgeStore(':memory:' if configuration.database_url.endswith(':memory:') else os.getenv('FUTURECUP_KNOWLEDGE_DATABASE','.data/knowledge-12b.sqlite3'))
    service.knowledge=knowledge
    workflows = build_workflows(configuration, service)
    workspace = TaskPlanner(workflows,SQLiteWorkspaceRepository(workflows),WorkspaceValidator(),PLAN_SCHEMA,template_plan)
    workflows.selfloop.backend.external['workspace-business-v1']=workspace_check(workflows.repository,workflows.selfloop.repository)
    async def citation_check(artifact):
        from futurecup.domain.verification import CheckEvidence
        node_id=artifact.ref.id.rsplit(':',1)[0]
        session=await workflows.selfloop.repository.session(node_id)
        attempt=await workflows.selfloop.repository.attempt(session['current_attempt'])
        execution=await service.get(attempt['execution_id'])
        return knowledge_evidence(artifact.content,execution.retrieval_records,artifact.digest)
    workflows.selfloop.backend.external['knowledge-citations-v1']=citation_check
    from futurecup.adapters.staged_knowledge import staged_check
    workflows.selfloop.backend.external['workspace-staged-knowledge-v1']=staged_check(workflows.repository,workflows.selfloop.repository,service)
    workflows.selfloop.backend.external['workspace-staged-knowledge-v2']=staged_check(workflows.repository,workflows.selfloop.repository,service)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            fixture=getattr(app.state,"platform_fixture",None)
            if fixture: await fixture.restore()
            yield
        finally:
            await workspace.close()
            await workflows.close()
            await service.close()
            owned_ledger=getattr(app.state,"owned_ledger",None)
            if owned_ledger:owned_ledger.close()
            if hasattr(knowledge,'connection'):knowledge.connection.close()

    app = FastAPI(title="FutureCup Agent Platform", version="0.1.0", lifespan=lifespan)
    app.state.settings = configuration
    app.state.runtime = service
    app.state.workflows = workflows
    app.state.workspace = workspace
    app.state.knowledge=knowledge
    app.state.local_recovery=LocalRecoveryService(workflows)
    app.include_router(build_recovery_router(app.state.local_recovery))
    app.include_router(build_platform_router(workflows))
    app.include_router(build_knowledge_router(knowledge))
    app.include_router(build_workspace_router(workspace))
    app.include_router(build_router(service))
    app.include_router(build_provider_router(service))
    app.include_router(build_workflow_router(workflows))
    app.include_router(build_console_router(workflows))
    if workflows.selfloop is not None:
        app.include_router(build_verification_router(workflows.selfloop))
    if workflows.scheduler is not None:
        app.include_router(build_scheduling_router(workflows.scheduler))

    @app.exception_handler(RuntimeFault)
    async def runtime_error(request: object, fault: RuntimeFault) -> JSONResponse:
        return JSONResponse(status_code=fault.status_code,
                            content={"error": {"code": fault.code, "message": fault.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: object, fault: RequestValidationError) -> JSONResponse:
        # Never reflect rejected inputs, headers or validation contexts containing secrets.
        return JSONResponse(status_code=422, content={"error": {
            "code": "invalid_request", "message": "Request validation failed",
            "fields": [{"location": list(error["loc"]), "type": error["type"]} for error in fault.errors()],
        }})

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", scope="process")

    @app.get("/api/v1/demo-info", tags=["health"])
    async def demo_mode():
        return {"mode": "interactive", "notice": "Use the standalone read-only replay service for historical evidence."}

    return app


app = create_app()
