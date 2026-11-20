"""11-A: explicit simulated providers, no network or real credentials."""
import asyncio
from copy import deepcopy
import json
import time

from fastapi.testclient import TestClient
import pytest

from futurecup.api.app import create_app
from futurecup.bootstrap import build_runtime
from futurecup.config import Settings
from futurecup.adapters.llm import MockLLMProvider
from futurecup.adapters.workspace_demo import template_plan
from futurecup.adapters.workspace_validation import WorkspaceValidator
from futurecup.application.ports import ModelResponse
from futurecup.domain.errors import RuntimeFault


class Simulation(MockLLMProvider):
    def __init__(self, mutate=None, outputs=None, delay=None, failure=False):
        self.mutate,self.outputs,self.delay,self.failure=mutate,list(outputs or []),delay,failure
        self.requests=[];self.active=0
    async def infer(self, request):
        self.requests.append(request)
        self.active+=1
        try:
            if self.delay==request.model:await asyncio.sleep(10)
            if self.failure:raise RuntimeFault('simulated_provider_failure','SIMULATED failure')
            if request.model=='mock-workspace-planner' and self.mutate:
                plan=template_plan('SIMULATED plan','architecture');self.mutate(plan)
                return ModelResponse(json.dumps(plan),(),0,0,None,False)
            if request.model=='mock-workspace-worker' and self.outputs:
                return ModelResponse(json.dumps(self.outputs.pop(0)),(),0,0,None,False)
            return await super().infer(request)
        finally:self.active-=1


def application(provider=None,path=':memory:'):
    settings=Settings(environment='test',provider_config_json='[]',database_url='sqlite:///'+str(path),enable_real_llm=False)
    runtime=build_runtime(settings)
    if provider:runtime.providers['mock']=provider
    return create_app(settings,runtime)


def wait(client,identifier,states=('awaiting_confirmation','failed','succeeded','cancelled','interrupted')):
    for _ in range(300):
        r=client.get('/api/v1/workspace/tasks/'+identifier);assert r.status_code==200,r.text
        data=r.json()
        if data['state'] in states:return data
        time.sleep(.01)
    pytest.fail('Workspace did not reach a real terminal/confirmation state')


def create(client,**kwargs):
    r=client.post('/api/v1/workspace/tasks',json={'request':'Analyse requirements; design modules; review constraints',**kwargs})
    assert r.status_code==202,r.text
    return wait(client,r.json()['id'])


def confirm(client,task):
    r=client.post('/api/v1/workspace/tasks/'+task['id']+'/confirm',json={'revision':task['revision']})
    assert r.status_code==202,r.text
    return wait(client,task['id'],('succeeded','failed','cancelled'))


@pytest.mark.parametrize('scenario,data',[
    ('architecture',{}),('document',{'text':'A local document with a traceable source sentence.'}),
    ('schedule',{'tasks':['A','B','C','D'],'dependencies':[['A','C'],['B','C'],['C','D']], 'windows':[{'task':'D','min':4,'max':4}]}),
])
def test_three_real_engine_scenarios_are_explicit_mock_and_require_confirmation(scenario,data):
    provider=Simulation()
    with TestClient(application(provider)) as c:
        task=create(c,scenario=scenario,data=data,planning='template')
        assert task['state']=='awaiting_confirmation' and task['run_id'] is None
        assert c.get('/api/v1/workflow-runs').json()==[] and provider.requests==[]
        task=confirm(c,task)
        assert task['state']=='succeeded' and task['report']['verified'] is True
        assert task['simulated'] is True and task['delivery']['title']
        assert task['usage']['model_calls']==3 and task['report']['resource_usage']['model_calls']==3
        nodes=task['report']['nodes']
        assert nodes[1]['input']['upstream']['s1']==nodes[0]['output']
        assert nodes[2]['input']['upstream']['s2']==nodes[1]['output']
        assert all(n['verified'] for n in nodes)
        assert nodes[0]['input']['responsibilities']==task['plan']['steps'][0]['responsibilities']
        assert nodes[0]['input']['deliverable']==task['plan']['steps'][0]['deliverable']
        assert all(not a['tools'] for a in c.get('/api/v1/agents').json())
        if scenario=='document':assert task['delivery']['evidence'][0]['quote'] in data['text']
        if scenario=='schedule':assert task['delivery']['order'][-1]=='D'


