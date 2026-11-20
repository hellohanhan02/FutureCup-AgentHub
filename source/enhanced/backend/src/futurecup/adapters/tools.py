"""Trusted, side-effect-free async tools. Never eval, exec, shell or arbitrary files."""

import asyncio
from contextlib import suppress
from hashlib import sha256
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from typing import Awaitable, Callable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import JsonValue, VersionRef


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    ref: VersionRef
    description: str
    input_schema: dict[str, JsonValue]
    retry_safety: str = "unknown"


class InMemoryToolRegistry:
    def __init__(self) -> None:
        self._replays = {}
        self._tools: dict[VersionRef, tuple[ToolDefinition, Callable[..., Awaitable[JsonValue]]]] = {}

    def register(self, definition: ToolDefinition, handler: Callable[..., Awaitable[JsonValue]]) -> None:
        if definition.retry_safety not in {"none","idempotent","irreversible","unknown"}: raise ValueError("Invalid retry safety")
        if definition.ref in self._tools:
            raise RuntimeFault("tool_exists", "Tool version already registered", 409)
        Draft202012Validator.check_schema(definition.input_schema)
        self._tools[definition.ref] = (deepcopy(definition), handler)

    async def definitions(self, allowed: tuple[VersionRef, ...]) -> tuple[dict[str, JsonValue], ...]:
        result: list[dict[str, JsonValue]] = []
        for ref in allowed:
            if ref not in self._tools:
                raise RuntimeFault("tool_not_found", "Configured tool version not registered", 422)
            definition = self._tools[ref][0]
            result.append({"type": "function", "function": {
                "name": ref.id, "description": definition.description,
                "parameters": deepcopy(definition.input_schema),
            }})
        return tuple(result)

    async def replay_definition(self, allowed: tuple[VersionRef, ...], name: str) -> ToolDefinition:
        ref = next((ref for ref in allowed if ref.id == name), None)
        if ref is None or ref not in self._tools:
            raise RuntimeFault("tool_unauthorized", "Tool not available in approved whitelist", 403)
        return deepcopy(self._tools[ref][0])

    def retry_safe(self, allowed: tuple[VersionRef, ...]) -> bool:
        return all(ref in self._tools and self._tools[ref][0].retry_safety in {'none','idempotent'} for ref in allowed)

    def has_replay_scope(self, scope: str) -> bool:
        return any(key[0]==scope for key in self._replays)

    def resume_safe(self, allowed, scope):
        for ref in allowed:
            if ref not in self._tools: return False
            safety=self._tools[ref][0].retry_safety
            entries=[v for k,v in self._replays.items() if k[0]==scope and k[1]==ref]
            if safety=='none': continue
            if safety!='idempotent' or not entries or any(not v.done() or v.cancelled() or v.exception() for v in entries): return False
        return True

    async def invoke_replay(self, allowed, name, arguments, scope):
        ref = next((ref for ref in allowed if ref.id==name),None)
        if ref is None or ref not in self._tools: return await self.invoke(allowed,name,arguments)
        if self._tools[ref][0].retry_safety != 'idempotent': return await self.invoke(allowed,name,arguments)
        arguments=deepcopy(arguments)
        key=(scope,ref,sha256(json.dumps(arguments,sort_keys=True,allow_nan=False).encode()).hexdigest())
        if any(k[0]==scope and k[1]==ref and k!=key for k in self._replays):
            raise RuntimeFault('tool_replay_conflict','Changed effect arguments require manual reconciliation')
        if key not in self._replays:
            # Install before any await: concurrent callers share one invocation.
            async def capture_result():
                return deepcopy(await self.invoke(allowed,name,arguments))
            self._replays[key]=asyncio.create_task(capture_result())
        task=self._replays[key]
        if task.cancelled() or task.done() and task.exception() is not None:
            raise RuntimeFault('tool_replay_uncertain','Prior invocation failed or was cancelled; manual reconciliation required')
        try: return deepcopy(await asyncio.shield(task))
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError,Exception): await task
            raise

    async def invoke(self, allowed: tuple[VersionRef, ...], name: str,
                     arguments: dict[str, JsonValue]) -> JsonValue:
        ref = next((item for item in allowed if item.id == name), None)
        if ref is None:
            raise RuntimeFault("tool_unauthorized", "Tool is not in the Agent whitelist", 403)
        if ref not in self._tools:
            raise RuntimeFault("tool_not_found", "Tool version not registered", 404)
        definition, handler = self._tools[ref]
        try:
            json.dumps(arguments, allow_nan=False)
            Draft202012Validator(definition.input_schema).validate(arguments)
        except (ValidationError, ValueError, TypeError):
            raise RuntimeFault("tool_arguments_invalid", "Tool arguments do not match the schema", 422) from None
        try:
            output = await handler(**deepcopy(arguments))
            serialized = json.dumps(output, allow_nan=False)
            if len(serialized) > 65536:
                raise ValueError("Tool output too large")
            return output
        except RuntimeFault:
            raise
        except Exception:
            raise RuntimeFault("tool_execution_failed", "Tool execution failed") from None


async def calculate(operation: str, a: float, b: float) -> JsonValue:
    operations = {
        "add": lambda: a + b,
        "subtract": lambda: a - b,
        "multiply": lambda: a * b,
        "divide": lambda: a / b,
    }
    value = operations[operation]()
    if not math.isfinite(value):
        raise ValueError("Non-finite result")
    return {"result": value}


def builtin_tools() -> InMemoryToolRegistry:
    registry = InMemoryToolRegistry()
    registry.register(ToolDefinition(
        VersionRef("calculator", "1"), "Arithmetic on two bounded finite numbers",
        {"type": "object", "properties": {
            "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
            "a": {"type": "number", "minimum": -1e12, "maximum": 1e12},
            "b": {"type": "number", "minimum": -1e12, "maximum": 1e12},
        }, "required": ["operation", "a", "b"], "additionalProperties": False},
        retry_safety="none",
    ), calculate)
    return registry
