"""Fixed safe contracts for workspace plans; no model-authored validators/code."""
from copy import deepcopy
from itertools import permutations
import json
import re
from jsonschema import Draft202012Validator
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.verification import CheckEvidence

SCENARIOS = ('architecture','document','schedule')
STEP = {'type':'object','additionalProperties':False,
    'required':['id','title','role','responsibilities','skills','depends_on','input_from','tools','verification','deliverable'],
    'properties':{**{k:{'type':'string','minLength':1,'maxLength':300} for k in ('title','role','responsibilities','deliverable')},
        'id':{'type':'string','pattern':'^s[1-3]$'},'skills':{'type':'array','maxItems':4,'uniqueItems':True,'items':{'type':'string','pattern':'^[a-z_]{1,32}$'}},
        'depends_on':{'type':'array','uniqueItems':True,'maxItems':2,'items':{'type':'string'}},
        'input_from':{'type':'array','uniqueItems':True,'maxItems':2,'items':{'type':'string'}},
        'tools':{'type':'array','maxItems':0},'verification':{'enum':['schema','source_quotes','precedence']}}}
STEP['properties']['knowledge_topics']={'type':'array','minItems':1,'maxItems':8,'uniqueItems':True,
    'items':{'type':'string','minLength':1,'maxLength':48}}

PLAN_SCHEMA={'type':'object','additionalProperties':False,'required':['name','goal','steps','expected_delivery'],
    'properties':{'name':{'type':'string','minLength':1,'maxLength':100},'goal':{'type':'string','minLength':1,'maxLength':1500},
        'expected_delivery':{'type':'string','minLength':1,'maxLength':300},'steps':{'type':'array','minItems':1,'maxItems':3,'items':STEP}}}


class WorkspaceValidator:
    def plan_diagnostics(self, plan: object, scenario: str, code: str) -> list[dict]:
        expected={'architecture':'schema','document':'source_quotes','schedule':'precedence'}[scenario]
        if code=='workspace_validator' and isinstance(plan,dict):
            issues=[]
            steps=plan.get('steps',[])
            for i,step in enumerate(steps):
                if isinstance(step,dict) and (step.get('verification') not in ('schema',expected) or
                        (i==len(steps)-1 and step.get('verification')!=expected)):
                    issues.append({'path':f'steps.{i}.verification','rule':'scenario_validator_allowlist',
                        'message':f'当前场景该节点必须使用 {expected}；架构方案不能使用排序 precedence 或原文引用 source_quotes 验证器。' if scenario=='architecture' else f'该场景最终验证器必须为 {expected}，中间节点只能为 schema 或 {expected}。'})
            if issues:return issues
        if code=='workspace_plan_schema':
            issues=[]
            for error in Draft202012Validator(PLAN_SCHEMA).iter_errors(plan):
                issues.append({'path':'.'.join(map(str,error.absolute_path)) or 'plan','rule':str(error.validator),
                    'message':'计划字段不符合结构契约；请按提供的 Schema 修正字段、类型、数量或工具权限。'})
                if len(issues)>=8:break
            if issues:return issues
        messages={'workspace_mapping':'节点读取的上游必须与依赖列表一致。','workspace_cycle':'节点依赖存在循环，必须保持无环。',
            'workspace_reference':'节点引用无效或 ID 重复。','workspace_document_plan':'完整文档需要三个不同角色，最终节点必须读取前两个节点的实际产物。',
            'workspace_plan_json':'规划输出不是有效 JSON，请只返回符合契约的 JSON 对象。','workspace_delivery':'所有节点都必须汇入最终交付。',
            'shared_call_budget':'剩余调用次数不足，无法安全执行本计划。'}
        return [{'path':'plan','rule':code,'message':messages.get(code,'任务规划未通过安全校验，请查看审计记录后新建任务。')}]

    def validate_request(self, value: object) -> None:
        text=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if len(text)>16000 or re.search(r'(?i)\bsk-[a-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY|bearer\s+[a-z0-9_.-]{8,}',text):
            raise RuntimeFault('workspace_sensitive','Input is too large or appears to contain credentials',422)

    def validate_data(self, scenario: str, data: dict) -> None:
        self.validate_request(data)
        if scenario not in SCENARIOS: raise RuntimeFault('workspace_scenario','Unknown scenario',422)
        if scenario=='document':
            if set(data)!={'text'} or not isinstance(data['text'],str) or not 1<=len(data['text'])<=2000:
                raise RuntimeFault('workspace_data','Paste a document of 1..2000 characters',422)
        elif scenario=='schedule':
            schema={'type':'object','required':['tasks','dependencies'],'additionalProperties':False,
                'properties':{'tasks':{'type':'array','minItems':2,'maxItems':8,'uniqueItems':True,'items':{'type':'string','pattern':'^[A-Z]$'}},
                    'dependencies':{'type':'array','maxItems':16,'items':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'string'}}},
                    'windows':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,'required':['task','min','max'],'properties':{'task':{'type':'string'},'min':{'type':'integer','minimum':1,'maximum':8},'max':{'type':'integer','minimum':1,'maximum':8}}}}}}
            if not Draft202012Validator(schema).is_valid(data): raise RuntimeFault('workspace_data','Invalid bounded precedence input',422)
            self._dag(data['tasks'],data['dependencies'])
            if any(w['task'] not in data['tasks'] or w['min']>w['max'] for w in data.get('windows',[])):
                raise RuntimeFault('workspace_data','Invalid position windows',422)
            if data.get('windows') and not any(not schedule_errors(data,p) for p in permutations(data['tasks'])):
                raise RuntimeFault('workspace_unsatisfiable','No feasible ordering for the supplied constraints',422)
        elif data: raise RuntimeFault('workspace_data','Architecture accepts no external data fields',422)

    def _dag(self, ids, pairs) -> None:
        pending=set(ids)
        if len(pending)!=len(ids) or any(a not in pending or b not in pending or a==b for a,b in pairs):
            raise RuntimeFault('workspace_reference','Invalid or duplicate node reference',422)
        while pending:
            ready={i for i in pending if not any(b==i and a in pending for a,b in pairs)}
            if not ready: raise RuntimeFault('workspace_cycle','Cycles are not allowed',422)
            pending-=ready

    def validate_plan(self, plan: dict, scenario: str, data: dict) -> dict:
        self.validate_request(plan)
        if not Draft202012Validator(PLAN_SCHEMA).is_valid(plan):
            raise RuntimeFault('workspace_plan_schema','Plan violates the safe JSON contract',422)
        ids=[s['id'] for s in plan['steps']]
        pairs=[(d,s['id']) for s in plan['steps'] for d in s['depends_on']]
        self._dag(ids,pairs)
        for step in plan['steps']:
            if set(step['input_from'])!=set(step['depends_on']): raise RuntimeFault('workspace_mapping','Upstream mappings must match dependencies',422)
            if step['verification'] not in ('schema',{'document':'source_quotes','schedule':'precedence','architecture':'schema'}[scenario]):
                raise RuntimeFault('workspace_validator','Validator not allowed for scenario',422)
        final=plan['steps'][-1]
        if final['verification']!={'document':'source_quotes','schedule':'precedence','architecture':'schema'}[scenario]:
            raise RuntimeFault('workspace_validator','Final verification cannot be weakened',422)
        # Every node must contribute to final delivery; no hidden discarded side branches.
        ancestors={final['id']}
        while True:
            prior=set(ancestors)
            ancestors.update(a for a,b in pairs if b in ancestors)
            if ancestors==prior:break
        if ancestors!=set(ids):raise RuntimeFault('workspace_delivery','All steps must contribute to final delivery',422)
        self.validate_data(scenario,data)
        return deepcopy(plan)

    def output_schema(self, scenario: str, rule: str, *, document: bool=False, final: bool=False) -> dict:
        props={'title':{'type':'string','minLength':1,'maxLength':200},'summary':{'type':'string','minLength':1,'maxLength':2000},
               'items':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'string','minLength':1,'maxLength':500}}}
        if rule=='source_quotes':props['evidence']={'type':'array','minItems':1,'maxItems':6,'items':{'type':'object','additionalProperties':False,'required':['quote','comment'],'properties':{'quote':{'type':'string','minLength':1,'maxLength':500},'comment':{'type':'string','minLength':1,'maxLength':500}}}}
        if rule=='precedence':props['order']={'type':'array','minItems':2,'maxItems':8,'uniqueItems':True,'items':{'type':'string'}}
        if document:
            props['markdown']={'type':'string','minLength':600 if final else 80,'maxLength':12000}
        return {'type':'object','additionalProperties':False,'properties':props,'required':list(props)}