def test_dynamic_mock_structure_and_multiturn_context_without_fixed_plan_claims():
    provider=Simulation()
    with TestClient(application(provider)) as c:
        first=create(c)
        assert len(first['plan']['steps'])==3 and first['usage']['model_calls']==1
        second=c.post('/api/v1/workspace/tasks/'+first['id']+'/requirements',json={'revision':1,'request':'Add a final requirement'})
        assert second.status_code==202
        revised=wait(c,first['id'])
        assert revised['revision']==2 and len(revised['plan_history'])==1
        planner_requests=[r for r in provider.requests if r.model=='mock-workspace-planner']
        assert len(planner_requests)==2
        assert json.loads(planner_requests[1].messages[-1]['content'])['messages']==revised['messages']
        assert revised['usage']['model_calls']==2 and not revised['run_id']
        assert c.post('/api/v1/workspace/tasks/'+first['id']+'/confirm',json={'revision':1}).status_code==409


def cycle(p):p['steps'][0].update(depends_on=['s3'],input_from=['s3'])
def reference(p):p['steps'][1].update(depends_on=['unknown'],input_from=['unknown'])
def tool(p):p['steps'][0]['tools']=['shell']
def validator(p):p['steps'][-1]['verification']='arbitrary_python'
def mapping(p):p['steps'][1]['input_from']=[]
def extra(p):p['steps'][0]['provider']='unapproved-url'
def nodes(p):p['steps'].append(deepcopy(p['steps'][0]))


@pytest.mark.parametrize('mutation,code',[(cycle,'workspace_cycle'),(reference,'workspace_reference'),
    (tool,'workspace_plan_schema'),(validator,'workspace_plan_schema'),(mapping,'workspace_mapping'),
    (extra,'workspace_plan_schema'),(nodes,'workspace_plan_schema')])
def test_invalid_provider_plans_never_compile_or_run(mutation,code):
    provider=Simulation(mutate=mutation)
    with TestClient(application(provider)) as c:
        task=create(c)
        assert task['state']=='failed' and task['error_code']==code
        assert len(provider.requests)==1 and c.get('/api/v1/workflow-runs').json()==[]
        assert c.post('/api/v1/workspace/tasks/'+task['id']+'/confirm',json={'revision':1}).status_code==409


def test_edit_validation_duplicate_confirmation_and_matching_existing_agents():
    with TestClient(application()) as c:
        task=create(c,planning='template');plan=deepcopy(task['plan'])
        plan['steps'][0]['title']='Edited title'
        r=c.put('/api/v1/workspace/tasks/'+task['id']+'/plan',json={'revision':1,'plan':plan})
        assert r.status_code==200 and r.json()['revision']==2
        task=confirm(c,r.json())
        repeat=c.post('/api/v1/workspace/tasks/'+task['id']+'/confirm',json={'revision':2}).json()
        assert repeat['run_id']==task['run_id']
        second=confirm(c,create(c,planning='template'))
        assert second['id']!=task['id'] and second['run_id']!=task['run_id']
        assert all(v['matched_existing'] for v in second['assignments'].values())
        assert len(c.get('/api/v1/workflow-runs').json())==2
        assert c.get('/api/v1/workspace/tasks/'+task['id']).json()['report']==task['report']


