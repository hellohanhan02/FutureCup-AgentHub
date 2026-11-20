"""Scheduling audit and observed statistics, sharing the workflow SQLite owner."""

from dataclasses import asdict
from datetime import datetime
import json
import math

from futurecup.adapters.workflow_store import SQLiteWorkflowRepository, encode
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import VersionRef
from futurecup.domain.scheduling import CapabilityObservation, PerformanceStats, SchedulingDecision


class SQLiteSchedulingRepository:
    def __init__(self, workflows: SQLiteWorkflowRepository) -> None:
        self.workflows = workflows
        self._opened = False

    async def open(self) -> None:
        await self.workflows.open()
        if self._opened:
            return
        self.workflows.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_observations (
                execution_id TEXT PRIMARY KEY, observation TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS capability_recent_idx ON capability_observations(
                json_extract(observation,'$.agent_ref.id'),json_extract(observation,'$.agent_ref.version'),
                json_extract(observation,'$.task_type'),json_extract(observation,'$.source'));
            CREATE TABLE IF NOT EXISTS capability_statistics (
                agent_id TEXT NOT NULL, version TEXT NOT NULL, task_type TEXT NOT NULL,
                source TEXT NOT NULL, statistics TEXT NOT NULL,
                PRIMARY KEY(agent_id, version, task_type, source));
            CREATE TABLE IF NOT EXISTS scheduling_decisions (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, run_id TEXT NOT NULL, decision TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS scheduling_task_idx ON scheduling_decisions(task_id);
            CREATE INDEX IF NOT EXISTS scheduling_run_idx ON scheduling_decisions(run_id);
        """)
        self.workflows.db.commit()
        self._opened = True

    @staticmethod
    def _stats(data: str) -> PerformanceStats:
        value = json.loads(data)
        value["updated_at"] = datetime.fromisoformat(value["updated_at"]) if value["updated_at"] else None
        return PerformanceStats(**value)

    async def stats(self, ref: VersionRef, task_type: str, source: str) -> PerformanceStats:
        await self.open()
        row = self.workflows.db.execute("SELECT statistics FROM capability_statistics WHERE agent_id=? AND version=? AND task_type=? AND source=?",
                                        (ref.id, ref.version, task_type, source)).fetchone()
        return self._stats(row[0]) if row else PerformanceStats(task_type, source)

    async def history(self, ref: VersionRef) -> tuple[PerformanceStats, ...]:
        await self.open()
        rows = self.workflows.db.execute("SELECT statistics FROM capability_statistics WHERE agent_id=? AND version=? ORDER BY task_type,source", (ref.id, ref.version)).fetchall()
        return tuple(self._stats(row[0]) for row in rows)

    async def observe(self, observation: CapabilityObservation) -> bool:
        await self.open()
        if (observation.outcome_category not in {'success','agent_failure','tool_failure','timeout','unavailable','cancelled','validation_failure','unknown'}
                or observation.source not in {"observed_mock", "observed_real", "simulation"}
                or not math.isfinite(observation.duration_ms) or observation.duration_ms < 0
                or observation.succeeded and observation.cancelled):
            raise RuntimeFault("observation_invalid", "Invalid execution observation", 422)
        stats = await self.stats(observation.agent_ref, observation.task_type, observation.source)
        count = stats.sample_count
        mean = stats.mean_duration_ms
        if not observation.cancelled:
            mean = ((mean or 0) * count + observation.duration_ms) / (count + 1)
        updated = PerformanceStats(
            stats.task_type, stats.source, stats.successes + int(observation.succeeded),
            stats.failures + int(not observation.succeeded and not observation.cancelled),
            stats.cancellations + int(observation.cancelled), mean, stats.revision + 1, observation.occurred_at,
        )
        with self.workflows.db:
            inserted = self.workflows.db.execute("INSERT OR IGNORE INTO capability_observations VALUES (?, ?)",
                                                (observation.execution_id, encode(asdict(observation))))
            if not inserted.rowcount:
                return False
            self.workflows.db.execute("INSERT INTO capability_statistics VALUES (?,?,?,?,?) ON CONFLICT(agent_id,version,task_type,source) DO UPDATE SET statistics=excluded.statistics",
                (observation.agent_ref.id, observation.agent_ref.version, stats.task_type, stats.source, encode(asdict(updated))))
        return True

    async def adaptive_history(self, ref: VersionRef, task_type: str, source: str) -> tuple[dict, ...]:
        await self.open()
        rows = self.workflows.db.execute("""SELECT observation FROM capability_observations
            WHERE json_extract(observation,'$.agent_ref.id')=? AND json_extract(observation,'$.agent_ref.version')=?
            AND json_extract(observation,'$.task_type')=? AND json_extract(observation,'$.source')=?
            ORDER BY rowid DESC LIMIT 32""",(ref.id,ref.version,task_type,source)).fetchall()
        return tuple(json.loads(row[0]) for row in reversed(rows))

    async def record(self, decision: SchedulingDecision) -> None:
        await self.open()
        with self.workflows.db:
            self.workflows.db.execute("INSERT INTO scheduling_decisions VALUES (?,?,?,?)",
                                     (decision.id, decision.task_id, decision.workflow_run_id, encode(asdict(decision))))

    async def decisions(self, task_id: str | None = None, run_id: str | None = None, limit: int = 100) -> tuple[dict, ...]:
        await self.open()
        rows = self.workflows.db.execute("SELECT rowid, decision FROM scheduling_decisions WHERE (? IS NULL OR task_id=?) AND (? IS NULL OR run_id=?) ORDER BY rowid DESC LIMIT ?",
                                        (task_id, task_id, run_id, run_id, limit)).fetchall()
        return tuple({**json.loads(row[1]), "sequence": row[0]} for row in rows)
