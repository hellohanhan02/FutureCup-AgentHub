"""Allowlisted, read-only evidence extraction; never import runtime/settings/providers."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[4]
RUNS = ('ec09d758f81641f1a6c0f656c305a713', 'd2c530342bff47908a44a28505306ce7')
DEFAULT_PACKAGE = ROOT / '.data/competition-evidence-10b'
CHARTS = ('success-rate.svg','mean-latency.svg','p95-latency.svg')
# Only reviewed record/business fields. No headers/messages/prompts/credentials.
FIELDS = set('''id version ref run root_run_id definition_ref state policy parent_run_id revision nodes
node_id node_key workflow_run_id aggregate_id kind config edges source target when input output
input_ref input_digest input_value output_mapping agent_id agent_ref agent_bindings execution_id
child_run_ids error_code started_at ended_at elapsed_ms simulated verified ready_at assigned_at waiting_ms
scheduling_decision_id decision_id attempt_ids verification_ids feedback_ids current_artifact_ref fallback_activated
provider model resolved_model input_tokens output_tokens usage_complete cost_complete cost_basis
estimated_cost cost_currency provider_config_ref model_calls number provider_request_id usage_reported
currency retry_index http_status_code http_error_category resource_usage resources calls model_calls
tool_calls resource_units checks attempts feedback_loops replans reported_tokens unknown_usage_calls
events sequence type occurred_at payload attempt_id verification_id feedback_id ordinal tools artifact
digest content format_error execution_error policy_ref policy_digest record verdict reason_codes
verifier_ref evidence origin checks_used simulated_generation status reason path reference rule
schema_path details repair_hint violations before after before_position after_position task min max
actual_position latest actual_completion required_gap actual_gap expected_total actual_total
missing_tasks duplicate_tasks illegal_tasks invalid_task_positions expected_count actual_count
evaluation blocked_by category action repair_action strategy_patch created_at evidence_refs budget_cost
actual_budget_cost original_input recovery previous_attempt_id previous_artifact verification feedback
instruction reason_codes policy_ref source_evidence_count not_evaluated_count artifact_digest artifact_ref
request_id requirements text components issues summary analysis design review analyse schedule order total
instructions spec scenario_id tasks duration priority semantics constraints dependencies priority_before
immediately_before position_windows completion_deadlines min_position_gaps min_gap task_type strategy
min_gaps position_base single_machine idle_allowed
selected outcome effective_priority candidates agent_version eligible reasons components score profile
profile_version name scheduling_strategy max_concurrency timeout_seconds max_node_executions error_policy
max_subworkflow_depth limits recovery max_calls max_model_calls max_output_tokens model_timeout_seconds
max_attempts max_feedback_loops max_replans on_failure on_uncertain retry_execution_errors feedback_mode
resource_limits input_schema output_schema $ref $schema properties required additionalProperties const
enum items minItems maxItems uniqueItems minimum maximum minLength maxLength artifact_version
configuration match_weight reliability_weight load_weight cost_weight latency_weight cost_scale
latency_scale_ms prior_samples aging_points_per_second algorithm_revision dependent_count priority
node_id task_id workflow_run_id match reliability load cost latency'''.split())
SENSITIVE = re.compile(r'(?i)(authorization|cookie|bearer\s|api[ _-]?key|\bsk-[a-z0-9_-]{6,}|'
                       r'-----BEGIN|\bgh[pousr]_|github_pat_|\bAKIA[A-Z0-9]{16}|'
                       r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b1[3-9]\d{9}\b|[A-Z]:[\\/]|https?://)')


def sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def sanitize(value, counts: Counter, depth: int = 0, *, whitelist_keys: bool = True):
    """Drop unknown keys; redact suspicious/oversized text, not substrings of a secret."""
    if depth > 24:
        counts['depth_limit'] += 1
        return '[OMITTED]'
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if whitelist_keys and key not in FIELDS:
                counts['unlisted_fields'] += 1
                continue
            result[key] = sanitize(item, counts, depth + 1, whitelist_keys=whitelist_keys)
        return result
    if isinstance(value, list):
        if len(value) > 512:
            counts['list_limit'] += 1
        return [sanitize(item, counts, depth + 1, whitelist_keys=whitelist_keys) for item in value[:512]]
    if isinstance(value, str):
        if len(value) > 2048 or SENSITIVE.search(value):
            counts['redacted_strings'] += 1
            return '[REDACTED]'
        return value
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    counts['unsupported_value'] += 1
    return None


def extract(database: Path) -> tuple[dict, dict, dict]:
    database = database.resolve()
    if not database.is_relative_to(ROOT) or not database.is_file():
        raise ValueError('Existing project-local database required')
    before = sha(database.read_bytes())
    counts = Counter()
    package = {'reports': {}, 'definitions': [], 'audits': {}, 'decisions': []}
    summaries, records = [], []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')

        def saved(kind: str, identifier: str) -> dict:
            row = db.execute('SELECT body FROM safety_records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if not row:
                raise ValueError('Required audit record unavailable')
            records.append({'table': 'safety_records', 'kind': kind, 'id': identifier, 'sha256': sha(row[0])})
            return json.loads(row[0])

        for run_id in RUNS:
            row = db.execute('SELECT snapshot FROM workflow_runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise ValueError('Required historical run unavailable')
            report = json.loads(row[0])
            records.append({'table': 'workflow_runs', 'id': run_id, 'sha256': sha(row[0])})
            ref = report['run']['definition_ref']
            definition_text = db.execute('SELECT definition FROM workflow_definitions WHERE id=? AND version=?',
                                         (ref['id'], ref['version'])).fetchone()[0]
            definition = json.loads(definition_text)
            records.append({'table': 'workflow_definitions', 'id': ref['id'], 'version': ref['version'], 'sha256': sha(definition_text)})
            package['definitions'].append(sanitize(definition, counts))
            node_summary, raw_by_node = [], {}
            for node in report['nodes']:
                saved('session', node['id'])
                attempts = [saved('attempt', i) for i in node['attempt_ids']]
                verifications = [saved('verification', i) for i in node['verification_ids']]
                feedback = [saved('feedback', i) for i in node['feedback_ids']]
                raw_by_node[node['node_id']] = attempts
                feedback_bindings = []
                for a in attempts:
                    v = next(v for v in verifications if v['attempt_id'] == a['id'])
                    if a['artifact']['ref'] != v['artifact_ref'] or a['artifact']['digest'] != v['artifact_digest']:
                        raise ValueError('Artifact verification binding mismatch')
                    # SQLite safety policies are stored by versioned key.
                    policies = db.execute("SELECT id,body FROM safety_records WHERE kind='policy'").fetchall()
                    policy = next(((i,b) for i,b in policies if json.loads(b)['ref'] == a['policy_ref']), None)
                    if policy is None:
                        raise ValueError('Verification policy unavailable')
                    records.append({'table':'safety_records','kind':'policy','id':policy[0],'sha256':sha(policy[1])})
                    if a['ordinal'] > 1:
                        incoming = a['input_value']['recovery']
                        previous = next(x for x in attempts if x['id'] == incoming['previous_attempt_id'])
                        old_v = next(x for x in verifications if x['attempt_id'] == previous['id'])
                        f = next(x for x in feedback if x['record']['id'] == incoming['feedback']['id'])
                        bound = (incoming['previous_artifact']['content'] == previous['artifact']['content']
                            and incoming['previous_artifact']['digest'] == previous['artifact']['digest']
                            and incoming['verification']['id'] == old_v['record']['id']
                            and f['record']['verification_id'] == old_v['record']['id']
                            and old_v['record']['id'] in f['evidence_refs'])
                        feedback_bindings.append({'attempt_id':a['id'], 'previous_attempt_id':previous['id'],
                            'verification_id':old_v['record']['id'],'feedback_id':f['record']['id'], 'confirmed':bound})
                for v in verifications:
                    v['status'] = {'pass':'PASSED','fail':'FAILED','error':'ERROR','inconclusive':'INCONCLUSIVE'}[v['record']['verdict']]
                package['audits'][node['id']] = {
                    'attempts':sanitize(attempts, counts),'verifications':sanitize(verifications, counts),
                    'feedback':sanitize(feedback, counts)}
                node_summary.append({'node_id':node['node_id'],'node_instance_id':node['id'],
                    'agent_ref':node['agent_ref'],'state':node['state'],'started_at':node['started_at'],
                    'ended_at':node['ended_at'],'elapsed_ms':node['elapsed_ms'],
                    'attempts':[{'id':a['id'],'ordinal':a['ordinal'],'provider':a['provider'],'model':a['model'],
                        'resolved_model':a['resolved_model'],'agent_ref':a['agent_ref'],'policy_ref':a['policy_ref'],
                        'provider_config_ref':a['provider_config_ref'],'input_tokens':a['input_tokens'],
                        'output_tokens':a['output_tokens'],'usage_complete':a['usage_complete'],
                        'verdict':next(v['record']['verdict'] for v in verifications if v['attempt_id']==a['id']),
                        'verification_id':a['verification_id']} for a in attempts], 'feedback_bindings':feedback_bindings})
            mappings = []
            if {'analyse','design','review'}.issubset(raw_by_node):
                for upstream,downstream,key in [('analyse','design','analysis'),('analyse','review','analysis'),('design','review','design')]:
                    source = raw_by_node[upstream][-1]['artifact']['content']
                    received = raw_by_node[downstream][0]['input_value'][key]
                    mappings.append({'from':upstream,'to':downstream,'input_field':key,'matches':source==received,
                                     'source_content_sha256':sha(json.dumps(source,sort_keys=True,ensure_ascii=False,separators=(',',':')))})
            decisions = db.execute('SELECT id,decision FROM scheduling_decisions WHERE run_id=? ORDER BY rowid',(run_id,)).fetchall()
            for identifier,text in decisions:
                records.append({'table':'scheduling_decisions','id':identifier,'sha256':sha(text)})
                package['decisions'].append(sanitize(json.loads(text), counts))
            package['reports'][run_id] = sanitize(report, counts)
            summaries.append({'run_id':run_id,'state':report['run']['state'],'simulated':report['simulated'],
                'verified':report['verified'],'started_at':report['started_at'],'ended_at':report['ended_at'],
                'elapsed_ms':report['elapsed_ms'],'resource_usage':report['resource_usage'],'definition_ref':ref,
                'runtime_build_version':None,'runtime_build_version_status':'unknown_not_recorded',
                'execution_order':[n['node_id'] for n in sorted(report['nodes'],key=lambda n:n['started_at'] or '')],
                'nodes':node_summary,'data_mappings':mappings,'snapshot_sha256':sha(row[0])})
    if sha(database.read_bytes()) != before:
        raise ValueError('Source changed concurrently; no evidence exported')
    public = {'format_version':'10b-v1','exported_at':datetime.now(timezone.utc).isoformat(),
        'mode':'historical_read_only_replay','runs':summaries,'redactions':dict(counts),
        'notice':'Sanitized views; digests refer to original source records. Runtime build versions are unknown. No model is invoked.'}
    private = {'source_database':str(database.relative_to(ROOT)),'database_sha256':before,
               'exporter_sha256':sha(Path(__file__).read_bytes()),'records':records,
               'notice':'Original raw audits remain in the existing SQLite database; this is not a database backup.'}
    public = sanitize(public, counts, whitelist_keys=False)
    private = sanitize(private, counts, whitelist_keys=False)
    public['redactions'] = dict(counts)
    return package, public, private


def export(database: Path, output: Path) -> dict:
    output = output.resolve()
    if not output.is_relative_to(ROOT / '.data') or output.exists():
        raise ValueError('Output must be a NEW directory under project .data')
    package, public, private = extract(database)
    # Reuse all 10-A groups and their exact precomputed statistics, without selection.
    summary = ROOT/'results/scheduler-10a/summary.json'
    benchmark = json.loads(summary.read_text('utf-8')) if summary.is_file() else None
    public['benchmark_available'] = benchmark is not None
    charts = {}
    if benchmark is not None:
        original_manifest = json.loads((summary.parent/'manifest.json').read_text('utf-8'))
        if sha(summary.read_bytes()) != original_manifest['summary.json']:
            raise ValueError('Benchmark checksum mismatch')
        public['benchmark_source'] = {'path':'results/scheduler-10a/summary.json','sha256':sha(summary.read_bytes())}
        for name in CHARTS:
            text = (summary.parent/name).read_text('utf-8')
            if sha(text.encode()) != original_manifest[name] or re.search(r'(?i)<script|<foreignObject|\bon\w+\s*=|(?:href|src)\s*=\s*["\']https?:', text):
                raise ValueError('Unsafe or altered chart')
            charts[name] = text
    output.mkdir(parents=True)
    (output/'public').mkdir(); (output/'private').mkdir()
    files = {'replay.json':package,'public/summary.json':public,'private/source-manifest.json':private,
             'benchmark.json':benchmark}
    for name,value in files.items():
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    manifest = {name:sha((output/name).read_bytes()) for name in files}
    for name,text in charts.items():
        (output/name).write_bytes(text.encode('utf-8'))
        manifest[name] = sha(text.encode())
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return public
