"""Deterministic verification only. External tests are server-registered callbacks."""

from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
from typing import Awaitable, Callable

from jsonschema import Draft202012Validator

from futurecup.adapters.workflow_store import JsonSchemaWorkflowValidator
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.verification import ArtifactVersion, CheckEvidence, VerificationCheck, VerificationPolicy


def at(value: object, path: str) -> object:
    for part in path.split(".") if path else ():
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


class StructuredVerificationBackend:
    def __init__(self, external: dict[str, Callable[[ArtifactVersion], Awaitable[CheckEvidence]]] | None = None) -> None:
        self.external = dict(external or {})

    def digest(self, value: object) -> str:
        if is_dataclass(value):
            value = asdict(value)
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    def validate_policy(self, policy: VerificationPolicy) -> None:
        def bounded_schema(value: object, depth: int = 0, count: list[int] | None = None) -> None:
            count = count if count is not None else [0]
            count[0] += 1
            if depth > 16 or count[0] > 512:
                raise ValueError
            if isinstance(value, dict):
                # Synchronous regex execution cannot be interrupted by asyncio timeout.
                # Format is annotation-only without a configured checker: reject it explicitly.
                if set(value) & {"pattern", "patternProperties", "format", "$recursiveRef"}:
                    raise ValueError
                for child in value.values():
                    bounded_schema(child, depth + 1, count)
            elif isinstance(value, list):
                for child in value:
                    bounded_schema(child, depth + 1, count)
        try:
            if (not 1 <= len(policy.checks) <= 32 or isinstance(policy.timeout_seconds, bool)
                    or not 0 < policy.timeout_seconds <= 30 or not math.isfinite(policy.timeout_seconds)):
                raise ValueError
            for check in policy.checks:
                c = check.config
                if check.kind == "json_schema":
                    if set(c) != {"schema"}:
                        raise ValueError
                    JsonSchemaWorkflowValidator().check_schema(c["schema"])
                    bounded_schema(c["schema"])
                elif check.kind == "assertion":
                    if (set(c) != {"path", "op", "value"} or c["op"] not in ("eq", "ne", "gt", "gte", "lt", "lte")
                            or not isinstance(c["path"], str) or len(c["path"]) > 256):
                        raise ValueError
                elif check.kind == "numeric":
                    if (not {"path", "expected"}.issubset(c) or set(c) - {"path", "expected", "other_path", "tolerance"}
                            or not isinstance(c["path"], str) or len(c["path"]) > 256
                            or ("other_path" in c and not isinstance(c["other_path"], str))):
                        raise ValueError
                    for v in (c["expected"], c.get("tolerance", 0)):
                        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                            raise ValueError
                    if c.get("tolerance", 0) < 0:
                        raise ValueError
                elif check.kind == "external_tests":
                    if set(c) != {"test_id"} or not isinstance(c["test_id"], str) or len(c["test_id"]) > 64:
                        raise ValueError
                else:
                    raise ValueError
            if len(json.dumps(asdict(policy), allow_nan=False)) > 65536:
                raise ValueError
        except (TypeError, ValueError, KeyError, RuntimeFault):
            raise RuntimeFault("verification_policy_invalid", "Invalid verification policy", 422) from None

    async def check(self, check: VerificationCheck, artifact: ArtifactVersion) -> CheckEvidence:
        c = check.config
        if check.kind == "external_tests":
            callback = self.external.get(c["test_id"])
            if callback is None:
                return CheckEvidence(check.kind, "INCONCLUSIVE", "trusted_test_evidence_unavailable")
            result = await callback(artifact)
            if (not isinstance(result, CheckEvidence) or result.status not in {"PASSED", "FAILED", "INCONCLUSIVE", "ERROR"}
                    or result.reference != artifact.digest):
                return CheckEvidence(check.kind, "ERROR", "external_test_evidence_unbound")
            return result
        if check.kind == "json_schema":
            error = next(Draft202012Validator(c["schema"]).iter_errors(artifact.content), None)
            return CheckEvidence(check.kind, "FAILED" if error else "PASSED", "schema_mismatch" if error else "schema_matched",
                                 ".".join(str(p) for p in error.absolute_path)[:256] if error else None,
                                 rule=str(error.validator)[:64] if error else None,
                                 schema_path=".".join(str(p) for p in error.absolute_schema_path)[:256] if error else None)
        try:
            actual = at(artifact.content, c["path"])
            if check.kind == "numeric":
                expected = at(artifact.content, c["other_path"]) if "other_path" in c else c["expected"]
                numbers = (actual, expected)
                valid = all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in numbers)
                passed = valid and abs(actual - expected) <= c.get("tolerance", 0)
            else:
                expected, op = c["value"], c["op"]
                passed = {"eq": lambda: actual == expected, "ne": lambda: actual != expected,
                          "gt": lambda: actual > expected, "gte": lambda: actual >= expected,
                          "lt": lambda: actual < expected, "lte": lambda: actual <= expected}[op]()
            return CheckEvidence(check.kind, "PASSED" if passed else "FAILED", "assertion_matched" if passed else "assertion_failed", c["path"])
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            return CheckEvidence(check.kind, "FAILED", "missing_or_incompatible_value", c["path"])
