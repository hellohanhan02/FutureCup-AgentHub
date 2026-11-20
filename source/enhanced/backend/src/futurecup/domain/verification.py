"""Version-bound verification and bounded recovery records; no framework dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math

from futurecup.domain.models import Feedback, JsonValue, Verification, VersionRef, ModelCallRecord, ToolCallRecord


class ErrorCategory(str, Enum):
    OUTPUT_FORMAT_ERROR = "OUTPUT_FORMAT_ERROR"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    TOOL_EXECUTION_ERROR = "TOOL_EXECUTION_ERROR"
    AGENT_CAPABILITY_MISMATCH = "AGENT_CAPABILITY_MISMATCH"
    TIMEOUT = "TIMEOUT"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    SCHEMA_FAILED = "SCHEMA_FAILED"
    BUSINESS_CONSTRAINT = "BUSINESS_CONSTRAINT"
    KNOWLEDGE_MISSING = "KNOWLEDGE_MISSING"
    CITATION_MISSING = "CITATION_MISSING"
    RULE_CONFLICT = "RULE_CONFLICT"
    AGENT_RUNTIME_ERROR = "AGENT_RUNTIME_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VERIFIER_ERROR = "VERIFIER_ERROR"


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    kind: str
    config: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    ref: VersionRef
    name: str
    checks: tuple[VerificationCheck, ...]
    timeout_seconds: float = 5


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    ref: VersionRef
    digest: str
    content: JsonValue
    simulated: bool | None
    format_error: bool = False
    execution_error: str | None = None


@dataclass(frozen=True, slots=True)
class CheckEvidence:
    kind: str
    status: str
    reason: str
    path: str | None = None
    reference: str | None = None
    rule: str | None = None
    schema_path: str | None = None
    details: dict[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    record: Verification
    run_id: str
    node_id: str
    attempt_id: str
    artifact_ref: VersionRef
    artifact_digest: str
    policy_digest: str
    evidence: tuple[CheckEvidence, ...]
    elapsed_ms: float
    checks_used: int
    simulated_generation: bool | None
    origin: str = "artifact"


@dataclass(frozen=True, slots=True)
class RecoveryLimits:
    max_attempts: int = 1
    max_feedback_loops: int = 0
    max_replans: int = 0
    timeout_seconds: float = 30
    on_failure: str = "abort"
    on_uncertain: str = "abort"
    reassign_after: int = 2
    tool_options: tuple[tuple[VersionRef, ...], ...] = ()
    fallback_node: str | None = None
    retry_execution_errors: bool = True
    retry_truncated_output: bool = False
    feedback_mode: str = "full"
    precise: bool = False

    def __post_init__(self) -> None:
        if type(self.precise) is not bool: raise ValueError("Invalid precise recovery switch")
        if self.feedback_mode not in ("full", "errors_only"):
            raise ValueError("Invalid feedback context mode")
        if type(self.retry_execution_errors) is not bool:
            raise ValueError("Invalid execution-error recovery option")
        if type(self.retry_truncated_output) is not bool:
            raise ValueError('Invalid truncation recovery option')
        for value, lower, upper in ((self.max_attempts, 1, 16), (self.max_feedback_loops, 0, 15),
                                    (self.max_replans, 0, 15), (self.reassign_after, 1, 16)):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("Recovery limits out of range")
        if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 300:
            raise ValueError("Invalid recovery timeout")
        if self.on_failure not in ("abort", "prompt_retry", "reassign", "tool_switch", "fallback", "human") or self.on_uncertain not in ("abort", "human"):
            raise ValueError("Unknown recovery action")


@dataclass
class ResourceBudget:
    max_calls: int = 128
    max_resource_units: int = 1048576
    max_checks: int = 512
    max_attempts: int = 64
    max_feedback_loops: int = 32
    max_replans: int = 16
    calls: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    resource_units: int = 0
    checks: int = 0
    attempts: int = 0
    feedback_loops: int = 0
    replans: int = 0
    reported_tokens: int = 0
    unknown_usage_calls: int = 0
    parent: "ResourceBudget | None" = None
    simulation_only: bool = False
    ledger_account_id: str | None = None
    ledger_run_id: str | None = None
    ledger_node_id: str | None = None
    estimated_cny_reserved: float = field(default=0, init=False)
    estimated_cny_ceiling: float | None = field(default=None, init=False)
    task_model_call_ceiling: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if type(self.simulation_only) is not bool:
            raise ValueError("Invalid simulation-only guard")
        for key in ("max_calls", "max_resource_units", "max_checks", "max_attempts", "max_feedback_loops", "max_replans"):
            value = getattr(self, key)
            if type(value) is not int or not 0 <= value <= 10000000:
                raise ValueError("Invalid shared resource limit")

    def consume(self, kind: str, units: int = 0) -> None:
        from futurecup.domain.errors import RuntimeFault
        if type(units) is not int or units < 0 or kind not in {"model", "tool", "checks", "attempts", "feedback_loops", "replans"}:
            raise ValueError("Invalid resource charge")
        if kind in ("model", "tool"):
            if self.calls >= self.max_calls or self.resource_units + units > self.max_resource_units:
                raise RuntimeFault("shared_call_budget", "Shared call/resource budget exhausted")
            if self.parent is not None:
                self.parent.consume(kind, units)
            self.calls += 1
            setattr(self, kind + "_calls", getattr(self, kind + "_calls") + 1)
            self.resource_units += units
            self.unknown_usage_calls += int(kind == "model")
            return
        maximum = getattr(self, "max_" + kind)
        if getattr(self, kind) >= maximum:
            raise RuntimeFault("shared_" + kind + "_budget", "Shared recovery budget exhausted")
        if self.parent is not None:
            self.parent.consume(kind, units)
        setattr(self, kind, getattr(self, kind) + 1)

    def fork(self) -> "ResourceBudget":
        return ResourceBudget(**{key: getattr(self, key) for key in ("max_calls", "max_resource_units", "max_checks",
            "max_attempts", "max_feedback_loops", "max_replans")}, parent=self)

    def record_model_usage(self, tokens: int, known: bool) -> None:
        self.reported_tokens += tokens
        self.unknown_usage_calls -= int(known)
        if self.parent is not None:
            self.parent.record_model_usage(tokens, known)

    def snapshot(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in ("calls", "model_calls", "tool_calls", "resource_units", "checks",
                "attempts", "feedback_loops", "replans", "reported_tokens", "unknown_usage_calls")}


@dataclass(frozen=True, slots=True)
class FeedbackReport:
    record: Feedback
    run_id: str
    node_id: str
    attempt_id: str
    artifact_ref: VersionRef
    artifact_digest: str
    category: ErrorCategory
    origin: str
    repair_action: str
    evidence_refs: tuple[str, ...]
    budget_cost: dict[str, int]
    diagnosis: dict | None = None


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    id: str
    run_id: str
    node_id: str
    ordinal: int
    input_ref: VersionRef
    input_digest: str
    input_value: JsonValue
    policy_ref: VersionRef
    policy_digest: str
    state: str = "created"
    revision: int = 0
    agent_ref: VersionRef | None = None
    provider: str | None = None
    model: str | None = None
    decision_id: str | None = None
    execution_id: str | None = None
    artifact: ArtifactVersion | None = None
    verification_id: str | None = None
    error_code: str | None = None
    simulated: bool | None = None
    tools: tuple[VersionRef, ...] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    resources: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    usage_complete: bool | None = None
    resolved_model: str | None = None
    provider_config_ref: VersionRef | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    cost_complete: bool = False
    model_calls: tuple[ModelCallRecord, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    retrieval_records: tuple[dict, ...] = ()
