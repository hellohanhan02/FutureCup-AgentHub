"""Seeded SIMULATION benchmark. No real LLM, token billing or quality claims.

All policies execute the same JSON task set through WorkflowEngine and Runtime.
Latent duration/failure draws live only in the Mock provider; policies cannot read them.
"""

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
from time import perf_counter

from futurecup.adapters.scheduling_store import SQLiteSchedulingRepository
from futurecup.adapters.workflow_store import SQLiteWorkflowRepository, JsonSchemaWorkflowValidator
from futurecup.application.ports import ModelResponse
from futurecup.application.scheduling import SchedulingCoordinator
from futurecup.application.workflow import WorkflowEngine
from futurecup.bootstrap import build_runtime
from futurecup.config import Settings
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import AgentDefinition, NodeKind, VersionRef, WorkflowDefinition, WorkflowNode
from futurecup.domain.scheduling import CAASConfig

SCENARIOS = ("similar", "capability_difference", "mixed_priority", "changing_load", "failures", "concurrent", "no_qualified")
POLICIES = ("fixed", "round_robin", "caas")


def draw(seed: int, *parts: str) -> float:
    digest = hashlib.sha256((str(seed) + ":" + ":".join(parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2 ** 64)


def workload(scenario: str, seed: int, count: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    agents = []
    for identifier in ("agent-a", "agent-b", "agent-c"):
        latency = {kind: (.006 if scenario == "similar" else rng.uniform(.003, .018)) for kind in ("analysis", "writing")}
        failure = {kind: (rng.uniform(.15, .45) if scenario == "failures" else .02) for kind in latency}
        skills = ["analysis", "writing"] if scenario != "capability_difference" else rng.sample(["analysis", "writing"], rng.randint(1, 2))
        agents.append({"id": identifier, "skills": skills, "latent_latency": latency, "latent_failure": failure,
                       "declared_cost": rng.choice([None, .5, 1.0, 2.0])})
    tasks = []
    for index in range(count):
        kind = rng.choice(["analysis", "writing"])
        tasks.append({"id": "task-" + str(index), "task_type": kind,
            "required_skills": ["missing-skill"] if scenario == "no_qualified" else ([kind] if scenario == "capability_difference" else []),
            "preferred_skills": [kind], "priority": rng.choice([0, 20, 80, 100]) if scenario == "mixed_priority" else 50})
    rng.shuffle(tasks)
    return agents, tasks


class SimulatedProvider:
    is_simulated = True
    def __init__(self, seed: int, agents: list[dict]) -> None:
        self.seed = seed
        self.agents = {agent["id"]: agent for agent in agents}

    async def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("background"):
            await asyncio.sleep(.12)
            return ModelResponse("[MOCK SIMULATION] background", (), 0, 0, None, False)
        identifier, kind, task_id = request.model, payload["task_type"], payload["id"]
        private = self.agents[identifier]
        duration = private["latent_latency"][kind] * (.8 + .4 * draw(self.seed, task_id, identifier, "delay"))
        await asyncio.sleep(duration)
        if draw(self.seed, task_id, identifier, "failure") < private["latent_failure"][kind]:
            raise RuntimeFault("simulated_model_failure", "Explicit simulated benchmark failure")
        return ModelResponse("[MOCK SIMULATION] completed", (), 0, 0, None, False)


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[(95 * len(values) + 99) // 100 - 1]


def metrics(raw: list[dict], seconds: float, planning_ms: float, passes: int) -> dict:
    count = len(raw)
    latencies = [record["latency_ms"] for record in raw]
    loads: dict[str, int] = {}
    for record in raw:
        if record["agent_id"]:
            loads[record["agent_id"]] = loads.get(record["agent_id"], 0) + 1
    successes = sum(record["state"] == "succeeded" for record in raw)
    failures = sum(record["state"] == "failed" for record in raw)
    return {"tasks": count, "success_rate": successes / count, "failure_rate": failures / count,
        "mean_latency_ms": sum(latencies) / count, "p95_latency_ms": percentile95(latencies),
        "terminal_throughput_per_second": count / seconds, "successful_throughput_per_second": successes / seconds,
        "mean_waiting_ms": sum(record["waiting_ms"] for record in raw) / count,
        "agent_assignment_counts": loads, "scheduling_total_ms": planning_ms,
        "scheduling_passes": passes, "mean_scheduling_pass_ms": planning_ms / passes if passes else None,
        "makespan_seconds": seconds}


async def trial(scenario: str, strategy: str, seed: int, count: int) -> dict:
    declarations, tasks = workload(scenario, seed, count)
    runtime = build_runtime(Settings(environment="simulation", database_url="sqlite:///:memory:", enable_real_llm=False))
    runtime.providers["mock"] = SimulatedProvider(seed, declarations)
    repository = SQLiteWorkflowRepository()
    store = SQLiteSchedulingRepository(repository)
    scheduler = SchedulingCoordinator(runtime, store, history_source="simulation")
    engine = WorkflowEngine(runtime, repository, JsonSchemaWorkflowValidator(), scheduler=scheduler)
    try:
        for data in declarations:
            await runtime.registry.register(AgentDefinition(VersionRef(data["id"], "1"), "simulation", tuple(data["skills"]), (),
                "simulation", "simulation", "builtin-safe-only", 2, name=data["id"],
                responsibilities="Simulated benchmark only", system_prompt="Mock simulation; no real inference",
                provider="mock", model=data["id"], task_types=("analysis", "writing"), estimated_cost=data["declared_cost"]))
        # Same disjoint warmup workload for all policies; no evaluation outcomes are preloaded.
        warmup = 0 if scenario in {"similar", "no_qualified"} else 2
        for data in declarations:
            for kind in ("analysis", "writing"):
                for index in range(warmup):
                    result = await runtime.run(data["id"], json.dumps({"id": f"warmup-{data['id']}-{kind}-{index}", "task_type": kind}))
                    await scheduler.observe(result, kind)
        background = None
        if scenario == "changing_load":
            background = await runtime.submit("agent-a", json.dumps({"background": True}))
        specs = tuple(WorkflowNode(task["id"], NodeKind.TASK, {
            "candidate_agents": [data["id"] for data in declarations], "task_type": task["task_type"],
            "required_skills": task["required_skills"], "preferred_skills": task["preferred_skills"],
            "priority": task["priority"], "schedule_timeout_seconds": 10,
            "input": {"id": task["id"], "task_type": task["task_type"]},
        }) for task in tasks)
        definition = WorkflowDefinition(VersionRef("benchmark", "1"), specs, (), {}, {}, 4, name="SIMULATED benchmark",
            max_concurrency=8, max_node_executions=64, timeout_seconds=30, scheduling_strategy=strategy)
        await engine.register(definition)
        started = perf_counter()
        report = await engine.run(definition.ref, {})
        duration = perf_counter() - started
        records = await store.decisions(run_id=report.run.id, limit=1000)
        latest = {}
        for record in records:
            latest.setdefault(record["task_id"], record)
        raw = []
        for node in report.nodes:
            assert node.ready_at and node.ended_at
            decision = latest.get(node.id)
            raw.append({"id": node.node_id, "state": node.state.value, "agent_id": node.agent_ref.id if node.agent_ref else None,
                "latency_ms": (node.ended_at - node.ready_at).total_seconds() * 1000,
                "waiting_ms": node.waiting_ms if node.assigned_at else (decision["waiting_ms"] if decision else node.elapsed_ms),
                "error_code": node.error_code})
        for data in declarations:
            assert (await runtime.capacity(data["id"])).peak_active <= 2
        measured = metrics(raw, duration, scheduler.planning_ms, scheduler.planning_passes)
        if background:
            await runtime.wait(background.execution.id)
        return {"scenario": scenario, "strategy": strategy, "seed": seed,
            "mode": "SIMULATION; not real LLM performance", "workload_sha256": hashlib.sha256(json.dumps([declarations, tasks], sort_keys=True).encode()).hexdigest(),
            "declarations_and_latent_simulation_parameters": declarations, "tasks": tasks, "raw_outcomes": raw,
            "metrics": measured, "decisions": records, "warmup_per_agent_and_task_type": warmup,
            "capacity_safe": True}
    finally:
        await engine.close()
        await runtime.close()


async def main(args) -> None:
    root = Path(__file__).resolve().parents[1]
    trials = []
    for scenario in SCENARIOS:
        for repeat in range(args.repeats):
            seed = args.seed + repeat
            # Predeclared rotation balances execution order; never chosen from outcomes.
            order = POLICIES[repeat % 3:] + POLICIES[:repeat % 3]
            for strategy in order:
                item = await trial(scenario, strategy, seed, args.tasks)
                trials.append(item)
        for seed in range(args.seed, args.seed + args.repeats):
            assert len({item["workload_sha256"] for item in trials if item["scenario"] == scenario and item["seed"] == seed}) == 1
        print('Completed simulation scenario:', scenario, flush=True)
    summaries = []
    for scenario in SCENARIOS:
        for strategy in POLICIES:
            selected = [item for item in trials if item["scenario"] == scenario and item["strategy"] == strategy]
            raw = [record for item in selected for record in item["raw_outcomes"]]
            summary = metrics(raw, sum(item["metrics"]["makespan_seconds"] for item in selected),
                sum(item["metrics"]["scheduling_total_ms"] for item in selected), sum(item["metrics"]["scheduling_passes"] for item in selected))
            summaries.append({"scenario": scenario, "strategy": strategy, **summary})
            print(scenario, strategy, 'success=', round(summary['success_rate'], 3), 'mean_ms=', round(summary['mean_latency_ms'], 2),
                  'p95_ms=', round(summary['p95_latency_ms'], 2), flush=True)
    files = [str(path.relative_to(root)).replace('\\', '/') for path in (root / 'backend/src/futurecup').rglob('*.py')]
    files.append('benchmarks/run_scheduler_benchmark.py')
    output = {"mode": "SIMULATION ONLY", "seed": args.seed, "repeats": args.repeats, "tasks_per_trial": args.tasks,
        "python": sys.version, "algorithm_config": asdict(CAASConfig()),
        "sqlite_version": sqlite3.sqlite_version,
        "package_versions": {name: importlib.metadata.version(name) for name in ('fastapi', 'uvicorn', 'httpx', 'jsonschema', 'pytest')},
        "git_base": subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        "source_sha256": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in files},
        "statistics": "All terminal tasks included; P95 nearest rank; throughput=sum terminal tasks/sum makespans; warmup excluded; failures not dropped",
        "summaries": summaries, "trials": trials}
    (root / '.data').mkdir(exist_ok=True)
    (root / '.data/scheduler-05-results.json').write_text(json.dumps(output, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261111)
    parser.add_argument('--tasks', type=int, choices=range(1, 49), default=24)
    parser.add_argument('--repeats', type=int, choices=range(1, 6), default=3)
    asyncio.run(main(parser.parse_args()))
