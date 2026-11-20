"""Bounded console read models and side-effect-free workflow validation."""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from futurecup.api.workflows import WorkflowInput
from futurecup.application.workflow import WorkflowEngine
from futurecup.domain.models import WorkflowState


def run_summary(report):
    return {"id": report.run.id, "workflow": asdict(report.run.definition_ref), "state": report.run.state.value,
            "started_at": report.started_at, "ended_at": report.ended_at, "elapsed_ms": report.elapsed_ms,
            "simulated": report.simulated, "verified": report.verified, "error_code": report.error_code,
            "nodes": len(report.nodes), "failed_nodes": sum(n.state.value == "failed" for n in report.nodes)}


def build_console_router(engine: WorkflowEngine) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["console"])

    @router.post("/workflows/validate")
    async def validate(data: WorkflowInput):
        definition = data.definition()
        await engine.validate(definition)
        return {"valid": True, "nodes": len(definition.nodes), "edges": len(definition.edges), "persisted": False}

    @router.get("/workflow-runs")
    async def recent(limit: Annotated[int, Query(ge=1, le=200)] = 100):
        await engine.start()
        return jsonable_encoder([run_summary(r) for r in await engine.repository.recent_reports(limit)])

    @router.get("/console/overview")
    async def overview():
        await engine.start()
        agents = await engine.runtime.registry.list_agents()
        definitions = await engine.repository.list_definitions()
        reports = await engine.repository.recent_reports(200)
        by_ref = {d.ref: d for d in definitions}
        strategies = {name: 0 for name in ("fixed", "round_robin", "caas")}
        for report in reports:
            definition = by_ref.get(report.run.definition_ref)
            if definition:
                strategies[definition.scheduling_strategy] += 1
        terminal = [r for r in reports if r.run.state in {WorkflowState.SUCCEEDED, WorkflowState.FAILED, WorkflowState.CANCELLED}]
        verification_counts = {name: 0 for name in ("PASSED", "FAILED", "INCONCLUSIVE", "ERROR")}
        if engine.selfloop:
            # Traverse child runs too so loops/subworkflows are represented in this root window.
            seen = set()
            async def collect(report):
                if report.run.id in seen:
                    return
                seen.add(report.run.id)
                for node in report.nodes:
                    for result in await engine.selfloop.repository.results(node.id):
                        key = {"pass": "PASSED", "fail": "FAILED", "inconclusive": "INCONCLUSIVE", "error": "ERROR"}[result["record"]["verdict"]]
                        verification_counts[key] += 1
                    for child in node.child_run_ids:
                        await collect(await engine.get(child))
            for report in reports:
                await collect(report)
        return jsonable_encoder({"scope": "latest_200_root_workflow_runs", "sample_size": len(reports),
            "agents": {"total": len(agents), "enabled": sum(a.enabled for a in agents), "available": sum(a.enabled and a.available for a in agents)},
            "workflows": {"total": len({d.ref.id for d in definitions}), "versions": len(definitions)},
            "runs": {"total": len(reports), "terminal": len(terminal), "succeeded": sum(r.run.state == WorkflowState.SUCCEEDED for r in terminal),
                     "active": sum(r.run.state in {WorkflowState.CREATED, WorkflowState.RUNNING, WorkflowState.PAUSED} for r in reports)},
            "verification": verification_counts, "strategies": strategies,
            "recent": [run_summary(r) for r in reports[:8]],
            "exceptions": [run_summary(r) for r in reports if r.error_code or any(n.error_code for n in r.nodes)][:8]})

    return router
