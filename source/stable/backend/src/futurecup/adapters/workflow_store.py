"""SQLite workflow definitions, atomic snapshots and append-only events.

Small bounded local transactions; no database lock is held across model calls.
The connection is opened lazily so importing the API never creates a database.
"""

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import (
    NodeKind, RecoveryPolicy, RuntimeEvent, TaskState, VersionRef, WorkflowDefinition,
    WorkflowEdge, WorkflowNode, WorkflowNodeRun, WorkflowReport, WorkflowRun, WorkflowState, JsonValue, ModelCallRecord,
)


def encode(value: object) -> str:
    def default(item: object) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError("Unsupported workflow data")
    return json.dumps(value, default=default, allow_nan=False, ensure_ascii=False)


def definition_from(data: dict) -> WorkflowDefinition:
    return WorkflowDefinition(
        ref=VersionRef(**data["ref"]), nodes=tuple(WorkflowNode(n["id"], NodeKind(n["kind"]), n["config"]) for n in data["nodes"]),
        edges=tuple(WorkflowEdge(**edge) for edge in data["edges"]), input_schema=data["input_schema"],
        output_schema=data["output_schema"], max_subworkflow_depth=data["max_subworkflow_depth"],
        name=data["name"], output_mapping=data["output_mapping"], max_concurrency=data["max_concurrency"],
        timeout_seconds=data["timeout_seconds"], max_node_executions=data["max_node_executions"], error_policy=data["error_policy"],
        scheduling_strategy=data.get("scheduling_strategy", "caas"), resource_limits=data.get("resource_limits", {}),
    )


def report_from(data: dict) -> WorkflowReport:
    metadata = data.pop("run")
    policy = metadata["policy"]
    policy["deadline"] = datetime.fromisoformat(policy["deadline"])
    metadata.update(definition_ref=VersionRef(**metadata["definition_ref"]), policy=RecoveryPolicy(**policy),
                    state=WorkflowState(metadata["state"]))
    nodes = []
    for node in data.pop("nodes"):
        for key in ("started_at", "ended_at"):
            node[key] = datetime.fromisoformat(node[key]) if node[key] else None
        node["state"] = TaskState(node["state"])
        for key in ("ready_at", "assigned_at"):
            node[key] = datetime.fromisoformat(node[key]) if node.get(key) else None
        node["child_run_ids"] = tuple(node["child_run_ids"])
        for key in ("attempt_ids", "verification_ids", "feedback_ids"):
            node[key] = tuple(node.get(key, []))
        node["current_artifact_ref"] = VersionRef(**node["current_artifact_ref"]) if node.get("current_artifact_ref") else None
        node["agent_ref"] = VersionRef(**node["agent_ref"]) if node["agent_ref"] else None
        node["model_calls"] = tuple(ModelCallRecord(**call) for call in node.get("model_calls", []))
        nodes.append(WorkflowNodeRun(**node))
    events = []
    for event in data.pop("events"):
        event["occurred_at"] = datetime.fromisoformat(event["occurred_at"])
        events.append(RuntimeEvent(**event))
    for key in ("started_at", "ended_at"):
        data[key] = datetime.fromisoformat(data[key]) if data[key] else None
    data["agent_bindings"] = {key: VersionRef(**value) for key, value in data["agent_bindings"].items()}
    return WorkflowReport(WorkflowRun(**metadata), nodes=tuple(nodes), events=tuple(events), **data)


class JsonSchemaWorkflowValidator:
    def check_schema(self, schema: dict[str, JsonValue]) -> None:
        def references(value: object) -> bool:
            if isinstance(value, dict):
                return any(key in {"$ref", "$dynamicRef"} or references(item) for key, item in value.items())
            return isinstance(value, list) and any(references(item) for item in value)
        try:
            if not isinstance(schema, dict) or references(schema) or len(encode(schema)) > 32768:
                raise ValueError
            Draft202012Validator.check_schema(schema)
        except (SchemaError, ValueError, TypeError, RecursionError):
            raise RuntimeFault("workflow_schema_invalid", "Invalid schema; external references are not supported", 422) from None

    def validate(self, schema: dict[str, JsonValue], value: JsonValue, code: str) -> None:
        try:
            encode(value)
            Draft202012Validator(schema).validate(value)
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise RuntimeFault(code, "Workflow data does not match its schema", 422) from None


