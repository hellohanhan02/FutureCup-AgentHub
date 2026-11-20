"""Historical input, offline indexes and Mock models only."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from test_workspace_11a import application,create,confirm,Simulation
from test_knowledge_12b import TEXT
from futurecup.adapters.knowledge import check_citations

FIXTURE=json.loads((Path(__file__).parent/'fixtures/knowledge_empty_12c.json').read_text('utf-8'))

def test_same_name_empty_library_refused_before_planning():
    provider=Simulation()
    with TestClient(application(provider)) as c:
        store=c.app.state.knowledge
        full=store.create('校园二手交易业务知识库');empty=store.create(full['name'])
        store.import_document(full['id'],'规范.md',TEXT.encode())
        values=c.get('/api/v1/knowledge-bases').json()
        assert [v['document_count'] for v in values]==[1,0]
        r=c.post('/api/v1/workspace/tasks',json={'request':FIXTURE['messages'][0],'knowledge_base_ids':[empty['id']]})
        assert r.status_code==422 and r.json()['error']['code']=='knowledge_empty' and not provider.requests

def test_actual_long_user_input_retrieves_and_flows_through_three_agents():
    with TestClient(application()) as c:
        store=c.app.state.knowledge;kb=store.create('规范')['id'];store.import_document(kb,'规范.md',TEXT.encode())
        task=confirm(c,create(c,request=FIXTURE['messages'][0],planning='template',knowledge_base_ids=[kb]))
        assert task['state']=='succeeded' and task['knowledge_enhanced'] and task['delivery']['citations']
        for node in task['report']['nodes']:
            a=c.get('/api/v1/self-loops/'+node['id']+'/attempts').json()[0]
            record=a['retrieval_records'][0]
            assert record['hits'] and record['knowledge_base_ids']==[kb]
            assert record['query_version']=='user-role-v2' and '"upstream"' not in record['query']
            assert check_citations(node['output'],[record])
        assert task['report']['nodes'][1]['input']['upstream']['s1']==task['report']['nodes'][0]['output']

def test_nonempty_library_but_no_matches_stops_before_worker_model():
    provider=Simulation()
    with TestClient(application(provider)) as c:
        store=c.app.state.knowledge;kb=store.create('不相关')['id'];store.import_document(kb,'a.txt',b'xyzunique')
        task=confirm(c,create(c,request='校园订单规范',planning='template',knowledge_base_ids=[kb]))
        assert task['state']=='failed' and not task['knowledge_enhanced'] and task['delivery'] is None
        assert task['knowledge_warning'] and not provider.requests
        assert not check_citations({'citations':[]},[{'hits':[]}])

def test_running_task_does_not_claim_knowledge_failure_before_results_exist():
    provider=Simulation(delay='mock-workspace-worker')
    with TestClient(application(provider)) as c:
        store=c.app.state.knowledge;kb=store.create('规范')['id'];store.import_document(kb,'规范.md',TEXT.encode())
        task=create(c,request='校园认证订单',planning='template',knowledge_base_ids=[kb])
        path='/api/v1/workspace/tasks/'+task['id']
        assert c.post(path+'/confirm',json={'revision':1}).status_code==202
        data=c.get(path).json()
        assert data['state']=='executing' and data['knowledge_status']=='pending'
        assert data['knowledge_enhanced'] is None and not data.get('knowledge_warning')
        assert data['delivery'] is None
        c.post(path+'/cancel',json={})
