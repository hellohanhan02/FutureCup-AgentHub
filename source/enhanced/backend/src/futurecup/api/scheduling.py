"""Read-only scheduling diagnostics; no task content or private Agent prompts."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.domain.models import VersionRef


def build_scheduling_router(scheduler: SchedulingCoordinator) -> APIRouter:
    router = APIRouter(prefix="/api/v1/scheduling", tags=["scheduling"])

    async def profile_view(ref: VersionRef, task_type: str) -> dict:
        profile = await scheduler.profile(ref, task_type)
        value = asdict(profile)
        count = profile.stats.sample_count
        value["stats"].update(sample_count=count, empirical_success_rate=profile.stats.empirical_success_rate)
        value["confidence"] = count / (count + scheduler.config.prior_samples)
        value["estimated_reliability"] = (profile.stats.successes + scheduler.config.prior_samples * .5) / (count + scheduler.config.prior_samples)
        value["low_samples"] = count < scheduler.config.prior_samples
        return jsonable_encoder(value)

    @router.get("/strategies")
    async def strategies(include_adaptive: bool = False) -> dict:
        return {"strategies": [key for key in scheduler.policies if include_adaptive or key != "adaptive_caas"],
                "available_strategies":list(scheduler.policies), "adaptive_config":asdict(scheduler.adaptive_config), "config": asdict(scheduler.config),
                "outcome_semantics": "execution_only_not_verified", "cost_units": "declared_relative_units"}

    @router.get("/agents")
    async def profiles(task_type: Annotated[str, Query(min_length=1, max_length=64)] = "default") -> list[dict]:
        return [await profile_view(agent.ref, task_type) for agent in await scheduler.runtime.registry.list_agents()]

    @router.get("/agents/{agent_id}/history")
    async def history(agent_id: str, version: str | None = None) -> list[dict]:
        agent = await scheduler.runtime.registry.get_current(agent_id)
        ref = VersionRef(agent_id, version) if version is not None else agent.ref
        await scheduler.runtime.registry.get(ref)
        return [jsonable_encoder({**asdict(stats), "sample_count": stats.sample_count,
                                  "empirical_success_rate": stats.empirical_success_rate})
                for stats in await scheduler.repository.history(ref)]

    @router.get("/agents/{agent_id}")
    async def profile(agent_id: str, task_type: Annotated[str, Query(min_length=1, max_length=64)] = "default",
                      version: str | None = None) -> dict:
        agent = await scheduler.runtime.registry.get_current(agent_id)
        return await profile_view(VersionRef(agent_id, version) if version is not None else agent.ref, task_type)

    @router.get("/decisions")
    async def decisions(task_id: str | None = None, run_id: str | None = None,
                        limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> list[dict]:
        return list(await scheduler.repository.decisions(task_id, run_id, limit))

    return router
