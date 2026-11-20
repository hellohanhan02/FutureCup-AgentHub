"""Register existing explicit Mock examples; never overwrite user configurations."""
from pathlib import Path
import json,base64
ROOT=Path(__file__).resolve().parents[1]
def seed(port):
    from demo_processes import request
    api=lambda path,body=None:request(port,'/api/v1'+path,body)
    if api('/workspace/capabilities')['real_available']:raise ValueError('Mock seeding refuses real-enabled services')
    agents=api('/agents');known={x['id']:x for x in agents}
    rows=json.loads((ROOT/'source/stable/frontend/src/examples/llm08/agents.json').read_text(encoding='utf-8'))
    for id,role,model in [('fc-researcher','research','mock-echo'),('fc-writer','writing','mock-echo'),('fc-reviewer','review','mock-echo'),('fc-repair','repair','mock-repair-demo')]:
        rows.append({'id':id,'name':'[MOCK] '+role,'role':role,'responsibilities':'Explicit synthetic demonstration','system_prompt':'MOCK SIMULATED demonstration only','skills':['analysis',role],'tools':[],'provider':'mock','model':model,'version':'1'})
    rows.append({'id':'judge-calculator','name':'[MOCK] calculator','role':'calculator','responsibilities':'Safe calculator demonstration','system_prompt':'MOCK tool call only','provider':'mock','model':'mock-calculator','tools':[{'id':'calculator','version':'1'}]})
    for row in rows:
        if row['id'] in known:
            if any(known[row['id']].get(k)!=row.get(k,'1' if k=='version' else None) for k in ['provider','model','version']):raise ValueError('Existing demo agent differs; refusing overwrite')
        else:api('/agents',row)
    policy={'id':'fc-demo-answer','version':'1','name':'[MOCK] independent answer rule','checks':[{'kind':'json_schema','config':{'schema':{'type':'object','required':['answer'],'properties':{'answer':{'type':'integer','const':42}},'additionalProperties':False}}}]}
    policies=[policy,*json.loads((ROOT/'source/stable/frontend/src/examples/llm08/policies.json').read_text(encoding='utf-8'))]
    existing={(x['ref']['id'],x['ref']['version']):x for x in api('/verification-policies')}
    for row in policies:
        key=(row['id'],row['version'])
        if key in existing:
            if existing[key]['checks']!=row['checks']:raise ValueError('Existing policy differs; refusing overwrite')
        else:api('/verification-policies',row)
    example=ROOT/'source/stable/frontend/src/examples';flows=[json.loads((example/(key+'.json')).read_text(encoding='utf-8')) for key in ['sequential','parallel','selfloop']]
    flows.append(json.loads((example/'llm08/requirements.json').read_text(encoding='utf-8')))
    flows.append(json.loads((ROOT/'source/stable/config/demo-branch-10b.json').read_text(encoding='utf-8')))
    existing={(x['id'],x['version']) for x in api('/workflows')}
    for row in flows:
        if (row['id'],row['version']) not in existing:api('/workflows',row)
    state=ROOT/'.runtime/seed.json'
    if state.exists():
        saved=json.loads(state.read_text(encoding='utf-8'));ids={x['id'] for x in api('/knowledge-bases')}
        if saved['knowledge_base_id'] in ids:return saved
    kb=api('/knowledge-bases',{'name':'[MOCK] Judge campus business rules','description':'Synthetic specification included in source, not private campus data'})
    text=(ROOT/'source/stable/config/campus-business-rules-12b.md').read_bytes()
    doc=api('/knowledge-bases/'+kb['id']+'/documents',{'name':'synthetic-campus-rules.md','content_base64':base64.b64encode(text).decode()})
    saved={'mode':'MOCK_ONLY','knowledge_base_id':kb['id'],'workflow_ids':[x['id'] for x in flows],'agents':len(rows),'document':doc}
    state.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8');return saved