"""Secret-free server model policies; capability declarations are not certification."""

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 0
    initial_delay: float = .5
    max_delay: float = 4

    def __post_init__(self) -> None:
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise ValueError("Invalid retry policy")
        for value in (self.initial_delay, self.max_delay):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 30:
                raise ValueError("Invalid retry policy")
        if self.initial_delay > self.max_delay:
            raise ValueError("Invalid retry delay range")


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    id: str
    supports_tools: bool = False
    response_formats: tuple[str, ...] = ("text", "prompt_json")
    tools_with_json: bool = False
    max_context_bytes: int = 32768
    max_output_tokens: int = 4096
    thinking_disabled: bool = False
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not 1 <= len(self.id) <= 128 or any(ord(c) < 33 for c in self.id):
            raise ValueError("Invalid model identifier")
        if any(type(v) is not bool for v in (self.supports_tools, self.tools_with_json, self.thinking_disabled)):
            raise ValueError("Invalid capability flags")
        if not self.response_formats or not set(self.response_formats).issubset({"text", "prompt_json", "json_object"}):
            raise ValueError("Unsupported response format")
        for value, maximum in ((self.max_context_bytes, 1048576), (self.max_output_tokens, 4096)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Invalid model resource limit")
        rates = (self.input_price_per_million, self.output_price_per_million)
        if self.currency is not None and (not isinstance(self.currency, str) or len(self.currency) != 3
                or not self.currency.isascii() or not self.currency.isalpha() or not self.currency.isupper()):
            raise ValueError("Invalid currency code")
        if any(rate is not None and (isinstance(rate, bool) or not isinstance(rate, (int, float))
               or not math.isfinite(rate) or not 0 <= rate <= 1000000000) for rate in rates):
            raise ValueError("Invalid configured price")
        if any(rate is not None for rate in rates) and (any(rate is None for rate in rates)
               or not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isupper()):
            raise ValueError("Prices require both rates and a three-letter currency")

    def estimate(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.input_price_per_million is None or self.output_price_per_million is None:
            return None
        return (input_tokens * self.input_price_per_million + output_tokens * self.output_price_per_million) / 1000000


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    kind: str
    base_url: str
    key_env: str
    models: tuple[ModelPolicy, ...]
    allowed_hosts: tuple[str, ...] = ()
    enabled: bool = False
    timeout_seconds: float = 15
    retry: RetryPolicy = RetryPolicy()
    revision: str = "1"
    allow_loopback: bool = False


@dataclass(frozen=True, slots=True)
class RealCallSafety:
    """Optional preparation ceiling; prices are estimates, never invoice guarantees."""

    max_output_tokens: int = 1024
    max_task_model_calls: int = 3
    max_workflow_model_calls: int = 10
    max_feedback_loops: int = 2
    max_concurrency: int = 1
    estimated_workflow_cny: float = .50
    profile: str = 'preparation'

    def __post_init__(self) -> None:
        if self.profile not in ('preparation', 'workspace', 'workspace_document'):
            raise ValueError('Unknown safety profile')
        for value, maximum in ((self.max_output_tokens, 4096 if self.profile == 'workspace_document' else 1024), (self.max_task_model_calls, 3),
                               (self.max_workflow_model_calls, 10), (self.max_feedback_loops, 2),
                               (self.max_concurrency, 1)):
            if type(value) is not int or not (0 if maximum == 2 else 1) <= value <= maximum:
                raise ValueError("Preparation safety limit exceeds approved ceiling")
        if (isinstance(self.estimated_workflow_cny, bool)
                or not isinstance(self.estimated_workflow_cny, (int, float))
                or not math.isfinite(self.estimated_workflow_cny)
                or not 0 < self.estimated_workflow_cny <= (1000 if self.profile in ('workspace','workspace_document') else .50)):
            raise ValueError("Invalid CNY estimate ceiling")
