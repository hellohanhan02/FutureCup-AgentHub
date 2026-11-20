"""Local SQLite audit store. No transaction crosses an execution/verification await."""

from dataclasses import asdict, is_dataclass
import json
import sqlite3

from futurecup.adapters.workflow_store import SQLiteWorkflowRepository, encode
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import VersionRef
from futurecup.domain.verification import VerificationCheck, VerificationPolicy


class SQLiteSafetyRepository:
    def __init__(self, workflows: SQLiteWorkflowRepository) -> None:
        self.workflows = workflows

    async def open(self) -> None:
        await self.workflows.open()
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS safety_records (
            kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY(kind,id));
          CREATE INDEX IF NOT EXISTS safety_owner ON safety_records(kind,owner);
        """)
        self.db.commit()

    @property
    def db(self):
        return self.workflows.db

    def _get(self, kind: str, key: str) -> dict:
        row = self.db.execute("SELECT body FROM safety_records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            raise RuntimeFault("safety_record_not_found", "Audit record not found", 404)
        return json.loads(row[0])

    def _put(self, kind: str, key: str, owner: str, value: object, update: bool = False) -> None:
        body = encode(asdict(value) if is_dataclass(value) else value)
        try:
            if update:
                self.db.execute("UPDATE safety_records SET body=? WHERE kind=? AND id=?", (body, kind, key))
            else:
                self.db.execute("INSERT INTO safety_records VALUES (?,?,?,?)", (kind, key, owner, body))
        except sqlite3.IntegrityError:
            raise RuntimeFault("safety_record_conflict", "Immutable record already exists", 409) from None

    def _list(self, kind: str, owner: str | None = None) -> tuple[dict, ...]:
        query = "SELECT body FROM safety_records WHERE kind=?"
        args = [kind]
        if owner is not None:
            query += " AND owner=?"
            args.append(owner)
        return tuple(json.loads(row[0]) for row in self.db.execute(query + " ORDER BY rowid", args))

    async def register_policy(self, policy: VerificationPolicy) -> None:
        await self.open()
        with self.db:
            self._put("policy", policy.ref.id + "@" + policy.ref.version, policy.ref.id, policy)

    async def policy(self, ref: VersionRef) -> VerificationPolicy:
        await self.open()
        data = self._get("policy", ref.id + "@" + ref.version)
        return VerificationPolicy(VersionRef(**data["ref"]), data["name"],
                                  tuple(VerificationCheck(**check) for check in data["checks"]), data["timeout_seconds"])

    async def policies(self) -> tuple[VerificationPolicy, ...]:
        await self.open()
        return tuple([await self.policy(VersionRef(**d["ref"])) for d in self._list("policy")])

    async def create_session(self, node_id: str, value: dict) -> None:
        await self.open()
        with self.db:
            self._put("session", node_id, value["run_id"], {**value, "id": node_id, "current_attempt": None,
                       "accepted_artifact": None, "state": "running", "error": None, "usage": {}})

    async def session(self, node_id: str) -> dict:
        await self.open()
        return self._get("session", node_id)

    async def begin_attempt(self, attempt: object) -> None:
        with self.db:
            session = self._get("session", attempt.node_id)
            if session["state"] != "running":
                raise RuntimeFault("stale_attempt", "Session is not running", 409)
            if session["current_attempt"]:
                prior = self._get("attempt", session["current_attempt"])
                if prior["state"] not in {"completed", "cancelled", "failed"}:
                    raise RuntimeFault("attempt_active", "Prior attempt is still active", 409)
            self._put("attempt", attempt.id, attempt.node_id, attempt)
            session["current_attempt"] = attempt.id
            self._put("session", attempt.node_id, session["run_id"], session, True)

    async def save_attempt(self, attempt: object, expected_revision: int) -> None:
        with self.db:
            current = self._get("attempt", attempt.id)
            if current["revision"] != expected_revision or current["state"] in {"completed", "cancelled", "failed"}:
                raise RuntimeFault("stale_attempt", "Attempt revision changed or attempt is terminal", 409)
            if attempt.revision != expected_revision + 1:
                raise RuntimeFault("stale_attempt", "Invalid attempt revision", 409)
            self._put("attempt", attempt.id, attempt.node_id, attempt, True)

    async def attempt(self, attempt_id: str) -> dict:
        await self.open()
        return self._get("attempt", attempt_id)

    async def attempts(self, node_id: str) -> tuple[dict, ...]:
        await self.open()
        return self._list("attempt", node_id)

    async def save_result(self, result: object) -> None:
        with self.db:
            a = self._get("attempt", result.attempt_id)
            session = self._get("session", result.node_id)
            if (session["current_attempt"] != result.attempt_id or session["state"] != "running"
                    or a["artifact"] is None or a["artifact"]["digest"] != result.artifact_digest
                    or a["artifact"]["ref"] != asdict(result.artifact_ref)
                    or a["policy_digest"] != result.policy_digest or a["verification_id"] is not None
                    or a["run_id"] != result.run_id or a["node_id"] != result.node_id
                    or a["policy_ref"] != asdict(result.record.verifier_ref)
                    or (a["execution_id"] or a["id"]) != result.record.execution_id
                    or any(v["attempt_id"] == result.attempt_id for v in self._list("verification", result.node_id))):
                raise RuntimeFault("stale_verification", "Verification does not match current artifact/policy", 409)
            self._put("verification", result.record.id, result.node_id, result)

    async def results(self, node_id: str) -> tuple[dict, ...]:
        await self.open()
        return self._list("verification", node_id)

    async def save_feedback(self, feedback: object) -> None:
        with self.db:
            v = self._get("verification", feedback.record.verification_id)
            if (v["attempt_id"] != feedback.attempt_id or v["node_id"] != feedback.node_id
                    or v["artifact_digest"] != feedback.artifact_digest or v["artifact_ref"] != asdict(feedback.artifact_ref)):
                raise RuntimeFault("stale_feedback", "Feedback must reference matching verification evidence", 409)
            self._put("feedback", feedback.record.id, feedback.node_id, feedback)

    async def feedbacks(self, node_id: str) -> tuple[dict, ...]:
        await self.open()
        usage = {u["feedback_id"]: u["usage"] for u in self._list("feedback_usage", node_id)}
        return tuple({**f, "actual_budget_cost": usage.get(f["record"]["id"], {"feedback_loops": 0, "replans": 0})}
                     for f in self._list("feedback", node_id))

    async def record_feedback_usage(self, feedback_id: str, usage: dict[str, int]) -> None:
        with self.db:
            f = self._get("feedback", feedback_id)
            self._put("feedback_usage", feedback_id, f["node_id"], {"feedback_id": feedback_id, "usage": usage})

    async def apply_feedback(self, feedback_id: str, attempt_id: str, artifact: VersionRef) -> None:
        with self.db:
            f = self._get("feedback", feedback_id)
            s = self._get("session", f["node_id"])
            if (f["attempt_id"] != attempt_id or s["current_attempt"] != attempt_id
                    or f["artifact_ref"] != asdict(artifact) or s["state"] != "running"):
                raise RuntimeFault("stale_feedback", "Feedback does not match current attempt/artifact", 409)
            self._put("feedback_applied", feedback_id, f["node_id"], {"feedback_id": feedback_id, "attempt_id": attempt_id})

    async def publish(self, node_id: str, attempt_id: str, verification_id: str) -> None:
        with self.db:
            s, a, v = self._get("session", node_id), self._get("attempt", attempt_id), self._get("verification", verification_id)
            if (s["state"] != "running" or s["current_attempt"] != attempt_id or v["attempt_id"] != attempt_id
                    or a["verification_id"] != verification_id or v["record"]["verdict"] != "pass"
                    or v["artifact_digest"] != a["artifact"]["digest"] or v["policy_digest"] != a["policy_digest"]):
                raise RuntimeFault("artifact_not_verified", "Only current verified artifact can be published", 409)
            s.update(state="succeeded", accepted_artifact=a["artifact"]["ref"])
            self._put("session", node_id, s["run_id"], s, True)

    async def finish_session(self, node_id: str, state: str, error: str | None, usage: dict) -> None:
        with self.db:
            s = self._get("session", node_id)
            s.update(state=state, error=error, usage=usage)
            for t in self._list("ticket", node_id):
                if t["state"] == "pending":
                    t.update(state="expired", revision=t["revision"] + 1)
                    self._put("ticket", t["id"], node_id, t, True)
            if error == "workflow_interrupted" and s["current_attempt"]:
                a = self._get("attempt", s["current_attempt"])
                if a["state"] not in {"completed", "failed", "cancelled"}:
                    a.update(state="failed", error_code=error, revision=a["revision"] + 1)
                    self._put("attempt", a["id"], node_id, a, True)
            self._put("session", node_id, s["run_id"], s, True)

    async def create_ticket(self, value: dict) -> None:
        with self.db:
            self._put("ticket", value["id"], value["node_id"], value)

    async def ticket(self, ticket_id: str) -> dict:
        await self.open()
        return self._get("ticket", ticket_id)

    async def tickets(self, node_id: str | None = None) -> tuple[dict, ...]:
        await self.open()
        return self._list("ticket", node_id)

    async def resolve_ticket(self, ticket_id: str, expected_revision: int, attempt_id: str,
                             artifact: VersionRef, decision: str, actor: str) -> None:
        with self.db:
            t = self._get("ticket", ticket_id)
            s = self._get("session", t["node_id"])
            if (decision not in {"retry", "abort"} or t["state"] != "pending" or t["revision"] != expected_revision
                    or t["attempt_id"] != attempt_id or t["artifact_ref"] != asdict(artifact)
                    or s["current_attempt"] != attempt_id or s["state"] != "running"):
                raise RuntimeFault("stale_intervention", "Intervention expired or does not match current artifact", 409)
            t.update(state=decision, actor=actor, revision=expected_revision + 1)
            self._put("ticket", ticket_id, t["node_id"], t, True)
