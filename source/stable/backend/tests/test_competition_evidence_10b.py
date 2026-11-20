"""Synthetic isolated SQLite audit fixtures; never call a model or use real keys."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from futurecup.adapters import competition_evidence as evidence
from futurecup.api import replay


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence,'ROOT',tmp_path)
    monkeypatch.setattr(replay,'ROOT',tmp_path)
    path=tmp_path/'fixture.sqlite3'
    with sqlite3.connect(path) as c:
        c.executescript('CREATE TABLE workflow_runs(id TEXT,snapshot TEXT); CREATE TABLE workflow_definitions(id TEXT,version TEXT,definition TEXT); CREATE TABLE safety_records(kind TEXT,id TEXT,body TEXT); CREATE TABLE scheduling_decisions(id TEXT,run_id TEXT,decision TEXT);')
        def put(kind,value):
            key=value['record']['id'] if 'record' in value else value['id']
            c.execute('INSERT INTO safety_records VALUES(?,?,?)',(kind,key,json.dumps(value)))
        for index,rid in enumerate(evidence.RUNS):
            names=['analyse','design','review'] if index==0 else ['schedule']
            ref={'id':f'wf{index}','version':'1'}
            definition={'ref':ref,'nodes':[{'id':n,'kind':'task','config':{'agent_id':n,'input':{'$ref':'nodes.analyse.output'}}} for n in names],'edges':[]}
            c.execute('INSERT INTO workflow_definitions VALUES(?,?,?)',(ref['id'],'1',json.dumps(definition)))
            report={'run':{'id':rid,'definition_ref':ref,'state':'succeeded','revision':1,'parent_run_id':None},
                'nodes':[],'events':[],'input':{},'output':{},'started_at':'2026-11-12T10:00:00+00:00',
                'ended_at':'2026-11-12T10:00:01+00:00','elapsed_ms':1000,'simulated':True,'verified':True,
                'error_code':None,'resource_usage':{'model_calls':3 if index==0 else 2,'reported_tokens':10,'feedback_loops':index}}
            policy={'id':f'policy{index}','ref':{'id':f'policy{index}','version':'1'},'checks':[]}
            put('policy',policy)
            artifacts={};old=None
            for n in names:
                node={'id':f'node{index}-{n}','node_id':n,'state':'succeeded','agent_ref':{'id':n,'version':'1'},
                    'started_at':report['started_at'],'ended_at':report['ended_at'],'elapsed_ms':1,
                    'input':{},'output':{},'attempt_ids':[],'verification_ids':[],'feedback_ids':[]}
                put('session',{'id':node['id']})
                for ordinal in range(1,3 if index else 2):
                    aid=f'a{index}-{n}-{ordinal}';vid='v'+aid;fid='f'+aid
                    content={'request_id':'SIMULATED','requirements':[],'components':[]}
                    artifacts[n]=content
                    inp={} if index else ({'analysis':artifacts['analyse']} if n=='design' else {'analysis':artifacts['analyse'],'design':artifacts['design']} if n=='review' else {})
                    if old and index:
                        inp={'original_input':{},'recovery':{'previous_attempt_id':old['id'],
                            'previous_artifact':old['artifact'],'verification':{'id':old['verification_id'],'verdict':'fail'},
                            'feedback':{'id':old['feedback_id'],'action':'prompt_retry','category':'ASSERTION_FAILED'}}}
                    a={'id':aid,'ordinal':ordinal,'node_id':node['id'],'input_value':inp,'input_ref':{'id':aid,'version':str(ordinal)},
                        'input_digest':'input-digest','artifact':{'ref':{'id':node['id']+':artifact','version':str(ordinal)},
                        'digest':'digest-'+aid,'content':content,'simulated':True},'verification_id':vid,
                        'provider':'mock','model':'SIMULATED','resolved_model':'SIMULATED','agent_ref':node['agent_ref'],
                        'policy_ref':policy['ref'],'provider_config_ref':None,'input_tokens':3,'output_tokens':2,
                        'usage_complete':True,'model_calls':[],'simulated':True}
                    v={'record':{'id':vid,'verdict':'fail' if index and ordinal==1 else 'pass','verifier_ref':policy['ref'],
                                'reason_codes':[]},'attempt_id':aid,'artifact_ref':a['artifact']['ref'],
                        'artifact_digest':a['artifact']['digest'],'evidence':[],'origin':'artifact'}
                    put('attempt',a);put('verification',v)
                    node['attempt_ids'].append(aid);node['verification_ids'].append(vid)
                    if index and ordinal==1:
                        f={'record':{'id':fid,'verification_id':vid},'attempt_id':aid,'repair_action':'prompt_retry',
                            'category':'ASSERTION_FAILED','evidence_refs':[vid]}
                        put('feedback',f);node['feedback_ids'].append(fid)
                        old={**a,'feedback_id':fid}
                report['nodes'].append(node)
            c.execute('INSERT INTO workflow_runs VALUES(?,?)',(rid,json.dumps(report)))
    return path,tmp_path/'.data/package'


def test_export_immutable_sources_complete_links_and_mock_identity(source):
    path,out=source
    before=path.read_bytes()
    public=evidence.export(path,out)
    assert path.read_bytes()==before
    assert all(r['simulated'] is True for r in public['runs'])  # Never relabel fixture as real.
    assert public['runs'][0]['execution_order']==['analyse','design','review']
    assert all(m['matches'] for m in public['runs'][0]['data_mappings'])
    assert public['runs'][1]['nodes'][0]['feedback_bindings'][0]['confirmed']
    assert all(r['runtime_build_version'] is None for r in public['runs'])
    manifest=json.loads((out/'manifest.json').read_text())
    assert all(evidence.sha((out/n).read_bytes())==h for n,h in manifest.items())
    with pytest.raises(ValueError,match='NEW directory'):
        evidence.export(path,out)


@pytest.mark.parametrize('method',['post','put','patch','delete','options'])
def test_replay_blocks_mutation_no_raw_private_files(source,method):
    path,out=source;evidence.export(path,out)
    with TestClient(replay.create_replay_app(out)) as client:
        response=getattr(client,method)('/api/v1/workflows/anything/executions')
        assert response.status_code==405 and response.json()['error']['code']=='replay_read_only'
        assert client.get('/private/source-manifest.json').status_code==404
        assert client.get('/replay.json').status_code==404
        assert client.get('/api/v1/demo-charts/source-manifest.json').status_code==404


def test_replay_compatible_queries_binding_and_missing_ids(source):
    path,out=source;evidence.export(path,out)
    with TestClient(replay.create_replay_app(out)) as client:
        assert client.get('/api/v1/health').json()['scope']=='historical_read_only'
        info=client.get('/api/v1/demo-info').json()
        assert info['real_calls_enabled'] is False and len(info['runs'])==2
        rid=evidence.RUNS[1]
        report=client.get('/api/v1/workflow-runs/'+rid).json()
        assert report['replay']['read_only'] and report['simulated'] is True
        node=report['nodes'][0]
        attempts=client.get('/api/v1/self-loops/'+node['id']+'/attempts').json()
        assert len(attempts)==2 and attempts[1]['input_value']['recovery']['previous_attempt_id']==attempts[0]['id']
        assert client.get('/api/v1/demo-structure/'+rid).json()['ref']==report['run']['definition_ref']
        assert len(client.get('/api/v1/workflow-runs').json())==2
        assert client.get('/api/v1/workflow-runs/not-present').status_code==404
        assert client.get('/api/v1/self-loops/nope/attempts').status_code==404


def test_sanitizer_drops_credentials_requests_and_personal_text():
    counts=Counter()
    data={'Authorization':'SIMULATED-secret','system_prompt':'private prompt','messages':[{'content':'private body'}],
          'headers':{'Cookie':'SIMULATED-cookie'},'api_key':'SIMULATED-key','content':{
          'requirements':[{'id':'R1','text':'contact person@example.invalid'}],'summary':'Bearer SIMULATED-placeholder'},
          'model':'SIMULATED','unknown_customer_field':'private'}
    clean=evidence.sanitize(data,counts)
    text=json.dumps(clean)
    assert clean['model']=='SIMULATED' and clean['content']['requirements'][0]['text']=='[REDACTED]'
    assert clean['content']['summary']=='[REDACTED]'
    assert all(s not in text for s in ('Authorization','Cookie','api_key','private prompt','private body','person@'))


def test_tampering_or_missing_package_fails_closed(source):
    path,out=source;evidence.export(path,out)
    (out/'replay.json').write_text('{}')
    with TestClient(replay.create_replay_app(out)) as client:
        assert client.get('/api/v1/demo-info').status_code==503
        assert client.get('/api/v1/workflow-runs').status_code==503
    with TestClient(replay.create_replay_app(out.parent/'absent')) as client:
        assert client.get('/api/v1/demo-info').status_code==503


def test_public_challenge_semantics_and_all_constraint_groups_survive_redaction():
    value={'spec':{'semantics':{'position_base':1,'single_machine':True,'idle_allowed':False},
        'constraints':{'dependencies':[['A','D']],'priority_before':[['B','A']],
            'immediately_before':[['E','D']],'position_windows':[{'task':'C','min':2,'max':3}],
            'completion_deadlines':[{'task':'C','latest':4}],
            'min_gaps':[{'before':'D','after':'F','min_gap':2}]}}}
    assert evidence.sanitize(value,Counter())==value


@pytest.mark.parametrize('flag',[True,False])
def test_demo_branch_uses_existing_engine_and_explicit_mock(flag):
    from futurecup.api.app import create_app
    from futurecup.config import Settings
    from test_workflow_api import completed
    definition=json.loads((Path(__file__).resolve().parents[2]/'config/demo-branch-10b.json').read_text('utf-8'))
    with TestClient(create_app(Settings(environment='test',database_url='sqlite:///:memory:',
                                      provider_config_json='[]',enable_real_llm=False))) as client:
        for identifier in ('fc-researcher','fc-writer'):
            assert client.post('/api/v1/agents',json={'id':identifier,'name':identifier,'role':'demo',
                'responsibilities':'MOCK SIMULATED only','system_prompt':'MOCK SIMULATED',
                'provider':'mock','model':'mock-echo'}).status_code==201
        assert client.post('/api/v1/workflows',json=definition).status_code==201
        response=client.post('/api/v1/workflows/'+definition['id']+'/executions',json={'input':{'flag':flag}})
        assert response.status_code==202
        result=completed(client,response.json()['run']['id'])
        nodes={n['node_id']:n for n in result['nodes']}
        assert result['simulated'] is True and result['verified'] is False
        assert result['run']['state']=='succeeded'
        assert nodes['yes' if flag else 'no']['state']=='succeeded'
        assert nodes['no' if flag else 'yes']['state']=='skipped'
        assert nodes['join']['state']=='succeeded'
