"""Scoped engineering read models; never settle budgets or bypass recovery checks."""
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from fastapi import APIRouter, Query
from fastapi.encoders import jsonable_encoder

from futurecup.adapters.competition_evidence import sanitize
from futurecup.domain.errors import RuntimeFault

BLOCKED = re.compile(r'(?i)(authorization|cookie|password|secret|credential|api.?key|system.?prompt|headers|raw.?body|base.?url|database.?path)')


def safe_view(value):
    counts = Counter()
    def strip(item):
        if isinstance(item,dict):return {str(k):strip(v) for k,v in item.items() if not BLOCKED.search(str(k))}
        if isinstance(item,(list,tuple)):return [strip(v) for v in item]
        return item
    clean = sanitize(strip(jsonable_encoder(value)),counts,whitelist_keys=False)
    return clean, dict(counts)


async def recovery_options(engine, report):
    reasons=[]
    if report.run.state.value!='failed':reasons.append('recovery_source_not_failed')
    if report.simulated is not True:reasons.append('recovery_real_budget_unprovable')
    if report.run.parent_run_id:reasons.append('recovery_child_requires_root')
    if datetime.now(timezone.utc)>=report.run.policy.deadline:reasons.append('recovery_deadline_exhausted')
    if report.recovery_audit:reasons.append('recovery_generation_limit')
    claim=await engine.repository.recovery_claim(report.run.id)
    if claim:reasons.append('recovery_source_claimed')
    if engine.runtime.durability and not report.budget_account_id:reasons.append('recovery_ledger_missing')
    if engine.runtime.durability and report.budget_account_id and not claim and not report.recovery_audit:
        try:
            account=engine.runtime.durability.ledger.account(report.budget_account_id)
            if account['occupied_calls']!=report.resource_usage.get('calls',0) or account['occupied_units']!=report.resource_usage.get('resource_units',0):reasons.append('ledger_budget_snapshot_mismatch')
        except RuntimeFault as fault:reasons.append(fault.code)
    return {'requestable':not reasons,'reasons':reasons,'existing_run_id':claim['run_id'] if claim else None,
        'expected_revision':report.run.revision,'check_scope':'preflight_only; final permission/budget/tool checks remain in LocalRecoveryService',
        'authorize_effects_supported':False,'real_recovery_enabled':False}


async def observe(engine, run_id: str, expected_revision: int | None = None):
    report=await engine.get(run_id)
    if expected_revision is not None and expected_revision!=report.run.revision:
        raise RuntimeFault('report_revision_changed','Run changed; refresh before exporting',409)
    definition=await engine.repository.get_definition(report.run.definition_ref)
    audits={}
    if engine.selfloop:
        for node in report.nodes:
            if node.attempt_ids:
                store=engine.selfloop.repository
                audits[node.node_id]={'attempts':await store.attempts(node.id),'verifications':await store.results(node.id),'feedback':await store.feedbacks(node.id)}
    decisions=list(await engine.scheduler.repository.decisions(None,run_id,1000)) if engine.scheduler else []
    ledger={'available':False,'reason':'ledger_not_enabled_or_not_recorded'}
    durability=engine.runtime.durability
    if durability and report.budget_account_id:
        reader=getattr(durability.ledger,'public_view',None)
        if reader:
            try:ledger=reader(report.budget_account_id)
            except RuntimeFault as e:ledger={'available':False,'reason':e.code}
    options=await recovery_options(engine,report)
    related=[];seen={run_id}
    queue=[i for n in report.nodes for i in n.child_run_ids]
    if report.recovery_audit.get('source_run'):queue.append(report.recovery_audit['source_run'])
    while queue and len(related)<32:
        identifier=queue.pop(0)
        if identifier in seen:continue
        seen.add(identifier);other=await engine.get(identifier);other_definition=await engine.repository.get_definition(other.run.definition_ref)
        other_audits={}
        if engine.selfloop:
            for node in other.nodes:
                if node.attempt_ids:
                    store=engine.selfloop.repository
                    other_audits[node.node_id]={'attempts':await store.attempts(node.id),'verifications':await store.results(node.id),'feedback':await store.feedbacks(node.id)}
        other_decisions=list(await engine.scheduler.repository.decisions(None,identifier,1000)) if engine.scheduler else []
        if (await engine.get(identifier)).run.revision!=other.run.revision:
            raise RuntimeFault('report_revision_changed','Related evidence changed; refresh',409)
        related.append({'run_id':identifier,'report':asdict(other),'audits':other_audits,'decisions':other_decisions,
            'graph':{'nodes':[{'id':n.id,'kind':n.kind.value} for n in other_definition.nodes],'edges':[asdict(e) for e in other_definition.edges]}})
        queue.extend(i for n in other.nodes for i in n.child_run_ids)
    current=await engine.get(run_id)
    if current.run.revision!=report.run.revision:
        raise RuntimeFault('report_revision_changed','Run changed during observation; refresh',409)
    graph={'nodes':[{'id':n.id,'kind':n.kind.value} for n in definition.nodes],
           'edges':[asdict(e) for e in definition.edges]}
    links={'source_run_id':report.recovery_audit.get('source_run'), 'recovery_run_id':options['existing_run_id']}
    value={'schema_version':'platform-14d-v1','run_id':run_id,'revision':report.run.revision,
        'report':asdict(report),'graph':graph,'node_configs':{n.id:{k:v for k,v in n.config.items() if k in ('verification','recovery','input','scheduling_features','required_skills','candidate_agents','limits')} for n in definition.nodes},
        'audits':audits,'decisions':decisions,'ledger':ledger,'links':links,'recovery':options,'related_runs':related,'related_runs_truncated':bool(queue),
        'captured_at':datetime.now(timezone.utc),'privacy':'local redacted view; not supplier billing or proof of all business correctness'}
    clean,redactions=safe_view(value)
    return {**clean,'redactions':redactions}


def build_platform_router(engine):
    router=APIRouter(prefix='/api/v1',tags=['platform-observability'])
    @router.get('/workflow-runs/{run_id}/observability')
    async def view(run_id: str):
        return await observe(engine,run_id)
    @router.get('/workflow-runs/{run_id}/report-export')
    async def export(run_id: str, expected_revision: int = Query(ge=0)):
        value=await observe(engine,run_id,expected_revision)
        digest=sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'run_id':run_id,'revision':value['revision'],'evidence':value,'sha256':digest,
                'notice':'Redacted local evidence snapshot, not an invoice or signed attestation.'}
    return router
