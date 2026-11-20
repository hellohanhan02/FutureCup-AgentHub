"""Newly entered key only via stdin. No model inference on startup."""
import json,sys
from pathlib import Path
from demo_processes import ROOT,start,request

def main():
    value=json.loads(sys.stdin.readline());key=value.pop('key').strip()
    if not 8<=len(key)<=4096 or any(ord(c)<33 or ord(c)>126 for c in key):print('FAIL');raise SystemExit(1)
    # Import only delivery code; no original project or stored credentials.
    sys.path.insert(0,str(ROOT/'source/stable/backend/src'))
    from futurecup.domain.llm import ProviderProfile,ModelPolicy,RetryPolicy,RealCallSafety
    from futurecup.adapters.provider_config import validate_origin
    from dataclasses import asdict
    kind=value['provider'];host=value.get('allowed_host','')
    if kind=='openai_compatible' and not host:raise ValueError('A server-approved hostname is required')
    model=ModelPolicy(value['model'],max_context_bytes=131072,max_output_tokens=value['max_output_tokens'],response_formats=('text','prompt_json'),thinking_disabled=kind=='deepseek',input_price_per_million=value['input_price'],output_price_per_million=value['output_price'],currency='CNY')
    if value['input_price']<=0 or value['output_price']<=0:raise ValueError('Verify positive official CNY prices first')
    profile=ProviderProfile(kind,kind,value['base_url'],'JUDGE_LLM_KEY',(model,),allowed_hosts=(host,) if host else (),enabled=True,timeout_seconds=value['timeout'],retry=RetryPolicy(max_retries=0),revision='judge-1')
    validate_origin(profile)
    safety=RealCallSafety(max_output_tokens=value['max_output_tokens'],max_task_model_calls=2,max_workflow_model_calls=6,max_feedback_loops=1,max_concurrency=1,estimated_workflow_cny=value['budget_cny'],profile='workspace_document')
    env={'JUDGE_LLM_KEY':key,'FUTURECUP_ENABLE_REAL_LLM':'true','FUTURECUP_LLM_PROVIDERS_JSON':json.dumps([asdict(profile)]),'FUTURECUP_REAL_CALL_SAFETY_JSON':json.dumps(asdict(safety))}
    print('PASS',flush=True)
    record=start('real',value['backend_port'],value['frontend_port'],env)
    env.clear();key=None
    # Local deterministic admission calculation only; does not invoke a Provider.
    try:
        preview=request(record['backend_port'],'/api/v1/workspace/budget-preview',{'provider':kind,'model':value['model'],'output_profile':'design_document','budget_cny':value['budget_cny'],'max_calls':6})
        print('LOCAL BUDGET PREVIEW (not a bill): '+json.dumps(preview),flush=True)
    except Exception:print('Budget preview unavailable; do not submit before checking the workspace preview.',flush=True)
    print('Server started; zero model requests sent by this launcher. User confirmation is still required in the workspace.',flush=True)
if __name__=='__main__':
    try:main()
    except Exception:
        print('Real launch failed safely. Check secret-free endpoint/model/price settings; no credential contents are emitted.');raise SystemExit(1)