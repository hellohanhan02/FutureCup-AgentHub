"""Single-event-loop, process-local adapters. No persistence or multi-worker safety."""

from copy import deepcopy
from dataclasses import replace
import math
from uuid import uuid4

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import AgentDefinition, AgentRuntime, RunReport, VersionRef


class InMemoryAgentRegistry:
    def __init__(self) -> None:
        self._definitions: dict[VersionRef, AgentDefinition] = {}
        self._current: dict[str, VersionRef] = {}
        self._enabled: dict[str, bool] = {}
        self._instances: dict[str, AgentRuntime] = {}

    def _validate(self, definition: AgentDefinition) -> None:
        required = (definition.ref.id, definition.ref.version, definition.name,
                    definition.role, definition.responsibilities, definition.system_prompt,
                    definition.provider, definition.model)
        if not all(isinstance(value, str) and value.strip() for value in required):
            raise RuntimeFault("invalid_agent", "Agent configuration contains empty fields", 422)
        if type(definition.max_concurrency) is not int or not 1 <= definition.max_concurrency <= 64:
            raise RuntimeFault("invalid_agent", "Invalid concurrency limit", 422)
        if len({ref.id for ref in definition.tool_refs}) != len(definition.tool_refs):
            raise RuntimeFault("invalid_agent", "Tool names must be unique", 422)
        if not definition.task_types or len(definition.task_types) > 32 or not all(
                isinstance(tag, str) and 0 < len(tag) <= 64 and (tag == "*" or all(c.isascii() and (c.isalnum() or c in "_.-") for c in tag))
                for tag in definition.task_types):
            raise RuntimeFault("invalid_agent", "Invalid supported task types", 422)
        for estimate in (definition.estimated_cost, definition.estimated_latency_ms):
            if estimate is not None and (isinstance(estimate, bool) or not isinstance(estimate, (int, float))
                                         or not math.isfinite(estimate) or estimate < 0):
                raise RuntimeFault("invalid_agent", "Cost/latency estimates must be finite and nonnegative", 422)

    async def register(self, definition: AgentDefinition) -> None:
        self._validate(definition)
        if definition.ref.id in self._current:
            raise RuntimeFault("agent_exists", "Agent ID already registered", 409)
        self._definitions[definition.ref] = deepcopy(definition)
        self._current[definition.ref.id] = definition.ref
        self._enabled[definition.ref.id] = definition.enabled

    async def get(self, ref: VersionRef) -> AgentDefinition:
        if ref not in self._definitions:
            raise RuntimeFault("agent_not_found", "Agent version not found", 404)
        return deepcopy(self._definitions[ref])

    async def get_current(self, agent_id: str) -> AgentDefinition:
        if agent_id not in self._current:
            raise RuntimeFault("agent_not_found", "Agent not found", 404)
        return replace(await self.get(self._current[agent_id]), enabled=self._enabled[agent_id])

    async def list_agents(self) -> tuple[AgentDefinition, ...]:
        return tuple([await self.get_current(key) for key in sorted(self._current)])

    async def discover(self, skills: tuple[str, ...]) -> tuple[AgentDefinition, ...]:
        return tuple(agent for agent in await self.list_agents()
                     if agent.enabled and set(skills).issubset(agent.skills))

    async def update(self, definition: AgentDefinition, expected_version: str) -> None:
        self._validate(definition)
        current = await self.get_current(definition.ref.id)
        if current.ref.version != expected_version:
            raise RuntimeFault("version_conflict", "Agent version changed", 409)
        if definition.ref in self._definitions:
            raise RuntimeFault("version_exists", "An immutable version cannot be overwritten", 409)
        self._definitions[definition.ref] = deepcopy(definition)
        self._current[definition.ref.id] = definition.ref
        self._enabled[definition.ref.id] = definition.enabled

    async def set_enabled(self, agent_id: str, enabled: bool) -> AgentDefinition:
        await self.get_current(agent_id)
        self._enabled[agent_id] = enabled
        return await self.get_current(agent_id)

    async def heartbeat(self, runtime: AgentRuntime, expected_revision: int) -> bool:
        await self.get(runtime.agent_ref)
        old = self._instances.get(runtime.instance_id)
        if (old.revision if old else 0) != expected_revision:
            return False
        if runtime.revision != expected_revision + 1:
            return False
        self._instances[runtime.instance_id] = deepcopy(runtime)
        return True


class InMemoryExecutionRepository:
    def __init__(self, capacity: int = 1000) -> None:
        self._reports: dict[str, RunReport] = {}
        self._capacity = capacity

    async def add(self, report: RunReport) -> None:
        if len(self._reports) >= self._capacity:
            raise RuntimeFault("execution_capacity", "Process execution retention limit reached", 409)
        key = report.execution.id
        if key in self._reports:
            raise RuntimeFault("execution_exists", "Execution ID already exists", 409)
        self._reports[key] = deepcopy(report)

    async def get(self, execution_id: str) -> RunReport:
        if execution_id not in self._reports:
            raise RuntimeFault("execution_not_found", "Execution not found", 404)
        return deepcopy(self._reports[execution_id])

    async def save(self, report: RunReport) -> None:
        if report.execution.id not in self._reports:
            raise RuntimeFault("execution_not_found", "Execution not found", 404)
        self._reports[report.execution.id] = deepcopy(report)


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._items: dict[str, bytes] = {}

    async def put(self, content: bytes, media_type: str) -> str:
        ref = "memory://" + uuid4().hex
        self._items[ref] = content
        return ref

    async def get(self, ref: str) -> bytes:
        if ref not in self._items:
            raise RuntimeFault("artifact_not_found", "Artifact not found", 404)
        return self._items[ref]
