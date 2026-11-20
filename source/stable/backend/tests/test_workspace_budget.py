"""Configurable budgets with synthetic prices, Mock HTTP and no real credentials."""
from dataclasses import replace
import json
import subprocess
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.domain.llm import ModelPolicy, RealCallSafety
from futurecup.domain.workspace_budget import admission_estimate
from futurecup.adapters.workspace_demo import template_plan
from test_workspace_11b import document_settings, GOAL, MARKDOWN
from test_serial_collaboration_09a import app
from test_workspace_11a import create, wait
from test_real_llm_integration import completion

ROOT=Path(__file__).resolve().parents[2]

def settings(server=2):
    value=document_settings();profiles=json.loads(value.provider_config_json)
    # Synthetic rates reproduce arithmetic, NOT verified provider prices.
    profiles[0]['models'][0].update(input_price_per_million=2,output_price_per_million=8)
    safety=json.loads(value.real_call_safety_json);safety['estimated_workflow_cny']=server
    return replace(value,provider_config_json=json.dumps(profiles),real_call_safety_json=json.dumps(safety))

@pytest.mark.parametrize('server,budget,accepted',[(.5,.5,False),(2,.5,False),(2,1,False),(2,2,True),(5,5,True),(2,1.8,True),(2,5,False)])
def test_budget_preview_matches_real_admission_without_http(monkeypatch,server,budget,accepted):
    requests=[]
    async def transport(r):
        requests.append(True)
        return httpx.Response(200,json=completion(json.dumps(template_plan(GOAL,'architecture'))))
    with TestClient(app(monkeypatch,transport,settings(server))) as c:
        quote=c.post('/api/v1/workspace/budget-preview',json={'output_profile':'design_document','budget_cny':budget}).json()
        assert quote['estimated_admission_upper_cny']==pytest.approx(1.769472)
        assert quote['admitted'] is accepted and quote['real_requests_sent']==0
        assert not requests
        response=c.post('/api/v1/workspace/tasks',json={'request':GOAL,'mode':'real','output_profile':'design_document','budget_cny':budget,'authorize_real':True})
        assert response.status_code==(202 if accepted else 409),response.text
        if not accepted:assert not requests

def test_shared_two_yuan_budget_is_not_double_reserved_at_confirmation_and_repair(monkeypatch):
    seen=[]
    plan=template_plan(GOAL,'architecture');plan['steps'][-1].update(input_from=['s1','s2'],depends_on=['s1','s2'])
    async def transport(r):
        body=json.loads(r.content);seen.append(body)
        output=plan if len(seen)==1 else {'title':'SIMULATED','summary':'Mock HTTP','items':['SIMULATED'],'markdown':MARKDOWN}
        if len(seen)==4:output={**output,'markdown':MARKDOWN.replace('## 数据库设计','## Missing')}
        return httpx.Response(200,json=completion(json.dumps(output,ensure_ascii=False)))
    with TestClient(app(monkeypatch,transport,settings())) as c:
        task=create(c,request=GOAL,mode='real',output_profile='design_document',budget_cny=2,authorize_real=True)
        assert task['budget_preview']['estimated_admission_upper_cny']==pytest.approx(1.769472)
        path='/api/v1/workspace/tasks/'+task['id']
        assert c.post(path+'/confirm',json={'revision':1}).status_code==403
        assert len(seen)==1
        assert c.post(path+'/confirm',json={'revision':1,'authorize_real':True}).status_code==202
        task=wait(c,task['id'],('succeeded','failed'))
        assert task['state']=='succeeded',task
        assert len(seen)==task['usage']['model_calls']==5
        assert task['usage']['feedback_loops']==1 and task['budget_cny']==2
        expected=sum((131072*2+body['max_tokens']*8)/1e6 for body in seen)
        assert task['estimated_cny_reserved']==pytest.approx(expected)
        assert expected<=2

@pytest.mark.parametrize('budget',[-1,0,1000.01,True,'2'])
def test_invalid_budget_inputs_fail_before_any_http(monkeypatch,budget):
    async def never(r):raise AssertionError('Network forbidden')
    with TestClient(app(monkeypatch,never,settings())) as c:
        assert c.post('/api/v1/workspace/tasks',json={'request':GOAL,'budget_cny':budget}).status_code==422

def test_default_legacy_safety_and_custom_workspace_limits():
    assert RealCallSafety().estimated_workflow_cny==.5
    with pytest.raises(ValueError):RealCallSafety(estimated_workflow_cny=2)
    for budget in (.5,1,2,5,12.34):
        assert RealCallSafety(profile='workspace_document',estimated_workflow_cny=budget).estimated_workflow_cny==budget
    for budget in (float('inf'),float('nan'),0,1000.01):
        with pytest.raises(ValueError):RealCallSafety(profile='workspace_document',estimated_workflow_cny=budget)

def test_preflight_default_denies_and_explicit_two_yuan_accepts():
    command=[sys.executable,str(ROOT/'scripts/preflight_workspace_11b.py'),'--input-price','2','--output-price','8']
    for extra,code,budget in (([],1,.5),(['--budget-cny','2'],0,2)):
        result=subprocess.run(command+extra,cwd=ROOT,capture_output=True,text=True,timeout=10)
        assert result.returncode==code,result.stderr
        value=json.loads(result.stdout)
        assert value['estimated_admission_upper_cny']==pytest.approx(1.769472)
        assert value['threshold_cny']==budget and value['real_requests_sent']==0
    policy=ModelPolicy('unit',max_context_bytes=131072,input_price_per_million=2,output_price_per_million=8,currency='CNY')
    assert admission_estimate(policy,6,4096)==pytest.approx(1.769472)

def test_launcher_explicit_budget_preserves_limits_without_launching_real_backend():
    from test_start_deepseek_safe import run_harness,SYNTHETIC
    result=run_harness(SYNTHETIC,'PASS','document-budget')
    assert result.returncode==0,'Synthetic launcher budget configuration failed'
