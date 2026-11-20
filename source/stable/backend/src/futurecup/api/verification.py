"""Local audit API. Manual decisions require a separately configured server token."""

from dataclasses import asdict
import hmac
import os
from typing import Any, Literal

from fastapi import APIRouter, Header
from fastapi.encoders import jsonable_encoder
from pydantic import Field

from futurecup.api.agents import Identifier, StrictModel
from futurecup.application.selfloop import SelfLoopEngine
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import VersionRef
from futurecup.domain.verification import VerificationCheck, VerificationPolicy
from futurecup.adapters.order_challenge import frozen_definition


class CheckInput(StrictModel):
    kind: Literal["json_schema", "assertion", "numeric", "external_tests"]
    config: dict[str, Any]


class PolicyInput(StrictModel):
    id: Identifier
    version: Identifier = "1"
    name: str = Field(min_length=1, max_length=128)
    checks: list[CheckInput] = Field(min_length=1, max_length=32)
    timeout_seconds: float = Field(default=5, gt=0, le=30)


class ArtifactRefInput(StrictModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    version: Identifier


class HumanDecision(StrictModel):
    revision: int = Field(ge=0, strict=True)
    attempt_id: Identifier
    artifact_ref: ArtifactRefInput
    decision: Literal["retry", "abort"]
    actor: str = Field(min_length=1, max_length=64)


def build_verification_router(loop: SelfLoopEngine) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["verification-recovery"])
    store = loop.repository

    @router.get("/verification-capabilities")
    async def capabilities():
        return {"registered_external_tests": sorted(getattr(loop.backend, "external", {})),
                "self_loop_feedback_context_version": loop.feedback_context_version,
                "self_loop_feedback_modes": ["full", "errors_only"],
                "challenge09c": frozen_definition()[1],
                "network_request_performed": False}

    @router.post("/verification-policies", status_code=201)
    async def register(data: PolicyInput):
        loop.runtime.check_sensitive(data.model_dump())
        policy = VerificationPolicy(VersionRef(data.id, data.version), data.name,
                                   tuple(VerificationCheck(c.kind, c.config) for c in data.checks), data.timeout_seconds)
        await loop.verifier.register(policy)
        return jsonable_encoder(asdict(policy))

    @router.get("/verification-policies")
    async def policies():
        return jsonable_encoder([asdict(p) for p in await store.policies()])

    @router.get("/verification-policies/{policy_id}")
    async def policy(policy_id: str, version: str = "1"):
        return jsonable_encoder(asdict(await store.policy(VersionRef(policy_id, version))))

    @router.get("/self-loops/{node_id}")
    async def session(node_id: str):
        return await store.session(node_id)

    @router.get("/self-loops/{node_id}/attempts")
    async def attempts(node_id: str):
        await store.session(node_id)
        return await store.attempts(node_id)

    @router.get("/self-loops/{node_id}/verifications")
    async def verifications(node_id: str):
        await store.session(node_id)
        return [{**v, "status": {"pass": "PASSED", "fail": "FAILED", "error": "ERROR", "inconclusive": "INCONCLUSIVE"}[v["record"]["verdict"]]}
                for v in await store.results(node_id)]

    @router.get("/self-loops/{node_id}/feedback")
    async def feedback(node_id: str):
        await store.session(node_id)
        return await store.feedbacks(node_id)

    @router.get("/human-interventions")
    async def interventions(node_id: str | None = None):
        return await store.tickets(node_id)

    @router.post("/human-interventions/{ticket_id}/resolve")
    async def resolve(ticket_id: str, data: HumanDecision,
                      approval_token: str | None = Header(default=None, alias="X-Harness-Approval-Token")):
        expected = os.environ.get("FUTURECUP_APPROVAL_TOKEN")
        if not expected or not approval_token or not hmac.compare_digest(expected.encode(), approval_token.encode()):
            raise RuntimeFault("approval_denied", "Manual intervention requires configured authorization", 403)
        await store.resolve_ticket(ticket_id, data.revision, data.attempt_id,
            VersionRef(data.artifact_ref.id, data.artifact_ref.version), data.decision, data.actor)
        return await store.ticket(ticket_id)

    return router
