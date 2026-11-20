"""Evidence-based recovery policy. All outward values are controlled codes/references."""
from dataclasses import asdict
from futurecup.domain.verification import VerificationResult


def diagnose(result: VerificationResult | dict) -> dict:
    result=asdict(result) if not isinstance(result,dict) else result
    reasons=set(result['record']['reason_codes'])
    states={t.get('status') for e in result['evidence'] if isinstance(e.get('details'),dict)
            for t in e.get('details').get('coverage',[]) if isinstance(t,dict)}
    http={e['details'].get('http_error_category') for e in result['evidence']
          if e.get('kind')=='execution' and isinstance(e.get('details'),dict)}
    if 'insufficient_balance' in http: category='BUDGET_EXHAUSTED'
    elif 'authentication_failed' in http: category='PERMISSION_DENIED'
    elif http & {'rate_limited','server_error'}: category='SERVICE_UNAVAILABLE'
    elif 'model_unavailable' in http: category='RESOURCE_UNAVAILABLE'
    elif any('budget' in c or c in ('replan_limit','recovery_limit') for c in reasons): category='BUDGET_EXHAUSTED'
    elif reasons & {'cancelled','scheduling_cancelled'}:category='CANCELLED'
    elif reasons & {'tool_unauthorized','tool_permission_denied','sensitive_data_rejected','provider_address_denied','provider_auth_error','provider_credentials_invalid','provider_credentials_missing'}:category='PERMISSION_DENIED'
    elif result['origin']=='verifier':category='VERIFIER_ERROR'
    elif 'POSSIBLE_CONFLICT' in states or reasons & {'knowledge_rule_conflict','knowledge_numeric_unit_conflict'}:category='RULE_CONFLICT'
    elif states & {'SOURCE_UNAVAILABLE_UNMARKED','INSUFFICIENT_EVIDENCE','COVERAGE_RESOURCE_LIMIT'} or reasons & {'knowledge_no_matches','knowledge_unavailable','coverage_timeout','coverage_retrieval_error','knowledge_coverage_insufficient'}:category='KNOWLEDGE_MISSING'
    elif reasons & {'knowledge_rule_not_reflected','knowledge_semantic_uncertain'}:category='BUSINESS_CONSTRAINT'
    elif states & {'SOURCE_NOT_CITED','SOURCE_NOT_REFLECTED'} or 'knowledge_citation_invalid' in reasons:category='CITATION_MISSING'
    elif reasons & {'output_format_error','workflow_node_output_invalid','provider_incomplete'}:category='OUTPUT_FORMAT_ERROR'
    elif any(e['kind']=='json_schema' and e['status']=='FAILED' for e in result['evidence']):category='SCHEMA_FAILED'
    elif any(c in ('model_timeout','tool_timeout','selfloop_timeout','workflow_node_timeout') or c.endswith('_timeout') for c in reasons):category='TIMEOUT'
    elif any(c.startswith('tool_') for c in reasons):category='TOOL_EXECUTION_ERROR'
    elif reasons & {'provider_connection_failed','provider_unavailable','provider_rate_limited','provider_server_error','agent_unavailable','scheduling_no_candidate'}:category='SERVICE_UNAVAILABLE'
    elif result['origin']=='artifact':category='BUSINESS_CONSTRAINT'
    else:category='AGENT_RUNTIME_ERROR'
    actions={
        'OUTPUT_FORMAT_ERROR':('prompt_retry','reassign','fallback','human','abort'),
        'SCHEMA_FAILED':('prompt_retry','reassign','fallback','human','abort'),
        'BUSINESS_CONSTRAINT':('prompt_retry','reassign','fallback','human','abort'),
        'CITATION_MISSING':('prompt_retry','reassign','human','abort'),
        'RULE_CONFLICT':('prompt_retry','reassign','human','abort'),
        'TOOL_EXECUTION_ERROR':('tool_switch','prompt_retry','human','abort'),
        'TIMEOUT':('prompt_retry','fallback','human','abort'),
        'SERVICE_UNAVAILABLE':('prompt_retry','fallback','human','abort'),
        'AGENT_RUNTIME_ERROR':('human','abort'),
        'VERIFIER_ERROR':('human','abort'),'KNOWLEDGE_MISSING':('human','abort'),
    }.get(category,('abort',))
    return {'category':category,'origin':result['origin'],'repairable':any(a not in ('human','abort') for a in actions),
        'allowed_actions':actions,'evidence_refs':[result['record']['id']],'artifact_digest':result['artifact_digest'],
        'evidence_locations':[{'kind':e['kind'],'path':e.get('path'),'rule':e.get('rule'),'schema_path':e.get('schema_path')}
            for e in result['evidence'] if e['status']!='PASSED'],'reason':'evidence_policy_v1'}


def stop_diagnosis(code: str, evidence_refs: list[str]) -> dict | None:
    if 'budget' in code or code in {'recovery_limit','replan_limit'}:
        category,reason='BUDGET_EXHAUSTED','shared_or_repair_limit_exhausted'
    elif code=='cancelled':category,reason='CANCELLED','user_cancelled'
    elif code in {'tool_unauthorized','tool_permission_denied','sensitive_data_rejected'}:
        category,reason='PERMISSION_DENIED','permission_refused'
    elif code in {'repair_human_timeout','repair_human_required'}:
        category,reason='RESOURCE_UNAVAILABLE','operator_intervention_required'
    elif code.endswith('_timeout'):category,reason='TIMEOUT','execution_deadline_exhausted'
    else:return None
    return {'category':category,'origin':'control','repairable':False,'allowed_actions':['abort'],
            'evidence_refs':evidence_refs,'reason':reason}
