"""Versioned management and execution DTOs; never accept provider URLs or keys."""

from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from futurecup.application.runtime import AgentRuntimeService
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import AgentDefinition, Execution, ModelCallRecord, RunLimits, RunReport, VersionRef

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=2048)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ToolRefInput(StrictModel):
    id: Identifier
    version: Identifier = "1"


class AgentConfigInput(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=128)]
    role: Text
    responsibilities: Text
    system_prompt: Annotated[str, Field(min_length=1, max_length=8192)]
    skills: list[Identifier] = Field(default_factory=list, max_length=32)
    provider: Identifier = "mock"
    model: Annotated[str, Field(min_length=1, max_length=128)] = "mock-echo"
    tools: list[ToolRefInput] = Field(default_factory=list, max_length=16)
    version: Identifier = "1"
    enabled: bool = True
    max_concurrency: int = Field(default=1, ge=1, le=16, strict=True)
    task_types: list[str] = Field(default_factory=lambda: ["*"], min_length=1, max_length=32)
    available: bool = True
    estimated_cost: float | None = Field(default=None, ge=0)
    estimated_latency_ms: float | None = Field(default=None, ge=0)
    response_format: Literal["text", "prompt_json", "json_object"] = "text"
    knowledge_base_ids: list[Identifier] = Field(default_factory=list,max_length=5)

    def definition(self, agent_id: str) -> AgentDefinition:
        return AgentDefinition(
            ref=VersionRef(agent_id, self.version), role=self.role, skills=tuple(self.skills),
            tool_refs=tuple(VersionRef(tool.id, tool.version) for tool in self.tools),
            model_profile=self.provider + ":" + self.model,
            memory_namespace="agent:" + agent_id, permission_profile="builtin-safe-only",
            max_concurrency=self.max_concurrency, name=self.name,
            responsibilities=self.responsibilities, system_prompt=self.system_prompt,
            provider=self.provider, model=self.model, enabled=self.enabled,
            task_types=tuple(self.task_types), available=self.available,
            estimated_cost=self.estimated_cost, estimated_latency_ms=self.estimated_latency_ms,
            response_format=self.response_format,
            knowledge_base_ids=tuple(self.knowledge_base_ids),
        )


class AgentCreate(AgentConfigInput):
    id: Identifier


class AgentUpdate(AgentConfigInput):
    expected_version: Identifier


class AgentEdit(AgentUpdate):
    system_prompt: Annotated[str | None, Field(min_length=1, max_length=8192)] = None


class AgentResponse(AgentCreate):
    system_prompt: str = Field(exclude=True)


class LimitsInput(StrictModel):
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    model_timeout_seconds: float = Field(default=10, gt=0, le=300)
    tool_timeout_seconds: float = Field(default=5, gt=0, le=300)
    max_calls: int = Field(default=8, ge=1, le=64, strict=True)
    max_model_calls: int = Field(default=4, ge=1, le=32, strict=True)
    max_tool_calls: int = Field(default=4, ge=1, le=32, strict=True)
    max_output_tokens: int = Field(default=1024, ge=1, le=4096, strict=True)


class ExecuteInput(StrictModel):
    input: Annotated[str, Field(min_length=1, max_length=8192)]
    limits: LimitsInput = Field(default_factory=LimitsInput)


class ExecutionResponse(BaseModel):
    execution: Execution
    agent_name: str
    provider: str
    model: str
    simulated: bool
    input: str
    limits: RunLimits
    output: str | None
    elapsed_ms: float
    model_calls: list[ModelCallRecord]
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    usage_complete: bool
    call_count: int
    resolved_model: str | None
    provider_config_ref: VersionRef | None
    estimated_cost: float | None
    cost_currency: str | None
    cost_complete: bool
    cost_basis: str


