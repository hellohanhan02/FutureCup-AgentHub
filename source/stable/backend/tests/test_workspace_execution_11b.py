"""Real evidence audit and realistic Chinese Mock HTTP E2E; NO real inference."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from test_workspace_budget import settings
from test_serial_collaboration_09a import app
from test_workspace_11a import create,wait
from test_real_llm_integration import completion,managed,request,KEY,FAKE
from futurecup.domain.errors import RuntimeFault

FIXTURES=Path(__file__).parent/'fixtures'
HISTORY=json.loads((FIXTURES/'workspace_11b_execution_failure.json').read_text('utf-8'))
OUTPUTS=json.loads((FIXTURES/'workspace_11b_campus_protocol_outputs.json').read_text('utf-8'))

def test_actual_failure_has_no_output_finish_or_usage_to_reconstruct():
    assert HISTORY['source_run_id']=='3babc4efeba645b2933de30fe4cd0803'
    node=HISTORY['failed_node'];attempt=HISTORY['audit']['attempt']
    assert node['model_calls'][0]['error_code']=='provider_incomplete'
    assert node['output'] is None and attempt['artifact']['content'] is None
    assert not node['model_calls'][0]['usage_reported']
    assert 'finish_reason' not in node['model_calls'][0]
    assert HISTORY['audit']['verification']['origin']=='execution'
    assert HISTORY['audit']['verification']['checks_used']==0
    assert HISTORY['audit']['feedback']['repair_action']=='abort'
    assert HISTORY['resource_usage']['feedback_loops']==0

@pytest.mark.parametrize('scenario',['stop','length','length_twice','schema','invalid_json','content_filter','unknown'])
def test_three_agents_actual_plan_and_substantive_documents_e2e(monkeypatch,scenario):
    seen=[];attempts={};delivered={};usage=[];partial=None
    roles={s['role']:s['id'] for s in HISTORY['plan']['steps']}
    async def transport(r):
        nonlocal partial
        body=json.loads(r.content);seen.append(body)
        assert body['thinking']=={'type':'disabled'} and 'tools' not in body
        incoming=json.loads(body['messages'][-1]['content']);original=incoming.get('original_input',incoming)
        if 'role' not in original:
            stage='planner';content=json.dumps(HISTORY['plan'],ensure_ascii=False)
        else:
            stage=roles[original['role']];attempts[stage]=attempts.get(stage,0)+1
            upstream=original['upstream']
            assert set(upstream)==set(HISTORY['plan']['steps'][int(stage[1])-1]['input_from'])
            for key,value in upstream.items():assert value==delivered[key]
            assert original['responsibilities']==HISTORY['plan']['steps'][int(stage[1])-1]['responsibilities']
            prompt=body['messages'][0]['content']
            if stage!='s3':assert 'INTERMEDIATE CONTRIBUTION ONLY' in prompt and body['max_tokens']==2048
            else:assert 'FINAL DELIVERY' in prompt and body['max_tokens']==4096
            value=deepcopy(OUTPUTS[stage]);content=json.dumps(value,ensure_ascii=False)
            if stage=='s1' and attempts[stage]==1:
                if scenario=='schema':value['items']=[];content=json.dumps(value,ensure_ascii=False)
                elif scenario=='invalid_json':content='```json\n'+content+'\n```'
                elif scenario in ('length','length_twice','content_filter','unknown'):
                    partial=content[:len(content)//2];content=partial
            if stage=='s1' and attempts[stage]==2:
                recovery=incoming['recovery']
                assert recovery['feedback']['action']=='prompt_retry'
                assert recovery['previous_artifact']['content'] is not None
                if scenario in ('length','length_twice'):assert recovery['previous_artifact']['content']==partial and recovery['previous_artifact']['format_error']
                if scenario=='length_twice':content=partial
            delivered[stage]=deepcopy(OUTPUTS[stage])
        response=completion(content,model='SIMULATED-deepseek-flash')
        response['id']='11111111-1111-4111-8111-111111111111'
        tokens=2048 if stage=='s1' and scenario in ('length','length_twice') else 700
        response['usage']={'prompt_tokens':900,'completion_tokens':tokens,'total_tokens':900+tokens};usage.append(900+tokens)
        if stage=='s1' and ((attempts[stage]==1 and scenario in ('length','content_filter','unknown')) or scenario=='length_twice'):
            response['choices'][0]['finish_reason']=('length' if scenario=='length_twice' else scenario) if scenario!='unknown' else 'vendor-new-opaque-reason'
        return httpx.Response(200,json=response)
    with TestClient(app(monkeypatch,transport,settings())) as c:
        task=create(c,request=HISTORY['input']['messages'][0],mode='real',output_profile='design_document',budget_cny=2,authorize_real=True)
        assert task['state']=='awaiting_confirmation' and len(seen)==1 and task['usage']['feedback_loops']==0
        path='/api/v1/workspace/tasks/'+task['id']
        assert c.post(path+'/confirm',json={'revision':1,'authorize_real':True}).status_code==202
        task=wait(c,task['id'],('failed','succeeded'))
        assert task['usage']['reported_tokens']==sum(usage) and task['usage']['unknown_usage_calls']==0
        assert task['estimated_cny_reserved']<=2 and len(seen)<=6
        if scenario in ('content_filter','unknown','length_twice'):
            assert task['state']=='failed' and task['error_phase']=='execution'
            assert task['report']['nodes'][1]['state']=='skipped' and task.get('delivery') is None
            assert task['usage']['feedback_loops']==int(scenario=='length_twice') and len(seen)==(3 if scenario=='length_twice' else 2)
            assert '规划未通过' not in json.dumps(task['error_details'],ensure_ascii=False)
            assert task['report']['nodes'][0]['model_calls'][0]['finish_reason']==('length' if scenario=='length_twice' else scenario)
        else:
            assert task['state']=='succeeded' and task['report']['verified']
            assert task['delivery']==OUTPUTS['s3'] and 'price_snapshot' in task['delivery']['markdown']
            nodes=task['report']['nodes']
            assert nodes[1]['input']['upstream']['s1']==nodes[0]['output']==OUTPUTS['s1']
            assert nodes[2]['input']['upstream']=={'s1':OUTPUTS['s1'],'s2':OUTPUTS['s2']}
            assert all(n['verified'] for n in nodes)
            assert task['usage']['feedback_loops']==int(scenario!='stop')
            assert len(seen)==4+int(scenario!='stop')
        if scenario=='length':
            node=task['report']['nodes'][0]
            records=c.get('/api/v1/self-loops/'+node['id']+'/attempts').json()
            assert records[0]['artifact']['content']==partial and records[0]['artifact']['format_error']
            assert records[0]['model_calls'][0]['finish_reason']=='length'
            assert records[0]['model_calls'][0]['usage_reported']
            assert records[0]['error_code'] is None  # classified output failure, Runtime failure remains in model call audit
            assert records[0]['model_calls'][0]['error_code']=='provider_incomplete'

@pytest.mark.parametrize('finish',['length','content_filter','unrecognized'])
def test_incomplete_adapter_still_rejects_and_safely_retains_diagnostic(monkeypatch,finish):
    import asyncio
    monkeypatch.setenv(KEY,FAKE)
    body=completion('{"title":"SIMULATED partial',model='SIMULATED')
    body['choices'][0]['finish_reason']=finish
    body['id']='11111111-1111-4111-8111-111111111111'
    provider=managed(lambda r:httpx.Response(200,json=body))
    with pytest.raises(RuntimeFault) as failure:asyncio.run(provider.infer(request()))
    error=failure.value
    assert error.code=='provider_incomplete' and 'partial' not in str(error)
    assert error.provider_completion.finish_reason==(finish if finish!='unrecognized' else 'unknown')
    assert error.provider_completion.usage_reported and error.provider_completion.input_tokens==3
    assert error.provider_completion.content==body['choices'][0]['message']['content']

def test_incomplete_content_containing_synthetic_credential_is_never_retained(monkeypatch):
    import asyncio
    monkeypatch.setenv(KEY,FAKE)
    body=completion(FAKE);body['choices'][0]['finish_reason']='length'
    provider=managed(lambda r:httpx.Response(200,json=body))
    with pytest.raises(RuntimeFault) as failure:asyncio.run(provider.infer(request()))
    assert failure.value.provider_completion is None and FAKE not in str(failure.value)
