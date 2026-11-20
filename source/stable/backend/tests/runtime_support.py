"""Explicit Mock helpers. No real model service is ever contacted."""

import asyncio
from dataclasses import replace

from futurecup.adapters.llm import MockLLMProvider
from futurecup.adapters.memory import InMemoryAgentRegistry, InMemoryArtifactStore, InMemoryExecutionRepository
from futurecup.adapters.tools import builtin_tools
from futurecup.application.ports import ModelResponse
from futurecup.application.runtime import AgentRuntimeService
from futurecup.domain.models import AgentDefinition, VersionRef


def agent(identifier="analyst", **changes):
    return replace(AgentDefinition(
        ref=VersionRef(identifier, "1"), name=identifier, role="analyst",
        responsibilities="Analyse the submitted task", system_prompt="You are " + identifier,
        skills=("analysis",), tool_refs=(), model_profile="mock:test", memory_namespace=identifier,
        permission_profile="builtin-safe-only", max_concurrency=1,
    ), **changes)


def response(content="[MOCK SIMULATED] done", calls=()):
    return ModelResponse(content, calls, 0, 0, None, False)


def tool_call(name="calculator", arguments=None, identifier="call_1"):
    return {"id": identifier, "name": name, "arguments": arguments if arguments is not None else
            {"operation": "add", "a": 2, "b": 3}}


class ScriptedMockProvider:
    is_simulated = True

    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        await asyncio.sleep(0)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def service(provider=None, capacity=1000):
    return AgentRuntimeService(InMemoryAgentRegistry(), InMemoryExecutionRepository(capacity),
                               {"mock": provider or MockLLMProvider()}, builtin_tools(), InMemoryArtifactStore())
