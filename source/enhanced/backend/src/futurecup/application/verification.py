"""Independent evidence aggregation; generation success never implies correctness."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from futurecup.application.ports import SafetyRepository, VerificationBackend
from futurecup.domain.errors import HTTPErrorCategory
from futurecup.domain.models import Verification, VerificationVerdict
from futurecup.domain.verification import ArtifactVersion, CheckEvidence, ResourceBudget, VerificationPolicy, VerificationResult


class VerifierEngine:
    def __init__(self, backend: VerificationBackend, repository: SafetyRepository) -> None:
        self.backend, self.repository = backend, repository

    async def register(self, policy: VerificationPolicy) -> None:
        self.backend.validate_policy(policy)
        await self.repository.register_policy(policy)

    async def verify(self, policy: VerificationPolicy, artifact: ArtifactVersion, *, run_id: str,
                     node_id: str, attempt_id: str, execution_id: str, budget: ResourceBudget, execution_details: dict | None = None) -> VerificationResult:
        self.backend.validate_policy(policy)
        started = perf_counter()
        checks_before = budget.checks
        evidence: list[CheckEvidence] = []
        origin = "artifact"
        if self.backend.digest(artifact.content) != artifact.digest:
            origin = "verifier"
            evidence.append(CheckEvidence("artifact", "ERROR", "artifact_digest_mismatch"))
        elif artifact.execution_error:
            origin = "execution"
            safe_details = None
            if isinstance(execution_details, dict) and execution_details.get('http_error_category') in {c.value for c in HTTPErrorCategory}:
                status = execution_details.get('http_status_code')
                safe_details = {'http_error_category': execution_details['http_error_category'],
                                'http_status_code': status if type(status) is int and 100 <= status <= 599 else None}
            evidence.append(CheckEvidence("execution", "ERROR", artifact.execution_error, details=safe_details))
        elif artifact.format_error:
            evidence.append(CheckEvidence("json_schema", "FAILED", "output_format_error", rule="json_parse"))
        else:
            try:
                async with asyncio.timeout(policy.timeout_seconds):
                    for check in policy.checks:
                        budget.consume("checks")
                        try:
                            item = await self.backend.check(check, artifact)
                            if not isinstance(item, CheckEvidence) or item.status not in {"PASSED", "FAILED", "INCONCLUSIVE", "ERROR"}:
                                raise ValueError
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            item = CheckEvidence(check.kind, "ERROR", "verifier_internal_error")
                        evidence.append(item)
            except TimeoutError:
                evidence.append(CheckEvidence("verifier", "ERROR", "verifier_timeout"))
        statuses = {item.status for item in evidence}
        verdict = (VerificationVerdict.ERROR if "ERROR" in statuses else VerificationVerdict.FAIL if "FAILED" in statuses
                   else VerificationVerdict.INCONCLUSIVE if "INCONCLUSIVE" in statuses else VerificationVerdict.PASS)
        if verdict in {VerificationVerdict.ERROR, VerificationVerdict.INCONCLUSIVE} and origin != "execution":
            origin = "verifier"
        record = Verification(uuid4().hex, execution_id, policy.ref, verdict,
                              tuple(item.reference for item in evidence if item.reference),
                              tuple(item.reason for item in evidence if item.status != "PASSED"), datetime.now(timezone.utc))
        result = VerificationResult(record, run_id, node_id, attempt_id, artifact.ref, artifact.digest,
                                    self.backend.digest(asdict(policy)), tuple(evidence), (perf_counter()-started)*1000,
                                    budget.checks - checks_before, artifact.simulated, origin)
        await self.repository.save_result(result)
        return result