def workspace_check(workflows, safety):
    async def check(artifact):
        try:
            node_id=artifact.ref.id.rsplit(':',1)[0]
            session=await safety.session(node_id)
            attempt=await safety.attempt(session['current_attempt'])
            report=await workflows.get_report(session['run_id'])
            if attempt['artifact']['digest']!=artifact.digest or attempt['artifact']['content']!=artifact.content:
                return CheckEvidence('external_tests','ERROR','workspace_binding_error')
            source=report.input['data']; scenario=report.input['scenario'];value=artifact.content
            if scenario=='architecture' and report.input.get('output_profile')=='design_document':
                violations=document_errors(value['markdown']);valid=not violations
                reason='workspace_design_sections_missing'
            elif scenario=='document':
                violations=['quote_not_in_source:'+str(i) for i,e in enumerate(value['evidence']) if e['quote'] not in source['text']]
                valid=not violations
                reason='workspace_quote_not_in_source'
            elif scenario=='schedule':
                order=value['order'];violations=schedule_errors(source,order);valid=not violations
                reason='workspace_precedence_violation'
            else:return CheckEvidence('external_tests','ERROR','workspace_validator_mismatch')
            return CheckEvidence('external_tests','PASSED' if valid else 'FAILED','workspace_rule_matched' if valid else reason,
                reference=artifact.digest,details=None if valid else {'violations':violations,'repair_hint':'Correct these actual violations using the unchanged source data: '+ '; '.join(violations)})
        except (KeyError,TypeError,ValueError):
            return CheckEvidence('external_tests','FAILED','workspace_output_fields_missing')
    return check


def schedule_errors(source,order):
    if len(order)!=len(source['tasks']) or set(order)!=set(source['tasks']):return ['permutation']
    errors=[a+' before '+b for a,b in source['dependencies'] if order.index(a)>=order.index(b)]
    errors += [w['task']+' outside position window' for w in source.get('windows',[]) if not w['min']<=order.index(w['task'])+1<=w['max']]
    return errors


def document_errors(markdown: str) -> list[str]:
    # Fixed linear scan, no model-supplied regex or code. Ignore headings inside code fences.
    headings=set();fenced=False
    for line in markdown.splitlines():
        if line.startswith('```'):fenced=not fenced
        elif not fenced:headings.add(line.strip())
    return ['missing_section:'+title for title in ('需求分析','系统架构设计','数据库设计','技术选型','方案审查') if '## '+title not in headings]
