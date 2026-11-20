"""Real registry tests with configurations explicitly targeting the Mock provider."""

import asyncio
from dataclasses import replace

import pytest

from futurecup.adapters.memory import InMemoryAgentRegistry
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import VersionRef
from runtime_support import agent


def test_register_distinct_roles_and_discover():
    async def scenario():
        registry = InMemoryAgentRegistry()
        await registry.register(agent("researcher"))
        await registry.register(agent("reviewer", role="reviewer", skills=("review",), model="mock-calculator"))
        assert len(await registry.list_agents()) == 2
        reviewer = await registry.get_current("reviewer")
        assert reviewer.role == "reviewer" and reviewer.model == "mock-calculator"
        assert reviewer.system_prompt == "You are reviewer"
        assert [a.ref.id for a in await registry.discover(("review",))] == ["reviewer"]
        await registry.set_enabled("reviewer", False)
        assert await registry.discover(("review",)) == ()
    asyncio.run(scenario())


def test_duplicate_id_rejected_even_with_another_version():
    async def scenario():
        registry = InMemoryAgentRegistry()
        await registry.register(agent())
        with pytest.raises(RuntimeFault, match="already registered") as error:
            await registry.register(agent(ref=VersionRef("analyst", "2")))
        assert error.value.code == "agent_exists"
    asyncio.run(scenario())


def test_version_updates_are_immutable_and_conflicts_rejected():
    async def scenario():
        registry = InMemoryAgentRegistry()
        original = agent()
        await registry.register(original)
        updated = replace(original, ref=VersionRef("analyst", "2"), role="reviewer")
        await registry.update(updated, "1")
        assert (await registry.get(original.ref)).role == "analyst"
        assert (await registry.get_current("analyst")).role == "reviewer"
        with pytest.raises(RuntimeFault) as error:
            await registry.update(replace(updated, ref=VersionRef("analyst", "3")), "1")
        assert error.value.code == "version_conflict"
        with pytest.raises(RuntimeFault) as error:
            await registry.update(updated, "2")
        assert error.value.code == "version_exists"
        await registry.set_enabled("analyst", False)
        assert not (await registry.get_current("analyst")).enabled
        assert (await registry.get(original.ref)).enabled
    asyncio.run(scenario())


def test_registry_rejects_empty_configuration():
    async def scenario():
        with pytest.raises(RuntimeFault) as error:
            await InMemoryAgentRegistry().register(agent(system_prompt=" "))
        assert error.value.code == "invalid_agent"
    asyncio.run(scenario())
