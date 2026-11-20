"""Public scheduling data contains no prompts, credentials or task payloads."""

from dataclasses import dataclass
from datetime import datetime
import math

from futurecup.domain.models import VersionRef


@dataclass(frozen=True, slots=True)
class CAASConfig:
    match_weight: float = .30
    reliability_weight: float = .25
    load_weight: float = .20
    cost_weight: float = .10
    latency_weight: float = .15
    cost_scale: float = 1
    latency_scale_ms: float = 1000
    prior_samples: float = 5
    aging_points_per_second: float = 5
    revision: str = "caas-v1"

    def __post_init__(self) -> None:
        weights = (self.match_weight, self.reliability_weight, self.load_weight, self.cost_weight, self.latency_weight)
        if any(isinstance(x, bool) or not math.isfinite(x) or not 0 <= x <= 1 for x in weights) or abs(sum(weights) - 1) > 1e-9:
            raise ValueError("Weights must be finite, within [0,1], and sum to 1")
        for value in (self.cost_scale, self.latency_scale_ms, self.prior_samples, self.aging_points_per_second):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Normalization scales, prior and aging rate must be positive and finite")


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    task_type: str
    source: str
    successes: int = 0
    failures: int = 0
    cancellations: int = 0
    mean_duration_ms: float | None = None
    revision: int = 0
    updated_at: datetime | None = None

    @property
    def sample_count(self) -> int:
        return self.successes + self.failures

    @property
    def empirical_success_rate(self) -> float | None:
        return self.successes / self.sample_count if self.sample_count else None


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    active: int
    capacity: int
    runtime_active: int
    runtime_capacity: int
    revision: int
    peak_active: int = 0


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    agent_ref: VersionRef
    task_types: tuple[str, ...]
    skills: tuple[str, ...]
    tools: tuple[VersionRef, ...]
    enabled: bool
    available: bool
    provider_available: bool
    tools_available: bool
    capacity: CapacitySnapshot
    estimated_cost: float | None
    declared_latency_ms: float | None
    stats: PerformanceStats
    profile_version: str
    observed_at: datetime
    configuration_source: str = "agent_definition"


@dataclass(frozen=True, slots=True)
class SchedulingTask:
    task_id: str
    workflow_run_id: str
    node_id: str
    candidate_ids: tuple[str, ...]
    candidate_refs: tuple[VersionRef, ...]
    ready_at: datetime
    task_type: str = "default"
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    required_tools: tuple[VersionRef, ...] = ()
    priority: int = 50
    dependent_count: int = 0
    dependencies_satisfied: bool = True
    strategy: str = "caas"
    no_candidate_policy: str = "fail"
    timeout_seconds: float = 10
    tool_subset: tuple[VersionRef, ...] | None = None
    features: "TaskFeatures | None" = None

    def __post_init__(self) -> None:
        if self.features is not None and not isinstance(self.features, TaskFeatures):
            raise ValueError('Expected validated TaskFeatures')


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    agent_id: str
    agent_version: str | None
    eligible: bool
    reasons: tuple[str, ...]
    components: dict[str, float]
    score: float | None
    profile: CapabilityProfile | None
    adaptation: dict | None = None


@dataclass(frozen=True, slots=True)
class SchedulingDecision:
    id: str
    task_id: str
    workflow_run_id: str
    node_id: str
    strategy: str
    algorithm_revision: str
    priority: int
    effective_priority: float
    waiting_ms: float
    candidates: tuple[CandidateAssessment, ...]
    selected: VersionRef | None
    outcome: str
    elapsed_ms: float
    created_at: datetime
    configuration: CAASConfig
    requirements: dict | None = None


@dataclass(frozen=True, slots=True)
class SchedulingAssignment:
    task_id: str
    execution_id: str
    agent_ref: VersionRef
    decision_id: str
    assigned_at: datetime
    waiting_ms: float


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    execution_id: str
    agent_ref: VersionRef
    task_type: str
    source: str
    succeeded: bool
    cancelled: bool
    duration_ms: float
    occurred_at: datetime
    outcome_category: str = "unknown"


@dataclass(frozen=True, slots=True)
class TaskFeatures:
    complexity: float | None = None
    latency_target_ms: float | None = None
    cost_budget: float | None = None
    reliability_target: float | None = None

    def __post_init__(self) -> None:
        for name in ('complexity', 'latency_target_ms', 'cost_budget', 'reliability_target'):
            v = getattr(self, name)
            if v is None:
                continue
            maximum = 1 if name in ('complexity', 'reliability_target') else 1000000
            if (type(v) not in (int, float) or not math.isfinite(v)
                    or not 0 <= v <= maximum or name in ('cost_budget', 'latency_target_ms') and v == 0):
                raise ValueError('Invalid scheduling feature')


@dataclass(frozen=True, slots=True)
class AdaptiveConfig:
    dynamic_weights: bool = True
    recency: bool = True
    health: bool = True
    attribution: bool = True
    recency_seconds: float = 60
    cooldown_seconds: float = 1
    failure_threshold: int = 2
    weight_floor: float = .05
    revision: str = 'adaptive-caas-v1'

    def __post_init__(self) -> None:
        if any(type(getattr(self, name)) is not bool for name in ('dynamic_weights','recency','health','attribution')):
            raise ValueError('Invalid adaptive toggle')
        for v, low, high in ((self.recency_seconds,.01,3600),(self.cooldown_seconds,.01,60),(self.weight_floor,.01,.1)):
            if type(v) not in (int,float) or not math.isfinite(v) or not low <= v <= high:
                raise ValueError('Invalid adaptive bound')
        if type(self.failure_threshold) is not int or not 2 <= self.failure_threshold <= 8:
            raise ValueError('Invalid failure threshold')
