"""Shared, secret-free workspace admission arithmetic; never a supplier invoice."""
from futurecup.domain.llm import ModelPolicy

MAX_TASK_BUDGET_CNY = 1000
MAX_MODEL_CALLS = 6
DOCUMENT_CONTEXT_BYTES = 131072
DOCUMENT_OUTPUT_TOKENS = 4096


def admission_estimate(policy: ModelPolicy, max_calls: int, output_tokens: int,
                       *, calls: int = 0, reserved: float = 0) -> float | None:
    per_call = policy.estimate(policy.max_context_bytes, output_tokens)
    return None if per_call is None else reserved + per_call * max(0, max_calls - calls)