class SQLiteWorkflowRepository:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._db: sqlite3.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=3000")
        if self._db.execute("PRAGMA user_version").fetchone()[0] > 1:
            self._db.close()
            self._db = None
            raise RuntimeFault("workflow_schema_version", "Database schema is newer than this application", 409)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS workflow_definitions (
                id TEXT NOT NULL, version TEXT NOT NULL, definition TEXT NOT NULL,
                PRIMARY KEY(id, version));
            CREATE TABLE IF NOT EXISTS workflow_runs (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_events (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES workflow_runs(id),
                sequence INTEGER NOT NULL, event TEXT NOT NULL, UNIQUE(run_id, sequence));
            PRAGMA user_version=1;
        """)
        self._db.commit()

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeFault("workflow_store_closed", "Workflow repository is not open", 503)
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    async def register(self, definition: WorkflowDefinition) -> None:
        try:
            with self.db:
                self.db.execute("INSERT INTO workflow_definitions VALUES (?, ?, ?)",
                                (definition.ref.id, definition.ref.version, encode(asdict(definition))))
        except sqlite3.IntegrityError:
            raise RuntimeFault("workflow_exists", "Workflow version already exists", 409) from None

    async def get_definition(self, ref: VersionRef) -> WorkflowDefinition:
        row = self.db.execute("SELECT definition FROM workflow_definitions WHERE id=? AND version=?", (ref.id, ref.version)).fetchone()
        if row is None:
            raise RuntimeFault("workflow_not_found", "Workflow version not found", 404)
        return definition_from(json.loads(row[0]))

    async def latest(self, workflow_id: str) -> WorkflowDefinition:
        row = self.db.execute("SELECT definition FROM workflow_definitions WHERE id=? ORDER BY rowid DESC LIMIT 1", (workflow_id,)).fetchone()
        if row is None:
            raise RuntimeFault("workflow_not_found", "Workflow not found", 404)
        return definition_from(json.loads(row[0]))

    async def list_definitions(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(definition_from(json.loads(row[0])) for row in self.db.execute("SELECT definition FROM workflow_definitions ORDER BY rowid"))

    def _events(self, report: WorkflowReport) -> None:
        if ([event.sequence for event in report.events] != list(range(1, len(report.events) + 1))
                or any(event.workflow_run_id != report.run.id for event in report.events)):
            raise RuntimeFault("workflow_event_conflict", "Invalid workflow event sequence", 409)
        existing_events = [row[0] for row in self.db.execute(
            "SELECT event FROM workflow_events WHERE run_id=? ORDER BY sequence", (report.run.id,))]
        if (len(report.events) < len(existing_events) or
                any(encode(asdict(event)) != old for event, old in zip(report.events, existing_events))):
            raise RuntimeFault("workflow_event_conflict", "Existing event history is immutable", 409)
        for event in report.events:
            encoded = encode(asdict(event))
            existing = self.db.execute("SELECT run_id, sequence, event FROM workflow_events WHERE id=?", (event.id,)).fetchone()
            if existing:
                if existing != (report.run.id, event.sequence, encoded):
                    raise RuntimeFault("workflow_event_conflict", "An existing event is immutable", 409)
                continue
            try:
                self.db.execute("INSERT INTO workflow_events VALUES (?, ?, ?, ?)",
                                (event.id, report.run.id, event.sequence, encoded))
            except sqlite3.IntegrityError:
                raise RuntimeFault("workflow_event_conflict", "Workflow event sequence conflict", 409) from None

    async def create_report(self, report: WorkflowReport) -> None:
        with self.db:
            self.db.execute("INSERT INTO workflow_runs VALUES (?, ?, ?, ?)",
                            (report.run.id, report.run.state.value, report.run.revision, encode(asdict(report))))
            self._events(report)

    async def get_report(self, run_id: str) -> WorkflowReport:
        row = self.db.execute("SELECT snapshot FROM workflow_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeFault("workflow_run_not_found", "Workflow run not found", 404)
        return report_from(json.loads(row[0]))

    async def get_run(self, run_id: str) -> WorkflowRun:
        return (await self.get_report(run_id)).run

    async def save_report(self, report: WorkflowReport, expected_revision: int) -> None:
        if report.run.revision != expected_revision + 1:
            raise RuntimeFault("workflow_revision_conflict", "Snapshot revision must advance once", 409)
        with self.db:
            cursor = self.db.execute("UPDATE workflow_runs SET state=?, revision=?, snapshot=? WHERE id=? AND revision=?",
                (report.run.state.value, report.run.revision, encode(asdict(report)), report.run.id, expected_revision))
            if cursor.rowcount != 1:
                raise RuntimeFault("workflow_revision_conflict", "Workflow snapshot changed", 409)
            self._events(report)

    async def interrupted(self) -> tuple[WorkflowReport, ...]:
        rows = self.db.execute("SELECT snapshot FROM workflow_runs WHERE state IN ('created', 'running', 'paused')").fetchall()
        return tuple(report_from(json.loads(row[0])) for row in rows)

    async def recent_reports(self, limit: int = 200) -> tuple[WorkflowReport, ...]:
        await self.open()
        rows = self.db.execute("SELECT snapshot FROM workflow_runs WHERE json_extract(snapshot, '$.run.parent_run_id') IS NULL ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return tuple(report_from(json.loads(row[0])) for row in rows)
