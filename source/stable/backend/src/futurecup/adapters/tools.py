"""Trusted, side-effect-free async tools. Never eval, exec, shell or arbitrary files."""

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


class InMemoryToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[VersionRef, tuple[ToolDefinition, Callable[..., Awaitable[JsonValue]]]] = {}

    def register(self, definition: ToolDefinition, handler: Callable[..., Awaitable[JsonValue]]) -> None:
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
    ), calculate)
    return registry
