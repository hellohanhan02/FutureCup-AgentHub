"""11-B preparation: synthetic credentials and Mock HTTP only; no provider network."""
from dataclasses import replace
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from futurecup.adapters.workspace_demo import template_plan
from futurecup.adapters.workspace_validation import WorkspaceValidator, document_errors
from futurecup.domain.llm import RealCallSafety
from test_workspace_11a import application, create, confirm, wait
from test_serial_collaboration_09a import app, configuration
from test_real_llm_integration import completion

GOAL='我想开发一个面向大学生的校园二手交易平台，支持二手物品发布、商品搜索、在线沟通、订单管理和信用评价。请完成需求分析、系统架构设计、数据库设计、技术选型和方案审查，形成完整系统设计方案。'
HEADINGS=('需求分析','系统架构设计','数据库设计','技术选型','方案审查')
MARKDOWN='\n\n'.join('## '+h+'\n'+('无网络协议测试文本：实体User、Item、Order通过外键关联，所有内容均为测试替身，不能声称真实模型推理。'*20) for h in HEADINGS)

def document_settings(*, prices=True):
    settings=configuration(prices=prices)
    profiles=json.loads(settings.provider_config_json)
    profiles[0]['timeout_seconds']=120
    profiles[0]['models'][0].update(max_context_bytes=131072,max_output_tokens=4096)
    if prices:
        # Synthetic arithmetic only; NOT a quote of current provider pricing.
        profiles[0]['models'][0].update(input_price_per_million=.1,output_price_per_million=1)
    safety=json.loads(settings.real_call_safety_json)
    safety.update(profile='workspace_document',max_output_tokens=4096,max_task_model_calls=2,max_workflow_model_calls=6,max_feedback_loops=1)
    return replace(settings,provider_config_json=json.dumps(profiles),real_call_safety_json=json.dumps(safety))

@pytest.mark.parametrize('repair',[False,True])
def test_real_protocol_document_flow_budget_history_and_upstream(monkeypatch,repair):
    seen=[];outputs={}
    plan=template_plan(GOAL,'architecture')
    plan['steps'][-1].update(depends_on=['s1','s2'],input_from=['s1','s2'])
    async def transport(request):
        body=json.loads(request.content);seen.append(body)
        incoming=json.loads(body['messages'][-1]['content'])
        if len(seen)==1:
            assert incoming['messages']==[GOAL]
            assert body['max_tokens']==2048
            value=plan
        else:
            original=incoming.get('original_input',incoming)
            upstream=original['upstream']
            if original['final_delivery']:
                assert upstream=={'s1':outputs['first'],'s2':outputs['second']}
                assert len(body['messages'][-1]['content'])>8192
                assert body['max_tokens']==4096
                if 'recovery' in incoming:
                    assert incoming['recovery']['previous_artifact']['content']['markdown']==MARKDOWN.replace('## 数据库设计','## 缺失章节')
                    assert incoming['recovery']['verification']['id']
                value={'title':'SIMULATED 校园设计','summary':'Mock HTTP','items':['SIMULATED'],'markdown':MARKDOWN.replace('## 数据库设计','## 缺失章节') if repair and len(seen)==4 else MARKDOWN}
            else:
                assert body['max_tokens']==2048
                if upstream:assert upstream['s1']==outputs['first']
                value={'title':'SIMULATED','summary':'Mock HTTP','items':['SIMULATED'],'markdown':MARKDOWN[:4500]}
                outputs['first' if not upstream else 'second']=value
        return httpx.Response(200,json=completion(json.dumps(value,ensure_ascii=False),model='SIMULATED-deepseek'))
    with TestClient(app(monkeypatch,transport,document_settings())) as c:
        task=create(c,request=GOAL,output_profile='design_document',mode='real',authorize_real=True)
        assert task['state']=='awaiting_confirmation',task
        assert len(seen)==1 and task['run_id'] is None
        route='/api/v1/workspace/tasks/'+task['id']
        assert c.post(route+'/confirm',json={'revision':1}).status_code==403
        assert c.post(route+'/confirm',json={'revision':1,'authorize_real':True}).status_code==202
        task=wait(c,task['id'],('succeeded','failed'))
        assert task['state']=='succeeded',task
        assert task['delivery']['markdown']==MARKDOWN
        assert len(seen)==4+int(repair)==task['usage']['model_calls']
        assert task['usage']['feedback_loops']==int(repair)
        assert task['estimated_cny_reserved']<=.5
        assert c.get(route).json()['delivery']==task['delivery']
        assert any(t['id']==task['id'] for t in c.get('/api/v1/workspace/tasks').json())
        assert 'max_output_tokens' not in json.dumps(c.get('/api/v1/workspace/tasks').json())
        if repair:
            node=task['report']['nodes'][-1]
            records=c.get('/api/v1/self-loops/'+node['id']+'/verifications').json()
            assert records[0]['status']=='FAILED' and records[0]['evidence'][0]['status']=='PASSED'
            assert 'missing_section:数据库设计' in json.dumps(records[0],ensure_ascii=False)

@pytest.mark.parametrize('settings,budget',[(configuration(),.5),(document_settings(prices=False),.5),(document_settings(),.00001)])
def test_incompatible_profile_unknown_prices_and_budget_refuse_before_http(monkeypatch,settings,budget):
    async def never(request):raise AssertionError('No HTTP permitted')
    with TestClient(app(monkeypatch,never,settings)) as c:
        r=c.post('/api/v1/workspace/tasks',json={'request':GOAL,'mode':'real','authorize_real':True,'output_profile':'design_document','budget_cny':budget})
        assert r.status_code==409,r.text

def test_document_structure_validation_does_not_accept_missing_sections():
    schema=WorkspaceValidator().output_schema('architecture','schema',document=True,final=True)
    validator=Draft202012Validator(schema)
    data={'title':'方案','summary':'测试','items':['测试'],'markdown':MARKDOWN}
    assert validator.is_valid(data)
    assert document_errors(MARKDOWN.replace('## 数据库设计','## 错误章节'))==['missing_section:数据库设计']
    assert not validator.is_valid({**data,'markdown':'只重复用户输入'})
    assert RealCallSafety().max_output_tokens==1024
    assert RealCallSafety(profile='workspace_document',max_output_tokens=4096).max_output_tokens==4096

def test_document_mock_mode_and_invalid_mode_remain_explicit():
    with TestClient(application()) as c:
        task=create(c,request='先需求；再设计；最后审查',output_profile='design_document')
        task=confirm(c,task)
        assert task['state']=='succeeded' and task['simulated'] is True
        assert '[MOCK' in task['delivery']['markdown']
        assert c.post('/api/v1/workspace/tasks',json={'request':GOAL,'planning':'template','output_profile':'design_document'}).status_code==422

def test_document_launcher_with_synthetic_secure_input_only():
    from test_start_deepseek_safe import run_harness,SYNTHETIC
    result=run_harness(SYNTHETIC,'PASS','document-mode')
    assert result.returncode==0,'Synthetic document launcher failed'
