"""Independent SQLite recovery journal. No remote calls, credentials or implicit repair."""
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from futurecup.domain.errors import RuntimeFault
from futurecup.domain.ledger import CallContext, ReconciliationFinding, refused


def canonical(value: object) -> str:
    return json.dumps(value,sort_keys=True,separators=(",", ":"),ensure_ascii=False,allow_nan=False)


def digest(value: object) -> str:
    return sha256(canonical(value).encode('utf8')).hexdigest()


class SQLiteRecoveryLedger:
    digest = staticmethod(digest)
    def __init__(self, path: str, *, worker_id: str | None = None) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        existing = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith('sqlite_')}
        allowed = {'ledger_workers','ledger_accounts','ledger_reservations','ledger_tools','ledger_tool_refs','ledger_recoveries'}
        if existing - allowed:
            self.db.close()
            raise refused('ledger_database_not_isolated')
        self.lock = RLock()
        self.worker_id = worker_id or uuid4().hex
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS ledger_workers(id TEXT PRIMARY KEY, fenced INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ledger_accounts(
                id TEXT PRIMARY KEY, owner_id TEXT UNIQUE NOT NULL, max_calls INTEGER NOT NULL,
                max_units INTEGER NOT NULL, unit TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger_reservations(
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES ledger_accounts(id),
                worker_id TEXT NOT NULL, identity TEXT NOT NULL, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
                execution_id TEXT NOT NULL, kind TEXT NOT NULL, input_hash TEXT NOT NULL,
                reserved INTEGER NOT NULL, actual INTEGER, state TEXT NOT NULL,
                dispatched INTEGER NOT NULL DEFAULT 0, evidence_id TEXT);
            CREATE TABLE IF NOT EXISTS ledger_tools(
                id TEXT PRIMARY KEY, operation_key TEXT UNIQUE NOT NULL, reservation_id TEXT NOT NULL,
                worker_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
                tool_name TEXT NOT NULL, tool_version TEXT NOT NULL, input_hash TEXT NOT NULL,
                side_effect_class TEXT NOT NULL, state TEXT NOT NULL,
                result_digest TEXT, result_json TEXT, replay_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ledger_tool_refs(
                reservation_id TEXT PRIMARY KEY REFERENCES ledger_reservations(id), tool_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL, run_id TEXT NOT NULL, node_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger_recoveries(
                tool_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, worker_id TEXT NOT NULL, reservation_id TEXT NOT NULL);
        """)
        with self.transaction():
            # A worker ID is a process session, never reused after a fence/restart.
            try:
                self.db.execute("INSERT INTO ledger_workers(id) VALUES(?)", (self.worker_id,))
            except sqlite3.IntegrityError:
                raise refused("ledger_worker_identity_reused") from None

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _active(self) -> None:
        row = self.db.execute("SELECT fenced FROM ledger_workers WHERE id=?", (self.worker_id,)).fetchone()
        if row is None or row[0]:
            raise refused("ledger_worker_fenced")

    def create_account(self, account_id: str, owner_id: str, *, max_calls: int,
                       max_units: int, unit: str = "resource_units") -> dict:
        if any(type(v) is not int or not 0 <= v <= 10**12 for v in (max_calls, max_units)):
            raise ValueError("Invalid integer budget limits")
        if not account_id or not owner_id or unit not in {"resource_units", "estimated_micro_cny"}:
            raise ValueError("Only integer resource or configured CNY-estimate units; never supplier billing")
        with self.transaction():
            self._active()
            rows = self.db.execute("SELECT * FROM ledger_accounts WHERE id=? OR owner_id=?", (account_id, owner_id)).fetchall()
            expected = dict(id=account_id, owner_id=owner_id, max_calls=max_calls, max_units=max_units, unit=unit)
            if rows:
                if len(rows) != 1 or dict(rows[0]) != expected:
                    raise refused("ledger_account_identity_conflict")
            else:
                self.db.execute("INSERT INTO ledger_accounts VALUES(?,?,?,?,?)", tuple(expected.values()))
        return self.account(account_id)

    def account(self, account_id: str) -> dict:
        with self.lock:
            row = self.db.execute("SELECT * FROM ledger_accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                raise refused("ledger_account_missing")
            reservations = self.db.execute("SELECT * FROM ledger_reservations WHERE account_id=?", (account_id,)).fetchall()
            used = [r for r in reservations if r['state'] != 'RELEASED']
            return {**dict(row), 'occupied_calls': len(used),
                    'occupied_units': sum(r['actual'] if r['state'] == 'COMMITTED' and r['actual'] is not None else r['reserved'] for r in used),
                    'model_calls': sum(r['kind'] == 'model' for r in used),
                    'tool_calls': sum(r['kind'] == 'tool' for r in used),
                    'committed_calls': sum(r['state'] == 'COMMITTED' for r in used),
                    'unknown_calls': sum(r['state'] == 'UNKNOWN' for r in used)}

    def reservation(self, identifier: str) -> dict:
        row = self.db.execute("SELECT * FROM ledger_reservations WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise refused("ledger_reservation_missing")
        return dict(row)

    def reserve(self, context: CallContext) -> dict:
        if context.kind not in ('model', 'tool') or type(context.units) is not int or context.units < 0:
            raise ValueError('Invalid resource reservation')
        identity = digest(asdict(context))
        with self.transaction():
            self._active()
            old = self.db.execute("SELECT * FROM ledger_reservations WHERE id=?", (context.reservation_id,)).fetchone()
            if old:
                if old['identity'] != identity:
                    raise refused('ledger_reservation_identity_conflict')
                return dict(old)
            account = self.account(context.account_id)
            self._check_integrity(context.account_id)
            if account['occupied_calls'] >= account['max_calls'] or account['occupied_units'] + context.units > account['max_units']:
                raise refused('ledger_budget_exhausted')
            self.db.execute("INSERT INTO ledger_reservations VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'RESERVED',0,NULL)",
                (context.reservation_id, context.account_id, self.worker_id, identity, context.run_id, context.node_id,
                 context.execution_id, context.kind, context.input_hash, context.units))
            return self.reservation(context.reservation_id)

    def _check_integrity(self, account_id: str) -> None:
        rows = self.db.execute('SELECT * FROM ledger_reservations WHERE account_id=?',(account_id,)).fetchall()
        for row in rows:
            if (row['state'] not in {'RESERVED','COMMITTED','RELEASED','UNKNOWN'} or row['reserved'] < 0
                    or row['state'] == 'COMMITTED' and (row['actual'] is None or not 0 <= row['actual'] <= row['reserved'] or not row['evidence_id'] or not row['dispatched'])
                    or row['state'] == 'RELEASED' and row['dispatched']):
                raise refused('ledger_integrity_error')

    def _owned(self, identifier: str) -> dict:
        self._active()
        row = self.reservation(identifier)
        if row['worker_id'] != self.worker_id:
            raise refused('ledger_not_owner')
        return row

    def dispatch(self, identifier: str) -> None:
        with self.transaction():
            row = self._owned(identifier)
            if row['state'] != 'RESERVED' or row['dispatched']:
                raise refused('ledger_dispatch_conflict')
            self.db.execute("UPDATE ledger_reservations SET dispatched=1 WHERE id=?", (identifier,))

    def _commit(self, identifier: str, units: int, evidence_id: str) -> None:
        row = self._owned(identifier)
        if type(units) is not int or not 0 <= units <= row['reserved'] or not evidence_id:
            raise refused('ledger_settlement_invalid')
        if row['state'] == 'COMMITTED':
            if row['actual'] != units or row['evidence_id'] != evidence_id:
                raise refused('ledger_settlement_conflict')
            return
        if row['state'] != 'RESERVED' or not row['dispatched']:
            raise refused('ledger_settlement_requires_evidence')
        self.db.execute("UPDATE ledger_reservations SET state='COMMITTED',actual=?,evidence_id=? WHERE id=?", (units, evidence_id, identifier))

    def commit(self, identifier: str, units: int, evidence_id: str) -> None:
        with self.transaction():
            self._active()
            row = self.reservation(identifier)
            if row['state'] == 'COMMITTED':
                if type(units) is not int or row['actual'] != units or row['evidence_id'] != evidence_id:
                    raise refused('ledger_settlement_conflict')
                return  # Lost acknowledgement: terminal evidence is immutable across sessions.
            self._commit(identifier, units, evidence_id)

    def release(self, identifier: str) -> None:
        with self.transaction():
            self._active()
            if self.reservation(identifier)['state'] == 'RELEASED':
                return
            row = self._owned(identifier)
            if row['state'] != 'RESERVED' or row['dispatched']:
                raise refused('ledger_release_unsafe')
            self.db.execute("UPDATE ledger_reservations SET state='RELEASED' WHERE id=?", (identifier,))

    def uncertain(self, identifier: str) -> None:
        with self.transaction():
            row = self._owned(identifier)
            if row['state'] == 'RESERVED':
                self.db.execute("UPDATE ledger_reservations SET state='UNKNOWN' WHERE id=?", (identifier,))

    def recover(self, dead_worker: str, *, confirmed_stopped: bool = False) -> dict:
        if not confirmed_stopped or dead_worker == self.worker_id:
            raise refused('ledger_recovery_confirmation_required')
        with self.transaction():
            self._active()
            if self.db.execute("SELECT id FROM ledger_workers WHERE id=?", (dead_worker,)).fetchone() is None:
                raise refused('ledger_worker_missing')
            self.db.execute("UPDATE ledger_workers SET fenced=1 WHERE id=?", (dead_worker,))
            self.db.execute("UPDATE ledger_tools SET state='UNKNOWN' WHERE worker_id=? AND state='STARTED'", (dead_worker,))
            self.db.execute("UPDATE ledger_reservations SET state=CASE WHEN dispatched=1 THEN 'UNKNOWN' ELSE 'RELEASED' END WHERE worker_id=? AND state='RESERVED'", (dead_worker,))
            return {'worker_id': dead_worker, 'diagnosis': 'dead_session_fenced; dispatched charges retained',
                    'unknown': self.db.execute("SELECT COUNT(*) FROM ledger_reservations WHERE worker_id=? AND state='UNKNOWN'", (dead_worker,)).fetchone()[0]}

    def tool(self, identifier: str) -> dict:
        row = self.db.execute("SELECT * FROM ledger_tools WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise refused('ledger_tool_missing')
        return dict(row)

    def start_tool(self, context: CallContext, *, tool_call_id: str, tool_name: str,
                   tool_version: str, side_effect_class: str, replay_scope: str) -> dict:
        if side_effect_class not in ('none', 'idempotent', 'irreversible', 'unknown') or context.kind != 'tool':
            raise ValueError('Invalid tool declaration')
        key = digest((context.account_id, replay_scope, tool_name, tool_version)) if side_effect_class != 'none' else context.reservation_id
        with self.transaction():
            reservation = self._owned(context.reservation_id)
            if reservation['identity'] != digest(asdict(context)):
                raise refused('ledger_tool_identity_conflict')
            alias = self.db.execute("SELECT * FROM ledger_tool_refs WHERE reservation_id=?", (context.reservation_id,)).fetchone()
            if alias:
                if alias['tool_call_id'] != tool_call_id:
                    raise refused('ledger_tool_identity_conflict')
                previous = self.tool(alias['tool_id'])
                if (previous['operation_key'] != key or previous['tool_name'] != tool_name or previous['tool_version'] != tool_version
                        or previous['side_effect_class'] != side_effect_class or previous['input_hash'] != context.input_hash):
                    raise refused('ledger_tool_identity_conflict')
                return {**previous, 'cached': previous['state'] == 'SUCCEEDED', 'claimed': False}
            existing = self.db.execute("SELECT * FROM ledger_tools WHERE operation_key=?", (key,)).fetchone()
            if existing:
                if existing['input_hash'] != context.input_hash or existing['side_effect_class'] != side_effect_class:
                    raise refused('tool_replay_conflict')
                if existing['state'] != 'SUCCEEDED':
                    raise refused('tool_replay_uncertain')
                self.tool_result(existing['id'])  # Fail closed on missing/corrupt persisted result.
                self.db.execute("INSERT INTO ledger_tool_refs VALUES(?,?,?,?,?)", (context.reservation_id, existing['id'], tool_call_id, context.run_id, context.node_id))
                self.db.execute("UPDATE ledger_reservations SET dispatched=1 WHERE id=?", (context.reservation_id,))
                self._commit(context.reservation_id, context.units, 'tool-result:'+existing['result_digest'])
                return {**dict(existing), 'cached': True, 'claimed': False}
            if reservation['state'] != 'RESERVED' or reservation['dispatched']:
                raise refused('ledger_tool_state_conflict')
            identifier = uuid4().hex
            self.db.execute("INSERT INTO ledger_tools VALUES(?,?,?,?,?,?,?,?,?,?,?,'STARTED',NULL,NULL,0)",
                (identifier,key,context.reservation_id,self.worker_id,tool_call_id,context.run_id,context.node_id,tool_name,tool_version,context.input_hash,side_effect_class))
            self.db.execute("INSERT INTO ledger_tool_refs VALUES(?,?,?,?,?)", (context.reservation_id,identifier,tool_call_id,context.run_id,context.node_id))
            self.db.execute("UPDATE ledger_reservations SET dispatched=1 WHERE id=?", (context.reservation_id,))
            return {**self.tool(identifier), 'cached': False, 'claimed': True}

    def succeed_tool(self, tool_id: str, reservation_id: str, result: object) -> None:
        body = canonical(result)
        if len(body.encode('utf8')) > 65536:
            raise refused('ledger_result_too_large')
        with self.transaction():
            tool = self.tool(tool_id)
            self._active()
            if tool['state'] == 'SUCCEEDED' and tool['reservation_id'] == reservation_id:
                if digest(self.tool_result(tool_id)) != digest(result):
                    raise refused('ledger_tool_completion_conflict')
                return
            if tool['worker_id'] != self.worker_id or tool['reservation_id'] != reservation_id or tool['state'] != 'STARTED':
                raise refused('ledger_tool_completion_conflict')
            row = self._owned(reservation_id)
            result_hash = digest(result)
            self._commit(reservation_id, row['reserved'], 'tool-result:'+result_hash)
            self.db.execute("UPDATE ledger_tools SET state='SUCCEEDED',result_digest=?,result_json=? WHERE id=?", (result_hash,body,tool_id))

    def fail_tool(self, tool_id: str, reservation_id: str) -> None:
        with self.transaction():
            tool = self.tool(tool_id)
            row = self._owned(reservation_id)
            if tool['worker_id'] != self.worker_id or tool['reservation_id'] != reservation_id or tool['state'] != 'STARTED':
                raise refused('ledger_tool_completion_conflict')
            state = 'FAILED' if tool['side_effect_class'] == 'none' else 'UNKNOWN'
            self.db.execute('UPDATE ledger_tools SET state=? WHERE id=?', (state,tool_id))
            if state == 'FAILED':
                self._commit(reservation_id,row['reserved'],'tool-known-failure:'+tool_id)
            else:
                self.db.execute("UPDATE ledger_reservations SET state='UNKNOWN' WHERE id=?", (reservation_id,))

    def reusable_tool(self, account_id: str, scope: str, tool_name: str, version: str) -> bool:
        key = digest((account_id,scope,tool_name,version))
        with self.lock:
            row = self.db.execute('SELECT * FROM ledger_tools WHERE operation_key=?',(key,)).fetchone()
            if row is None or row['state'] != 'SUCCEEDED' or row['side_effect_class'] != 'idempotent':
                return False
            try:
                self.tool_result(row['id'])
                return True
            except RuntimeFault:
                return False

    def tool_result(self, identifier: str) -> object:
        tool = self.tool(identifier)
        try:
            value = json.loads(tool['result_json'])
            if tool['state'] != 'SUCCEEDED' or digest(value) != tool['result_digest']:
                raise ValueError
            return value
        except (ValueError,TypeError):
            raise refused('ledger_tool_result_missing_or_corrupt') from None

    def claim_unknown_tool(self, tool_id: str, request_id: str, *, allow_idempotent: bool = False) -> bool:
        with self.transaction():
            self._active()
            tool = self.tool(tool_id)
            previous = self.db.execute('SELECT * FROM ledger_recoveries WHERE tool_id=?',(tool_id,)).fetchone()
            if previous:
                return False  # A request key or a different worker must never dispatch twice.
            if tool['state'] != 'UNKNOWN' or tool['side_effect_class'] not in ('none','idempotent') or not allow_idempotent:
                raise refused('ledger_tool_manual_reconciliation_required')
            owner = self.db.execute('SELECT fenced FROM ledger_workers WHERE id=?',(tool['worker_id'],)).fetchone()
            if owner is None or not owner[0] or tool['replay_count'] >= 1:
                raise refused('ledger_tool_recovery_unsafe')
            reservation = self.reservation(tool['reservation_id'])
            if reservation['state'] != 'UNKNOWN':
                raise refused('ledger_tool_budget_inconsistent')
            # A new physical invocation consumes NEW capacity. The old UNKNOWN hold is never erased.
            account = self.account(reservation['account_id'])
            self._check_integrity(reservation['account_id'])
            if account['occupied_calls'] >= account['max_calls'] or account['occupied_units']+reservation['reserved'] > account['max_units']:
                raise refused('ledger_budget_exhausted')
            new_id = 'tool-recovery:'+tool_id+':'+request_id
            context = CallContext(reservation['account_id'],new_id,tool['run_id'],tool['node_id'],
                                  reservation['execution_id'],'tool',tool['input_hash'],reservation['reserved'])
            self.db.execute("INSERT INTO ledger_reservations VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'RESERVED',1,NULL)",
                (new_id,context.account_id,self.worker_id,digest(asdict(context)),context.run_id,context.node_id,
                 context.execution_id,'tool',context.input_hash,context.units))
            self.db.execute('INSERT INTO ledger_recoveries VALUES(?,?,?,?)',(tool_id,request_id,self.worker_id,new_id))
            self.db.execute("UPDATE ledger_tools SET worker_id=?,reservation_id=?,state='STARTED',replay_count=replay_count+1 WHERE id=?",(self.worker_id,new_id,tool_id))
            return True

    def reconcile(self, observations: tuple[dict, ...] = (), *, complete_inventory: bool = False) -> tuple[ReconciliationFinding, ...]:
        """Read-only diagnostics; absence is meaningful only for a declared complete inventory."""
        findings = []
        with self.transaction():
            rows = self.db.execute('SELECT * FROM ledger_reservations').fetchall()
            seen = {v['reservation_id']: v for v in observations}
            if len(seen) != len(observations):
                findings.append(ReconciliationFinding('duplicate_execution_evidence','inventory'))
            for account in self.db.execute('SELECT id FROM ledger_accounts').fetchall():
                usage = self.account(account['id'])
                if usage['occupied_calls'] > usage['max_calls'] or usage['occupied_units'] > usage['max_units']:
                    findings.append(ReconciliationFinding('account_budget_exceeded',account['id']))
            for row in rows:
                observed = seen.get(row['id'])
                if (row['state'] not in {'RESERVED','COMMITTED','RELEASED','UNKNOWN'} or row['reserved'] < 0
                        or row['state'] == 'COMMITTED' and (row['actual'] is None or not 0 <= row['actual'] <= row['reserved'] or not row['evidence_id'])):
                    findings.append(ReconciliationFinding('reservation_invariant_broken',row['id']))
                if row['state'] == 'UNKNOWN':
                    findings.append(ReconciliationFinding('budget_settlement_unknown',row['id']))
                if observed:
                    if observed.get('input_hash') != row['input_hash'] or observed.get('units') != row['actual']:
                        findings.append(ReconciliationFinding('budget_execution_mismatch',row['id']))
                    if observed.get('state') == 'succeeded' and row['state'] != 'COMMITTED':
                        findings.append(ReconciliationFinding('execution_without_committed_budget',row['id']))
                elif complete_inventory and row['state'] == 'COMMITTED':
                    findings.append(ReconciliationFinding('budget_without_execution_record',row['id']))
            known = {r['id'] for r in rows}
            for identifier in seen.keys()-known:
                findings.append(ReconciliationFinding('execution_without_reservation',identifier))
            for tool in self.db.execute('SELECT * FROM ledger_tools').fetchall():
                if tool['state'] == 'SUCCEEDED':
                    try:
                        self.tool_result(tool['id'])
                    except RuntimeFault:
                        findings.append(ReconciliationFinding('tool_result_missing_or_corrupt',tool['id']))
                    reservation = self.reservation(tool['reservation_id'])
                    if reservation['state'] != 'COMMITTED':
                        findings.append(ReconciliationFinding('tool_budget_state_mismatch',tool['id']))
                    observation = seen.get(tool['reservation_id'])
                    if observation and observation.get('state') != 'succeeded':
                        findings.append(ReconciliationFinding('tool_workflow_state_mismatch',tool['id']))
                    if observation and observation.get('result_digest') != tool['result_digest']:
                        findings.append(ReconciliationFinding('tool_workflow_result_mismatch',tool['id']))
                elif tool['state'] == 'UNKNOWN':
                    findings.append(ReconciliationFinding('tool_outcome_unknown',tool['id']))
        return tuple(findings)

    def public_view(self, account_id: str) -> dict:
        """Scoped read model. Never return arguments, result bodies, worker IDs or raw keys."""
        with self.transaction():
            account = self.account(account_id)
            rows = [dict(r) for r in self.db.execute('SELECT * FROM ledger_reservations WHERE account_id=?',(account_id,))]
            tools = [dict(r) for r in self.db.execute('SELECT t.* FROM ledger_tools t JOIN ledger_reservations r ON r.id=t.reservation_id WHERE r.account_id=?',(account_id,))]
            findings = []
            try:
                self._check_integrity(account_id)
            except RuntimeFault as fault:
                findings.append({'code':fault.code,'action':'manual_review'})
            for tool in tools:
                if tool['state'] == 'SUCCEEDED':
                    try:self.tool_result(tool['id'])
                    except RuntimeFault:findings.append({'code':'tool_result_missing_or_corrupt','record_id':tool['id'],'action':'manual_review'})
                elif tool['state'] == 'UNKNOWN':
                    findings.append({'code':'tool_outcome_unknown','record_id':tool['id'],'action':'manual_review'})
            if account['unknown_calls']:
                findings.append({'code':'budget_settlement_unknown','action':'external_reconciliation_required'})
            public_tools = [{k:t[k] for k in ('id','tool_call_id','run_id','node_id','tool_name','tool_version','side_effect_class','state','result_digest','replay_count')} | {
                'input_hash_summary':t['input_hash'][:16],'idempotency_summary':t['operation_key'][:16]} for t in tools[:1000]]
            return {'available':True,'account_id':account_id,'unit':account['unit'],
                'max_calls':account['max_calls'],'max_units':account['max_units'],
                'occupied_calls':account['occupied_calls'],'confirmed_calls':account['committed_calls'],'unknown_calls':account['unknown_calls'],
                'reserved_calls':sum(r['state']=='RESERVED' for r in rows),
                'confirmed_units':sum(r['actual'] for r in rows if r['state']=='COMMITTED' and r['actual'] is not None),
                'reserved_units':sum(r['reserved'] for r in rows if r['state']=='RESERVED'),
                'unknown_units':sum(r['reserved'] for r in rows if r['state']=='UNKNOWN'),
                'remaining_calls':account['max_calls']-account['occupied_calls'],
                'remaining_units':account['max_units']-account['occupied_units'],
                'tools':public_tools,'findings':findings,'truncated':len(tools)>1000,
                'scope':'shared_account_including_source_and_recovery','reconciliation_scope':'ledger_internal_only; full execution inventory not supplied',
                'cost_basis':'resource_quota_or_configured_estimate_not_supplier_invoice'}
