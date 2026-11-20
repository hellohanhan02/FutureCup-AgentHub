"""Stable public codes. Never include provider bodies, credentials or tracebacks."""


from dataclasses import dataclass, field
from enum import Enum


class HTTPErrorCategory(str, Enum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_FAILED = "authentication_failed"
    INSUFFICIENT_BALANCE = "insufficient_balance"
    RATE_LIMITED = "rate_limited"
    MODEL_UNAVAILABLE = "model_unavailable"
    SERVER_ERROR = "server_error"
    UNKNOWN_HTTP_ERROR = "unknown_http_error"


@dataclass(frozen=True, slots=True)
class ProviderHTTPDiagnostic:
    """Allowlisted metadata; upstream status is not our public API status."""

    status_code: int
    category: HTTPErrorCategory
    request_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ValueError("Invalid upstream HTTP status")
        if not isinstance(self.category, HTTPErrorCategory):
            raise ValueError("Invalid safe HTTP error category")
        value = self.request_id
        if value is not None:
            canonical = isinstance(value, str) and ((len(value) == 32 and "-" not in value) or
                (len(value) == 36 and tuple(i for i, c in enumerate(value) if c == "-") == (8, 13, 18, 23)))
            if not canonical or any(c not in "0123456789abcdefABCDEF" for c in value.replace("-", "")):
                raise ValueError("Invalid safe request identifier")


@dataclass(frozen=True, slots=True)
class ProviderCompletionDiagnostic:
    """Validated incomplete generation, isolated from published task output."""
    finish_reason: str
    content: str = field(repr=False)
    input_tokens: int
    output_tokens: int
    usage_reported: bool
    model: str | None
    request_id: str | None
    tool_calls_present: bool = False

    def __post_init__(self) -> None:
        if self.finish_reason not in ('length','content_filter','unknown') or not isinstance(self.content,str) or len(self.content)>65536:
            raise ValueError('Invalid incomplete generation metadata')
        if any(type(v) is not int or not 0<=v<=10000000 for v in (self.input_tokens,self.output_tokens)) or type(self.usage_reported) is not bool or type(self.tool_calls_present) is not bool:
            raise ValueError('Invalid incomplete usage metadata')
        if self.model is not None and (not isinstance(self.model,str) or not self.model.strip() or len(self.model)>128 or any(ord(c)<32 for c in self.model)):
            raise ValueError('Invalid model metadata')
        if self.request_id is not None:ProviderHTTPDiagnostic(200,HTTPErrorCategory.UNKNOWN_HTTP_ERROR,self.request_id)


class RuntimeFault(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, *,
                 provider_http: ProviderHTTPDiagnostic | None = None,
                 provider_completion: ProviderCompletionDiagnostic | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.provider_http = provider_http
        self.provider_completion = provider_completion
