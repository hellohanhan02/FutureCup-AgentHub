"""Domain records. Runtime limits and application workflow rules are validated.

IDs are opaque strings. Timestamps must be UTC aware. Version fields pin
immutable definitions; revision fields support optimistic concurrency.
"""

from dataclasses import dataclass, field
import math
from datetime import datetime
from enum import Enum
from typing import TypeAlias, Union

JsonValue: TypeAlias = Union[None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]]


class AgentState(str, Enum):
    REGISTERED = "registered"
    AVAILABLE = "available"
    DRAINING = "draining"
    OFFLINE = "offline"
    RETIRED = "retired"


class TaskState(str, Enum):
    PENDING = "pending"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class ExecutionState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class VerificationVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


class FeedbackAction(str, Enum):
    RETRY = "retry"
    REASSIGN = "reassign"
    REPLAN = "replan"
    REQUEST_HUMAN = "request_human"
    ABORT = "abort"


class WorkflowState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeKind(str, Enum):
    TASK = "task"
    PARALLEL = "parallel"
    CONDITION = "condition"
    LOOP = "loop"
    SUBWORKFLOW = "subworkflow"


@dataclass(frozen=True, slots=True)
class VersionRef:
    id: str
    version: str


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    ref: VersionRef
    role: str
    skills: tuple[str, ...]
    tool_refs: tuple[VersionRef, ...]
    model_profile: str
    memory_namespace: str
    permission_profile: str
    max_concurrency: int
    name: str = ""
    responsibilities: str = ""
    system_prompt: str = ""
    provider: str = "mock"
    model: str = "mock-echo"
    enabled: bool = True
    task_types: tuple[str, ...] = ("*",)
    available: bool = True
    estimated_cost: float | None = None
    estimated_latency_ms: float | None = None
    response_format: str = "text"
    knowledge_base_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentRuntime:
    agent_ref: VersionRef
    instance_id: str
    state: AgentState
    active_executions: int
    heartbeat_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    max_attempts: int
    max_replans: int
    max_no_progress: int
    deadline: datetime
    max_model_tokens: int
    max_cost_units: int


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    workflow_run_id: str
    node_instance_id: str
    required_skills: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    priority: int
    input_refs: tuple[str, ...]
    verification_profile: VersionRef
    permission_profile: str
    recovery_policy: RecoveryPolicy
    state: TaskState
    revision: int


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    id: str
    kind: NodeKind
    config: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    source: str
    target: str
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    ref: VersionRef
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    max_subworkflow_depth: int
    name: str = ""
    output_mapping: dict[str, JsonValue] = field(default_factory=dict)
    max_concurrency: int = 4
    timeout_seconds: float = 60
    max_node_executions: int = 100
    error_policy: str = "continue"
    scheduling_strategy: str = "caas"
    resource_limits: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    id: str
    definition_ref: VersionRef
    state: WorkflowState
    policy: RecoveryPolicy
    parent_run_id: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class Execution:
    id: str
    task_id: str
    attempt: int
    agent_ref: VersionRef
    agent_instance_id: str
    strategy_ref: VersionRef
    idempotency_key: str
    state: ExecutionState
    lease_expires_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    output_refs: tuple[str, ...]
    error_code: str | None
    revision: int


@dataclass(frozen=True, slots=True)
class Verification:
    id: str
    execution_id: str
    verifier_ref: VersionRef
    verdict: VerificationVerdict
    evidence_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Feedback:
    id: str
    task_id: str
    verification_id: str
    action: FeedbackAction
    reason: str
    strategy_patch: dict[str, JsonValue]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    id: str
    workflow_run_id: str
    aggregate_id: str
    sequence: int
    type: str
    occurred_at: datetime
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class RunLimits:
    timeout_seconds: float = 30
    model_timeout_seconds: float = 10
    tool_timeout_seconds: float = 5
    max_calls: int = 8
    max_model_calls: int = 4
    max_tool_calls: int = 4
    max_output_tokens: int = 1024
    max_input_chars: int = 8192

    def __post_init__(self) -> None:
        for value in (self.timeout_seconds, self.model_timeout_seconds, self.tool_timeout_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("Timeouts must be finite and between 0 and 300 seconds")
        for value, maximum in ((self.max_calls, 64), (self.max_model_calls, 32),
                               (self.max_tool_calls, 32), (self.max_output_tokens, 4096), (self.max_input_chars, 65536)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Call/token limits are outside the supported range")


@dataclass(frozen=True, slots=True)
class ModelCallRecord:
    number: int
    elapsed_ms: float
    error_code: str | None
    provider_request_id: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    usage_reported: bool = False
    estimated_cost: float | None = None
    currency: str | None = None
    resolved_model: str | None = None
    retry_index: int = 0
    http_status_code: int | None = None
    http_error_category: str | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    call_id: str
    name: str
    arguments: dict[str, JsonValue]
    state: ExecutionState
    elapsed_ms: float
    output: JsonValue = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class RunReport:
    execution: Execution
    agent_name: str
    provider: str
    model: str
    simulated: bool
    input: str
    limits: RunLimits
    output: str | None = None
    elapsed_ms: float = 0
    model_calls: tuple[ModelCallRecord, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    usage_complete: bool = True
    resolved_model: str | None = None
    provider_config_ref: VersionRef | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    cost_complete: bool = False
    cost_basis: str = "configured_estimate_not_invoice"
    incomplete_output: str | None = None
    retrieval_records: tuple[dict, ...] = ()

    @property
    def call_count(self) -> int:
        return len(self.model_calls) + len(self.tool_calls)


@dataclass(frozen=True, slots=True)
class WorkflowNodeRun:
    id: str
    node_id: str
    state: TaskState = TaskState.PENDING
    input: JsonValue = None
    output: JsonValue = None
    execution_id: str | None = None
    child_run_ids: tuple[str, ...] = ()
    agent_ref: VersionRef | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_ms: float = 0
    simulated: bool | None = None
    verified: bool = False
    ready_at: datetime | None = None
    assigned_at: datetime | None = None
    waiting_ms: float = 0
    scheduling_decision_id: str | None = None
    attempt_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    feedback_ids: tuple[str, ...] = ()
    current_artifact_ref: VersionRef | None = None
    fallback_activated: bool = False
    provider: str | None = None
    model: str | None = None
    resolved_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usage_complete: bool | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    cost_complete: bool = False
    cost_basis: str = "configured_estimate_not_invoice"
    model_calls: tuple[ModelCallRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowReport:
    run: WorkflowRun
    input: JsonValue
    nodes: tuple[WorkflowNodeRun, ...]
    root_run_id: str
    parent_node_id: str | None = None
    depth: int = 0
    output: JsonValue = None
    events: tuple[RuntimeEvent, ...] = ()
    agent_bindings: dict[str, VersionRef] = field(default_factory=dict)
    error_code: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed_ms: float = 0
    node_executions: int = 0
    simulated: bool | None = None
    verified: bool = False
    resource_usage: dict[str, int] = field(default_factory=dict)