@pytest.mark.parametrize('final_success',[True,False])
def test_actual_verification_errors_reach_second_attempt_and_shared_budget_stops(final_success):
    valid={'title':'SIMULATED delivery','summary':'SIMULATED response','items':['SIMULATED item']}
    provider=Simulation(outputs=[{'wrong':'SIMULATED invalid schema'}, valid if final_success else {'still':'invalid'},valid])
    with TestClient(application(provider)) as c:
        task=create(c,request='one task')
        task=confirm(c,task)
        assert task['state']==('succeeded' if final_success else 'failed')
        assert task['usage']['model_calls']==3 and task['usage']['feedback_loops']==1
        assert len(provider.outputs)==1
        requests=[r for r in provider.requests if r.model=='mock-workspace-worker']
        recovery=json.loads(requests[1].messages[-1]['content'])['recovery']
        node=task['report']['nodes'][0]
        audits=c.get('/api/v1/self-loops/'+node['id']+'/verifications').json()
        feedback=c.get('/api/v1/self-loops/'+node['id']+'/feedback').json()
        assert recovery['previous_artifact']['content']=={'wrong':'SIMULATED invalid schema'}
        assert recovery['verification']['id']==audits[0]['record']['id']
        assert recovery['feedback']['id']==feedback[0]['record']['id']
        assert task.get('delivery') is not None if final_success else task.get('delivery') is None


def test_shared_planning_execution_repair_call_limit_is_not_reset():
    provider=Simulation(outputs=[{'invalid':1},{'title':'SIMULATED','summary':'valid','items':['ok']}])
    with TestClient(application(provider)) as c:
        task=create(c,request='single task',max_calls=2)
        task=confirm(c,task)
        assert task['state']=='failed' and task['usage']['model_calls']==2
        assert len(provider.outputs)==1  # No third model call despite recovery policy.
        assert len(provider.requests)==2


@pytest.mark.parametrize('model',['mock-workspace-planner','mock-workspace-worker'])
def test_cancel_propagates_to_actual_runtime_and_workflow(model):
    provider=Simulation(delay=model)
    with TestClient(application(provider)) as c:
        r=c.post('/api/v1/workspace/tasks',json={'request':'one task'});identifier=r.json()['id']
        if model=='mock-workspace-worker':
            task=wait(c,identifier);assert c.post('/api/v1/workspace/tasks/'+identifier+'/confirm',json={'revision':1}).status_code==202
        for _ in range(100):
            if provider.active:break
            time.sleep(.01)
        r=c.post('/api/v1/workspace/tasks/'+identifier+'/cancel')
        assert r.status_code==200 and r.json()['state']=='cancelled'
        assert provider.active==0


def test_overall_planning_timeout_cancels_inflight_provider():
    provider=Simulation(delay='mock-workspace-planner');app=application(provider)
    app.state.workspace.remaining=lambda _: .03
    with TestClient(app) as c:
        task=create(c)
        assert task['state']=='failed' and task['error_code']=='workspace_timeout'
        assert provider.active==0


def test_disabled_real_and_input_permissions_refuse_without_requests():
    provider=Simulation()
    with TestClient(application(provider)) as c:
        assert c.post('/api/v1/workspace/tasks',json={'request':'task','mode':'real','authorize_real':True}).status_code==403
        assert c.post('/api/v1/workspace/tasks',json={'request':'task','max_calls':7}).status_code==422
        assert c.post('/api/v1/workspace/tasks',json={'request':'task','budget_cny':1000.01}).status_code==422
        assert c.post('/api/v1/workspace/tasks',json={'request':'Bearer SIMULATED-secret-token'}).status_code==422
        assert c.post('/api/v1/workspace/tasks',json={'request':'task','api_key':'SIMULATED'}).status_code==422
        assert not provider.requests


@pytest.mark.parametrize('data',[
    {'tasks':['A','B'],'dependencies':[['A','Z']]}, {'tasks':['A','B'],'dependencies':[['A','B'],['B','A']]},
    {'tasks':['A','B'],'dependencies':[['A','B']],'windows':[{'task':'A','min':2,'max':2}]},
])
def test_invalid_or_impossible_schedule_refused(data):
    with TestClient(application()) as c:
        assert c.post('/api/v1/workspace/tasks',json={'request':'schedule','scenario':'schedule','data':data}).status_code==422


