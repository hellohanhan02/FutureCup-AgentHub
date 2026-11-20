"""Small actual Mock API acceptance, no historical DB or model service required."""
from pathlib import Path
import json,time,sys,hashlib
from demo_processes import ROOT,request
PORT=19320
api=lambda path,body=None:request(PORT,'/api/v1'+path,body)
def finish(run_id):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        value=api('/workflow-runs/'+run_id)
        if value['run']['state'] in ['succeeded','failed','cancelled']:return value
        time.sleep(.1)
    raise TimeoutError('Bounded Mock execution did not finish')
checks=[];runs=[]
checkpoint_path=ROOT/'.validation/mock-run-checkpoint.json'
checkpoint=json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
assert api('/workspace/capabilities')['real_available'] is False
agents=api('/agents');assert len({x['role'] for x in agents})>=3;checks.append('Agent role registration')
flows=api('/workflows');assert len(flows)>=5;checks.append('Existing workflow definitions registered')
inputs=json.loads((ROOT/'source/stable/frontend/src/examples/llm08/inputs.json').read_text())
for fid,inp in [('llm08-requirements',inputs['requirements']),('fc-demo-parallel',{'topic':'MOCK independent parallel collaboration'}),('fc-demo-branch-10b',{'flag':True}),('fc-demo-selfloop',{'request':'MOCK bounded repair'})]:
    identifier=checkpoint.get(fid)
    if not identifier:
        admitted=api('/workflows/'+fid+'/executions',{'input':inp});identifier=admitted['run']['id'];checkpoint[fid]=identifier;checkpoint_path.write_text(json.dumps(checkpoint))
    run=finish(identifier);assert run['run']['state']=='succeeded' and run['simulated'] is True
    runs.append({'workflow':fid,'run_id':run['run']['id'],'state':run['run']['state'],'verified':run['verified'],'nodes':[{'key':n.get('node_id'),'state':n['state']} for n in run['nodes']],'model_calls':run['resource_usage']['model_calls']})
    if fid=='llm08-requirements':assert len(run['nodes'])==3 and run['verified'];checks.append('Three structured agents and independent verification')
    if fid=='fc-demo-parallel':checks.append('Parallel and join')
    if fid=='fc-demo-branch-10b':assert any(n['state']=='skipped' for n in run['nodes']);checks.append('Conditional branch and skipped-path join')
    if fid=='fc-demo-selfloop':
        node=run['nodes'][0]['id'];attempts=api('/self-loops/'+node+'/attempts');feedback=api('/self-loops/'+node+'/feedback');verification=api('/self-loops/'+node+'/verifications')
        assert len(attempts)==2 and feedback and any(v['status']=='FAILED' for v in verification) and any(v['status']=='PASSED' for v in verification)
        checks.append('Actual Mock FAILED -> feedback -> PASSED, two attempts')
        runs[-1].update(attempts=len(attempts),feedback_records=len(feedback))
seed=json.loads((ROOT/'.runtime/seed.json').read_text());kb=seed['knowledge_base_id']
search=api('/knowledge-bases/'+kb+'/search',{'query':'\u8ba2\u5355\u53d6\u6d88\u4e0e\u9501\u5355','top_k':4})
assert search and (search.get('hits') if isinstance(search,dict) else search);checks.append('Chinese keyword retrieval from newly imported synthetic document')
tool_checkpoint=ROOT/'.validation/tool-execution.json'
if tool_checkpoint.exists():execution=api('/executions/'+json.loads(tool_checkpoint.read_text())['id'])
else:
    execution=api('/agents/judge-calculator/executions',{'input':json.dumps({'operation':'add','a':2,'b':3})});tool_checkpoint.write_text(json.dumps({'id':execution['execution']['id']}))
deadline=time.monotonic()+10
while execution['execution']['state'] in ['created','pending','running']:
    if time.monotonic()>deadline:raise TimeoutError('Tool execution deadline')
    time.sleep(.1);execution=api('/executions/'+execution['execution']['id'])
assert execution['execution']['state']=='succeeded' and len(execution['tool_calls'])==1;checks.append('Actual safe calculator tool call')
# A template is explicitly labelled; it is not advertised as dynamic real planning.
task=api('/workspace/tasks',{'request':'\u8bf7\u6839\u636e\u4e1a\u52a1\u89c4\u8303\u8bbe\u8ba1\u6821\u56ed\u4e8c\u624b\u4ea4\u6613\u5e73\u53f0\uff0c\u660e\u786e\u8ba2\u5355\u53d6\u6d88\u4e0e\u9501\u5355\u3001\u4fe1\u7528\u8bc4\u4ef7\u548c\u9690\u79c1\u4fdd\u62a4\u3002','planning':'template','mode':'mock','scenario':'architecture','knowledge_base_ids':[kb],'max_calls':6,'budget_cny':.5})
def task_until(states):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        value=api('/workspace/tasks/'+task['id'])
        if value['state'] in states:return value
        if value['state'] in ['failed','cancelled']:raise AssertionError('Task failed: '+str(value.get('error_code')))
        time.sleep(.1)
    raise TimeoutError('Bounded workspace wait expired')
ready=task_until(['awaiting_confirmation']);api('/workspace/tasks/'+task['id']+'/confirm',{'revision':ready['revision'],'authorize_real':False});final=task_until(['succeeded'])
assert final['delivery']['items'] and final['simulated'] is True
checks.append('User confirmation, knowledge binding, actual generated delivery')
report=api('/workflow-runs/'+final['run_id']);assert len(report['nodes'])==3 and report['verified']
# Save actual structured export, not a replacement answer.
folder=ROOT/'.validation';folder.mkdir(exist_ok=True);(folder/'workspace-delivery.json').write_text(json.dumps(final['delivery'],ensure_ascii=False,indent=2),encoding='utf-8')
checks.append('Actual structured delivery exported')
for port in [19320,15320]:assert request(port,'/api/v1/health')['status']=='ok'
assert '<html' in request(15320,'/').lower();checks.append('Backend health, frontend and Vite proxy')
assert request(PORT,'/api/v1/scheduling/decisions');checks.append('Actual CAAS decisions recorded')
result={'scope':'FRESH_INSTALL_ACTUAL_MOCK_NOT_REAL_LLM','real_requests_sent':0,'checks':checks,'workflow_runs':runs,'workspace':{'id':task['id'],'run_id':final['run_id'],'state':final['state'],'verified':report['verified'],'nodes':len(report['nodes']),'usage':final['usage']},'calculator':{'execution_id':execution['execution']['id'],'tools':len(execution['tool_calls'])},'knowledge_base_id':kb,'search_hits':len(search['hits']) if isinstance(search,dict) and 'hits' in search else len(search)}
(folder/'mock-acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'passed':len(checks),'runs':len(runs),'workspace':result['workspace']},ensure_ascii=False))