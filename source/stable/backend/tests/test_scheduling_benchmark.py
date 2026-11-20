"""Reproducibility and metric definitions for the explicit simulation harness."""

import importlib.util
from pathlib import Path


def benchmark_module():
    path = Path(__file__).resolve().parents[2] / "benchmarks/run_scheduler_benchmark.py"
    spec = importlib.util.spec_from_file_location("scheduler_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seeded_workload_and_pairwise_failure_draws_are_reproducible():
    benchmark = benchmark_module()
    for scenario in benchmark.SCENARIOS:
        a = benchmark.workload(scenario, 20261111, 24)
        b = benchmark.workload(scenario, 20261111, 24)
        assert a == b
        assert len(a[1]) == 24
        assert benchmark.draw(5, "task", "agent", "failure") == benchmark.draw(5, "task", "agent", "failure")
    assert benchmark.workload("failures", 1, 24) != benchmark.workload("failures", 2, 24)


def test_metric_denominators_include_failed_and_unassigned_tasks():
    benchmark = benchmark_module()
    raw = [
        {"state": "succeeded", "agent_id": "a", "latency_ms": 10, "waiting_ms": 2},
        {"state": "failed", "agent_id": None, "latency_ms": 20, "waiting_ms": 4},
    ]
    metrics = benchmark.metrics(raw, 2, 3, 2)
    assert metrics["success_rate"] == metrics["failure_rate"] == .5
    assert metrics["mean_latency_ms"] == 15 and metrics["p95_latency_ms"] == 20
    assert metrics["terminal_throughput_per_second"] == 1
    assert metrics["successful_throughput_per_second"] == .5
    assert metrics["mean_waiting_ms"] == 3
    assert metrics["agent_assignment_counts"] == {"a": 1}
