"""JSON workflow DTOs. Graph semantics are validated in the application layer."""

from dataclasses import asdict
from typing import Any, Annotated, Literal

from fastapi import APIRouter, Response, Query
from fastapi.encoders import jsonable_encoder
from pydantic import Field

from futurecup.api.agents import Identifier, StrictModel
from futurecup.application.workflow import WorkflowEngine
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import NodeKind, VersionRef, WorkflowDefinition, WorkflowEdge, WorkflowNode


class NodeInput(StrictModel):
    id: Identifier
    kind: NodeKind
    config: dict[str, Any] = Field(default_factory=dict)


class EdgeInput(StrictModel):
    source: Identifier
    target: Identifier
    branch: str | None = None


class WorkflowInput(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=128)
    version: Identifier = "1"
    nodes: list[NodeInput] = Field(min_length=1, max_length=64)
    edges: list[EdgeInput] = Field(default_factory=list, max_length=256)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    output_mapping: dict[str, Any] = Field(default_factory=dict)
    max_subworkflow_depth: int = Field(default=4, ge=1, le=8, strict=True)
    max_concurrency: int = Field(default=4, ge=1, le=16, strict=True)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_node_executions: int = Field(default=100, ge=1, le=512, strict=True)
    error_policy: str = "continue"
    scheduling_strategy: Literal["fixed", "round_robin", "caas"] = "caas"

    resource_limits: dict[str, Annotated[int, Field(strict=True, ge=0, le=10000000)]] = Field(default_factory=dict)

    def definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            VersionRef(self.id, self.version), tuple(WorkflowNode(n.id, n.kind, n.config) for n in self.nodes),
            tuple(WorkflowEdge(e.source, e.target, e.branch) for e in self.edges), self.input_schema,
            self.output_schema, self.max_subworkflow_depth, self.name, self.output_mapping,
            self.max_concurrency, self.timeout_seconds, self.max_node_executions, self.error_policy,
            self.scheduling_strategy, self.resource_limits,
        )


class WorkflowExecute(StrictModel):
    input: Any
    version: Identifier | None = None


def definition_response(definition: WorkflowDefinition) -> WorkflowInput:
    values = asdict(definition)
    values.update(id=definition.ref.id, version=definition.ref.version)
    values.pop("ref")
    return WorkflowInput.model_validate(values)


def build_workflow_router(engine: WorkflowEngine) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["workflows"])

    @router.post("/workflows", status_code=201, response_model=WorkflowInput)
    async def create_workflow(data: WorkflowInput) -> WorkflowInput:
        await engine.register(data.definition())
        return data

    @router.get("/workflows", response_model=list[WorkflowInput])
    async def list_workflows() -> list[WorkflowInput]:
        await engine.start()
        return [definition_response(item) for item in await engine.repository.list_definitions()]

    @router.get("/workflows/{workflow_id}", response_model=WorkflowInput)
    async def get_workflow(workflow_id: str, version: str | None = None) -> WorkflowInput:
        await engine.start()
        definition = (await engine.repository.get_definition(VersionRef(workflow_id, version)) if version is not None
                      else await engine.repository.latest(workflow_id))
        return definition_response(definition)

    @router.post("/workflows/{workflow_id}/executions", status_code=202)
    async def execute(workflow_id: str, data: WorkflowExecute, response: Response) -> dict[str, Any]:
        await engine.start()
        definition = (await engine.repository.get_definition(VersionRef(workflow_id, data.version)) if data.version is not None
                      else await engine.repository.latest(workflow_id))
        report = await engine.submit(definition.ref, data.input)
        response.headers["Location"] = "/api/v1/workflow-runs/" + report.run.id
        return jsonable_encoder(asdict(report))

    @router.get("/workflow-runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        return jsonable_encoder(asdict(await engine.get(run_id)))

    @router.get("/workflow-runs/{run_id}/nodes")
    async def get_nodes(run_id: str) -> list[dict[str, Any]]:
        return [jsonable_encoder(asdict(node)) for node in (await engine.get(run_id)).nodes]

    @router.get("/workflow-runs/{run_id}/nodes/{node_id}")
    async def get_node(run_id: str, node_id: str) -> dict[str, Any]:
        node = next((node for node in (await engine.get(run_id)).nodes if node.node_id == node_id), None)
        if node is None:
            raise RuntimeFault("workflow_node_not_found", "Node run not found", 404)
        return jsonable_encoder(asdict(node))

    @router.get("/workflow-runs/{run_id}/events")
    async def get_events(run_id: str, after: Annotated[int, Query(ge=0)] = 0) -> list[dict[str, Any]]:
        return [jsonable_encoder(asdict(event)) for event in (await engine.get(run_id)).events if event.sequence > after]

    @router.post("/workflow-runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict[str, Any]:
        return jsonable_encoder(asdict(await engine.cancel(run_id)))

    return router