def test_restart_preserves_history_without_automatic_model_resume(tmp_path):
    path=tmp_path/'workspace.sqlite3'
    with TestClient(application(path=path)) as c:task=confirm(c,create(c,request='single task'))
    provider=Simulation()
    with TestClient(application(provider,path)) as c:
        stored=c.get('/api/v1/workspace/tasks/'+task['id']).json()
        assert stored['state']=='succeeded' and stored['delivery']==task['delivery']
        assert stored['usage']==task['usage'] and not provider.requests


@pytest.mark.parametrize('prices,budget,authorized,expected',[(False,.5,True,409),(True,.000001,True,409),(True,.5,False,403)])
def test_real_admission_rejects_before_any_http(monkeypatch,prices,budget,authorized,expected):
    from test_serial_collaboration_09a import app, configuration
    requests=[]
    async def never(request):
        requests.append(request)
        raise AssertionError('No HTTP permitted')
    with TestClient(app(monkeypatch,never,configuration(prices=prices))) as c:
        r=c.post('/api/v1/workspace/tasks',json={'request':'task','mode':'real','authorize_real':authorized,'budget_cny':budget})
        assert r.status_code==expected,r.text
        assert requests==[]


def test_real_path_uses_mock_http_shared_budget_and_confirmation(monkeypatch):
    import httpx
    from test_serial_collaboration_09a import app
    from test_real_llm_integration import completion
    requests=[]
    plan=template_plan('SIMULATED HTTP requirement','architecture')
    plan['steps']=plan['steps'][:1]
    async def simulate(request):
        body=json.loads(request.content);requests.append(body)
        value=plan if len(requests)==1 else {'title':'SIMULATED','summary':'Mock HTTP transport','items':['No real request']}
        return httpx.Response(200,json=completion(json.dumps(value),model='SIMULATED-deepseek'))
    with TestClient(app(monkeypatch,simulate)) as c:
        task=create(c,request='one task',mode='real',authorize_real=True,max_calls=2)
        assert task['state']=='awaiting_confirmation' and len(requests)==1,task
        path='/api/v1/workspace/tasks/'+task['id']+'/confirm'
        assert c.post(path,json={'revision':1}).status_code==403
        assert len(requests)==1
        r=c.post(path,json={'revision':1,'authorize_real':True});assert r.status_code==202,r.text
        task=wait(c,task['id'],('succeeded','failed'))
        assert task['state']=='succeeded',task
        assert len(requests)==2 and task['usage']['model_calls']==2
        assert 0<task['estimated_cny_reserved']<=task['budget_cny']
        assert requests[0]['max_tokens']==1024 and requests[1]['max_tokens']==512


@pytest.mark.parametrize('scenario,data,bad,good',[
    ('document',{'text':'Original source'}, {'evidence':[{'quote':'Invented quote','comment':'SIMULATED'}]}, {'evidence':[{'quote':'Original','comment':'SIMULATED'}]}),
    ('schedule',{'tasks':['A','B'],'dependencies':[['A','B']]}, {'order':['B','A']}, {'order':['A','B']}),
])
def test_business_validator_actual_violations_drive_original_feedback(scenario,data,bad,good):
    base={'title':'SIMULATED','summary':'No real reasoning','items':['Mock transport']}
    first={**base,**bad};second={**base,**good}
    provider=Simulation(outputs=[first,second])
    with TestClient(application(provider)) as c:
        task=confirm(c,create(c,request='One objective',scenario=scenario,data=data))
        assert task['state']=='succeeded' and task['delivery']==second
        requests=[r for r in provider.requests if r.model=='mock-workspace-worker']
        recovery=json.loads(requests[1].messages[-1]['content'])['recovery']
        assert recovery['previous_artifact']['content']==first
        assert task['usage']['feedback_loops']==1
        records=c.get('/api/v1/self-loops/'+task['report']['nodes'][0]['id']+'/verifications').json()
        assert records[0]['status']=='FAILED'
        assert 'violations' in json.dumps(records[0])
