"""Historical business-plan fixture replay via Mock HTTP; never a live model request."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from futurecup.adapters.workspace_validation import WorkspaceValidator, PLAN_SCHEMA
from futurecup.domain.errors import RuntimeFault
from test_workspace_budget import settings
from test_serial_collaboration_09a import app
from test_workspace_11a import create,wait
from test_workspace_11b import GOAL,MARKDOWN
from test_real_llm_integration import completion

FIXTURE=json.loads((Path(__file__).parent/'fixtures/workspace_11b_failed_plan.json').read_text('utf-8'))
BAD=FIXTURE['plan']

def test_actual_historical_plan_still_fails_unchanged_allowlist_with_precise_fields():
    validator=WorkspaceValidator()
    with pytest.raises(RuntimeFault) as error:validator.validate_plan(BAD,'architecture',{})
    assert error.value.code=='workspace_validator'
    issues=validator.plan_diagnostics(BAD,'architecture',error.value.code)
    assert [i['path'] for i in issues]==['steps.1.verification','steps.2.verification']
    assert all(i['rule']=='scenario_validator_allowlist' for i in issues)
    assert PLAN_SCHEMA['properties']['steps']['items']['properties']['verification']['enum']==['schema','source_quotes','precedence']

@pytest.mark.parametrize('repair_success,execution_failure',[(True,False),(False,False),(True,True)])
def test_original_selfloop_repairs_historical_plan_and_shares_single_repair(monkeypatch,repair_success,execution_failure):
    seen=[];planner_inputs=[];workers=[]
    good=deepcopy(BAD)
    for step in good['steps']:step['verification']='schema'
    async def transport(request):
        body=json.loads(request.content);seen.append(body)
        incoming=json.loads(body['messages'][-1]['content']);original=incoming.get('original_input',incoming)
        if 'role' not in original:
            planner_inputs.append(incoming)
            assert '"enum": ["schema"]' in body['messages'][0]['content']
            assert 'ALL nodes MUST use verification="schema"' in body['messages'][0]['content']
            value=BAD if len(planner_inputs)==1 or not repair_success else good
        else:
            workers.append(incoming)
            value={'title':'SIMULATED','summary':'Mock HTTP only','items':['SIMULATED'],'markdown':MARKDOWN}
            if execution_failure:value={'invalid':'SIMULATED failure after planning repair'}
        return httpx.Response(200,json=completion(json.dumps(value,ensure_ascii=False)))
    with TestClient(app(monkeypatch,transport,settings())) as c:
        task=create(c,request=GOAL,mode='real',output_profile='design_document',budget_cny=2,authorize_real=True)
        assert len(planner_inputs)==2 and not workers
        assert task['usage']['model_calls']==2 and task['usage']['feedback_loops']==1
        assert len(task['planning_attempts'])==2
        assert [v['status'] for v in task['planning_verifications']]==['FAILED','PASSED' if repair_success else 'FAILED']
        assert task['planning_attempts'][0]['artifact']['content']==BAD
        rec=planner_inputs[1]['recovery']
        assert rec['previous_artifact']['content']==BAD
        assert rec['verification']['id']==task['planning_verifications'][0]['record']['id']
        assert rec['feedback']['id']==task['planning_feedback'][0]['record']['id']
        assert 'steps.1.verification' in json.dumps(rec)
        if not repair_success:
            assert task['state']=='failed' and task['error_code']=='workspace_validator'
            assert len(task['error_details'])==2 and task['run_id'] is None
            return
        assert task['state']=='awaiting_confirmation' and task['run_id'] is None
        path='/api/v1/workspace/tasks/'+task['id']
        assert c.post(path+'/confirm',json={'revision':1}).status_code==403
        assert c.post(path+'/confirm',json={'revision':1,'authorize_real':True}).status_code==202
        task=wait(c,task['id'],('failed','succeeded'))
        assert task['state']==('failed' if execution_failure else 'succeeded')
        assert task['usage']['feedback_loops']==1
        assert len(workers)==(1 if execution_failure else 3)
        assert len(seen)<=6 and task['estimated_cny_reserved']<=2

def test_no_planning_repair_when_calls_reserved_for_three_workers(monkeypatch):
    requests=[]
    async def transport(r):
        requests.append(True)
        return httpx.Response(200,json=completion(json.dumps(BAD)))
    with TestClient(app(monkeypatch,transport,settings())) as c:
        task=create(c,request=GOAL,mode='real',output_profile='design_document',budget_cny=2,authorize_real=True,max_calls=4)
        assert task['state']=='failed' and task['error_code']=='workspace_validator'
        assert len(requests)==1 and task['usage']['feedback_loops']==0

def test_legacy_failure_explanation_is_read_only(monkeypatch,tmp_path):
    from test_workspace_11a import application
    import sqlite3
    path=tmp_path/'history.sqlite3'
    with TestClient(application(path=path)) as c:
        # Synthetic local historical record, not the live evidence database.
        import asyncio
        record={'id':'ws-history','created_at':'2026-11-14T11:15:19+00:00','state':'failed','scenario':'architecture','mode':'real',
            'run_id':None,'max_calls':6,'budget_cny':2,'error_code':'workspace_validator','usage':{},
            'planning_executions':[{'output':json.dumps(BAD)}]}
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_tasks(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
            original=json.dumps(record);db.execute('INSERT INTO workspace_tasks VALUES(?,?)',(record['id'],original))
        result=c.get('/api/v1/workspace/tasks/ws-history').json()
        assert result['diagnostic_source']=='read_only_reanalysis' and len(result['error_details'])==2
        with sqlite3.connect(path) as db:assert db.execute('SELECT body FROM workspace_tasks').fetchone()[0]==original
