"""Actual offline system acceptance. No real Provider or historical database."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from futurecup.api.platform_demo import create_platform_app
from futurecup.api.platform import safe_view
from futurecup.domain.ledger import CallContext


def finished(client, run_id):
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        value=client.get('/api/v1/workflow-runs/'+run_id)
        assert value.status_code==200,value.text
        if value.json()['run']['state'] in ('failed','succeeded','cancelled'):return value.json()
        time.sleep(.01)
    pytest.fail('Actual Mock workflow did not finish within bounded wait')


def begin(client,key='test14d'):
    response=client.post('/api/v1/platform/mock-demo',json={'request_id':key,'authorize_mock':True})
    assert response.status_code==202,response.text
    return finished(client,response.json()['run']['id'])


def recover(client, source, key='repair14d'):
    response=client.post('/api/v1/workflow-runs/'+source['run']['id']+'/recover',json={
        'request_id':key,'expected_revision':source['run']['revision'],'authorize_recovery':True})
    assert response.status_code==202,response.text
    return finished(client,response.json()['run']['id'])


def test_actual_rag_adaptive_failure_precise_recovery_ledger_and_export(tmp_path):
    app=create_platform_app(tmp_path/'system')
    with TestClient(app) as client:
        source=begin(client)
        assert source['run']['state']=='failed' and source['simulated'] is True and not source['verified']
        view=client.get('/api/v1/workflow-runs/'+source['run']['id']+'/observability').json()
        assert view['run_id']==source['run']['id'] and view['recovery']['requestable']
        assert view['graph']['edges']==[{'source':'analyse','target':'review','branch':None}]
        first=view['audits']['review']['verifications'][0]
        assert first['record']['verdict']=='fail'
        assert any(c['status']=='SOURCE_NOT_REFLECTED' for e in first['evidence'] for c in (e.get('details') or {}).get('coverage',[]))
        assert all(d['strategy']=='adaptive_caas' for d in view['decisions'])
        assert all(d['requirements'] for d in view['decisions'])
        assert any(c.get('adaptation',{}).get('weights') for d in view['decisions'] for c in d['candidates'])
        assert view['ledger']['occupied_calls']==source['resource_usage']['calls']
        assert view['ledger']['tools'][0]['state']=='SUCCEEDED'
        final=recover(client,source)
        assert final['verified'] and final['run']['id']!=source['run']['id']
        assert final['nodes'][0]['reused_from']['run_id']==source['run']['id']
        assert not final['nodes'][0]['model_calls']
        again=client.get('/api/v1/workflow-runs/'+source['run']['id']).json()
        assert again==source
        observed=client.get('/api/v1/workflow-runs/'+final['run']['id']+'/observability').json()
        assert observed['links']['source_run_id']==source['run']['id']
        assert observed['ledger']['occupied_calls']==final['resource_usage']['calls']
        prior=observed['audits']['review']['attempts'][0]['input_value']['recovery']
        assert prior['previous_artifact'] and prior['verification']['evidence']
        response=client.get('/api/v1/workflow-runs/'+final['run']['id']+'/report-export',params={'expected_revision':final['run']['revision']})
        assert response.status_code==200,response.text
        assert response.json()['run_id']==final['run']['id']
        assert response.json()['evidence']['related_runs'][0]['run_id']==source['run']['id']
        assert response.json()['evidence']['related_runs'][0]['audits']['analyse']['attempts'][0]['tool_calls']
        assert 'secret' not in response.json()['evidence']


def test_duplicate_demo_and_recovery_requests_do_not_execute_twice(tmp_path):
    app=create_platform_app(tmp_path/'system')
    with TestClient(app) as c:
        source=begin(c)
        before=app.state.runtime.durability.ledger.account(source['budget_account_id'])
        assert begin(c)['run']['id']==source['run']['id']
        assert app.state.runtime.durability.ledger.account(source['budget_account_id'])==before
        final=recover(c,source)
        assert recover(c,source)['run']['id']==final['run']['id']
        assert c.post('/api/v1/platform/mock-demo',json={'request_id':'denied'}).status_code==409
        assert c.post('/api/v1/workflow-runs/'+source['run']['id']+'/recover',json={'request_id':'denied','expected_revision':source['run']['revision']}).status_code==409


def test_observation_survives_restart_and_preserves_tool_calls_and_links(tmp_path):
    folder=tmp_path/'system'
    with TestClient(create_platform_app(folder)) as c:
        source=begin(c);final=recover(c,source)
    with TestClient(create_platform_app(folder)) as c:
        value=c.get('/api/v1/workflow-runs/'+source['run']['id']+'/observability').json()
        assert value['links']['recovery_run_id']==final['run']['id']
        assert value['audits']['analyse']['attempts'][0]['tool_calls'][0]['output']=={'result':5}
        assert value['ledger']['available']
        assert c.get('/api/v1/workflow-runs/'+final['run']['id']).json()['verified']


def test_unknown_is_occupied_and_export_is_revision_bound(tmp_path):
    app=create_platform_app(tmp_path/'system')
    with TestClient(app) as c:
        source=begin(c);ledger=app.state.runtime.durability.ledger
        context=CallContext(source['budget_account_id'],'simulated-unknown',source['run']['id'],'test-node','test-execution','model',ledger.digest('SIMULATED uncertainty'),10)
        ledger.reserve(context);ledger.dispatch(context.reservation_id);ledger.uncertain(context.reservation_id)
        view=c.get('/api/v1/workflow-runs/'+source['run']['id']+'/observability').json()['ledger']
        assert view['unknown_calls']==1 and view['unknown_units']==10
        assert view['remaining_calls']==view['max_calls']-view['occupied_calls']
        assert any(f['code']=='budget_settlement_unknown' for f in view['findings'])
        assert 'result_json' not in json.dumps(view) and 'operation_key' not in json.dumps(view)
        response=c.get('/api/v1/workflow-runs/'+source['run']['id']+'/report-export',params={'expected_revision':0})
        assert response.status_code==409
        assert c.get('/api/v1/workflow-runs/no-such-id/observability').status_code==404


def test_public_report_masks_credentials_prompts_and_sensitive_text():
    value,count=safe_view({'Authorization':'private fake material','system_prompt':'not public','api_key':'fixture',
        'run_id':'safe','body':'sk-simulatedfixture123456789','business':{'items':['actual safe business rule']}})
    assert value['run_id']=='safe' and value['body']=='[REDACTED]'
    assert 'Authorization' not in value and 'system_prompt' not in value and 'api_key' not in value
    assert value['business']['items']==['actual safe business rule'] and count['redacted_strings']==1


def test_normal_workspace_remains_usable_with_same_durable_budget(tmp_path):
    from test_workspace_11a import create,confirm
    app=create_platform_app(tmp_path/'system')
    with TestClient(app) as c:
        planned=create(c)
        assert planned['state']=='awaiting_confirmation',planned
        final=confirm(c,planned)
        assert final['state']=='succeeded',final
        report=c.get('/api/v1/workflow-runs/'+final['run_id']).json()
        assert report['budget_account_id']==planned['budget_account_id']
        ledger=app.state.runtime.durability.ledger.account(report['budget_account_id'])
        assert ledger['occupied_calls']==final['usage']['calls']