def agent_response(agent: AgentDefinition) -> AgentResponse:
    return AgentResponse(
        id=agent.ref.id, version=agent.ref.version, name=agent.name,
        role=agent.role, responsibilities=agent.responsibilities,
        system_prompt=agent.system_prompt, skills=list(agent.skills),
        provider=agent.provider, model=agent.model,
        tools=[ToolRefInput(id=ref.id, version=ref.version) for ref in agent.tool_refs],
        enabled=agent.enabled, max_concurrency=agent.max_concurrency,
        task_types=list(agent.task_types), available=agent.available,
        estimated_cost=agent.estimated_cost, estimated_latency_ms=agent.estimated_latency_ms,
        response_format=agent.response_format,
        knowledge_base_ids=list(agent.knowledge_base_ids),
    )


def execution_response(report: RunReport) -> ExecutionResponse:
    return ExecutionResponse.model_validate({**asdict(report), "call_count": report.call_count})


def build_router(runtime: AgentRuntimeService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["agents"])

    @router.post("/agents", status_code=201, response_model=AgentResponse)
    async def create_agent(data: AgentCreate) -> AgentResponse:
        definition = data.definition(data.id)
        runtime.validate_agent_config(definition)
        await runtime.tools.definitions(definition.tool_refs)
        await runtime.registry.register(definition)
        return agent_response(await runtime.registry.get_current(data.id))

    @router.get("/agents", response_model=list[AgentResponse])
    async def list_agents() -> list[AgentResponse]:
        return [agent_response(agent) for agent in await runtime.registry.list_agents()]

    @router.get("/agents/{agent_id}", response_model=AgentResponse)
    async def get_agent(agent_id: str, version: str | None = None) -> AgentResponse:
        agent = (await runtime.registry.get(VersionRef(agent_id, version)) if version is not None
                 else await runtime.registry.get_current(agent_id))
        return agent_response(agent)

    @router.put("/agents/{agent_id}", response_model=AgentResponse)
    async def update_agent(agent_id: Identifier, data: AgentUpdate) -> AgentResponse:
        definition = data.definition(agent_id)
        runtime.validate_agent_config(definition)
        await runtime.tools.definitions(definition.tool_refs)
        await runtime.registry.update(definition, data.expected_version)
        return agent_response(await runtime.registry.get_current(agent_id))

    @router.patch("/agents/{agent_id}", response_model=AgentResponse)
    async def edit_agent(agent_id: Identifier, data: AgentEdit) -> AgentResponse:
        current = await runtime.registry.get_current(agent_id)
        values = data.model_dump()
        values["system_prompt"] = data.system_prompt if data.system_prompt is not None else current.system_prompt
        definition = AgentUpdate.model_validate(values).definition(agent_id)
        runtime.validate_agent_config(definition)
        await runtime.tools.definitions(definition.tool_refs)
        await runtime.registry.update(definition, data.expected_version)
        return agent_response(await runtime.registry.get_current(agent_id))

    @router.post("/agents/{agent_id}/disable", response_model=AgentResponse)
    async def disable_agent(agent_id: str) -> AgentResponse:
        return agent_response(await runtime.registry.set_enabled(agent_id, False))

    @router.post("/agents/{agent_id}/executions", status_code=202, response_model=ExecutionResponse)
    async def execute(agent_id: str, data: ExecuteInput, response: Response) -> ExecutionResponse:
        report = await runtime.submit(agent_id, data.input, RunLimits(**data.limits.model_dump()))
        response.headers["Location"] = "/api/v1/executions/" + report.execution.id
        return execution_response(report)

    @router.get("/executions/{execution_id}", response_model=ExecutionResponse)
    async def get_execution(execution_id: str) -> ExecutionResponse:
        return execution_response(await runtime.get(execution_id))

    @router.post("/executions/{execution_id}/cancel", response_model=ExecutionResponse)
    async def cancel_execution(execution_id: str) -> ExecutionResponse:
        return execution_response(await runtime.cancel(execution_id))

    return router
