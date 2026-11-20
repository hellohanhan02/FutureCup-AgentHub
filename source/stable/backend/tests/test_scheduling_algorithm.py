"""Deterministic formula/constraint tests with explicit synthetic profiles."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from futurecup.application.scheduling import assess, effective_priority, FixedSelection, RoundRobinSelection, CAASSelection
from futurecup.domain.models import VersionRef
from futurecup.domain.scheduling import CAASConfig, CapabilityProfile, CapacitySnapshot, PerformanceStats
from scheduling_support import request

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def profile(identifier="analyst", **values):
    original = CapabilityProfile(VersionRef(identifier, "1"), ("analysis",), ("analysis",), (),
        True, True, True, True, CapacitySnapshot(0, 2, 0, 64, 0), None, None,
        PerformanceStats("analysis", "simulation"), "synthetic-v1", NOW)
    return replace(original, **values)


@pytest.mark.parametrize("change,reason", [
    ({"skills": ()}, "skills_mismatch"), ({"task_types": ("other",)}, "task_type_mismatch"),
    ({"enabled": False}, "agent_disabled"), ({"available": False}, "agent_unavailable"),
    ({"provider_available": False}, "provider_unavailable"), ({"tools_available": False}, "tools_unavailable"),
    ({"capacity": CapacitySnapshot(2, 2, 2, 64, 1)}, "agent_capacity"),
    ({"capacity": CapacitySnapshot(0, 2, 64, 64, 1)}, "runtime_capacity"),
])
def test_hard_candidate_constraints(change, reason):
    task = request(task_type="analysis", required_skills=("analysis",))
    result = assess(task, profile(**change), CAASConfig())
    assert not result.eligible and reason in result.reasons and result.score is None


def test_tool_whitelist_requires_exact_version_and_dependencies_must_be_ready():
    task = request(task_type="analysis", required_tools=(VersionRef("calculator", "1"),))
    assert "tool_permission_mismatch" in assess(task, profile(), CAASConfig()).reasons
    assert "tool_permission_mismatch" in assess(task, profile(tools=(VersionRef("calculator", "2"),)), CAASConfig()).reasons
    assert assess(task, profile(tools=(VersionRef("calculator", "1"),)), CAASConfig()).eligible
    assert "dependencies_not_ready" in assess(replace(task, dependencies_satisfied=False), profile(), CAASConfig()).reasons


def test_cold_start_is_unknown_and_small_samples_are_shrunk():
    config = CAASConfig()
    task = request(task_type="analysis")
    cold = profile()
    assert cold.stats.empirical_success_rate is None and cold.stats.sample_count == 0
    cold_score = assess(task, cold, config)
    assert cold_score.components["reliability"] == .5
    one = replace(cold, stats=PerformanceStats("analysis", "simulation", successes=1, revision=1))
    assert .5 < assess(task, one, config).components["reliability"] < .6
    for value in cold_score.components.values():
        assert 0 <= value <= 1


def test_priority_dependency_bonus_and_aging_allow_old_low_priority_to_overtake():
    config = CAASConfig()
    recent = request("high", priority=100, ready_at=NOW)
    older = request("old", priority=0, ready_at=NOW - timedelta(seconds=22))
    assert effective_priority(older, NOW, config) > effective_priority(recent, NOW, config)
    assert effective_priority(replace(recent, dependent_count=3), NOW, config) == 100.3


def test_caas_fixed_ties_are_deterministic_and_round_robin_rotates():
    task = request(task_type="analysis")
    a = assess(task, profile("a"), CAASConfig())
    b = assess(task, profile("b"), CAASConfig())
    assert CAASSelection().select((b, a)) == CAASSelection().select((a, b)) == "a"
    assert FixedSelection().select((b, a)) == "a"
    robin = RoundRobinSelection()
    assert [robin.select((b, a)) for _ in range(4)] == ["a", "b", "a", "b"]


def test_profile_scores_use_only_supplied_predecision_data():
    task = request(task_type="analysis", preferred_skills=("analysis",))
    snapshot = profile(estimated_cost=1, declared_latency_ms=100)
    first = assess(task, snapshot, CAASConfig())
    future = replace(snapshot, stats=PerformanceStats("analysis", "simulation", failures=10, revision=10))
    assert assess(task, future, CAASConfig()).score < first.score
    assert assess(task, snapshot, CAASConfig()) == first


@pytest.mark.parametrize("values", [{"match_weight": 2}, {"load_weight": float("nan")},
                                     {"match_weight": 0}, {"cost_scale": 0}, {"aging_points_per_second": -1}])
def test_algorithm_parameters_are_validated(values):
    with pytest.raises(ValueError):
        CAASConfig(**values)
