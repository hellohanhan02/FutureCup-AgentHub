"""Explicit MOCK simulation of planning/work products, never real reasoning."""
import json
import re


def template_plan(request: str, scenario: str) -> dict:
    titles={'architecture':['需求梳理','架构设计','约束审查'],'document':['文档提取','引用核对','审查交付'],'schedule':['约束整理','顺序规划','依赖复核']}[scenario]
    return make_plan(request,scenario,titles,False)


def make_plan(request,scenario,titles,parallel):
    steps=[]
    for i,title in enumerate(titles):
        deps=([f's{x+1}' for x in range(i)] if parallel and i==len(titles)-1 else [] if parallel or i==0 else [f's{i}'])
        steps.append({'id':f's{i+1}','title':title[:100],'role':title[:100],'responsibilities':title[:200],
            'skills':[scenario],'depends_on':deps,'input_from':deps,'tools':[],
            'verification':{'document':'source_quotes','schedule':'precedence','architecture':'schema'}[scenario] if i==len(titles)-1 else 'schema',
            'deliverable':title[:100]+'的结构化成果'})
    return {'name':request[:50],'goal':request,'steps':steps,'expected_delivery':'结构化文档与可审计成果'}


def mock_response(model, content):
    value=json.loads(content)
    if model=='mock-workspace-planner':
        request=value['messages'][-1]
        clauses=[x.strip() for x in re.split('[。；;\n]',request) if x.strip()][:3] or [request]
        # Simulated structure varies with supplied clauses; not proof of LLM planning.
        plan=make_plan(request,value['scenario'],clauses,'并行' in request and len(clauses)>2)
        if value.get('output_profile')=='design_document' and len(plan['steps'])==3:
            plan['steps'][-1].update(depends_on=['s1','s2'],input_from=['s1','s2'])
        if value.get('knowledge_contract') in ('staged-rules-v1','staged-rules-v2') and len(plan['steps'])==3:
            plan['steps'][0].update(depends_on=[],input_from=[])
            plan['steps'][1].update(depends_on=['s1'],input_from=['s1'])
            plan['steps'][2].update(depends_on=['s1','s2'],input_from=['s1','s2'])
        return plan
    root=value.get('original_input',value)
    source=root['request'];scenario=source['scenario']
    title=root['role'];upstream=root.get('upstream',{})
    result={'title':title,'summary':'[MOCK 模拟] '+source['messages'][-1][:200],
            'items':['[MOCK 模拟] '+title,'已接收上游：'+(', '.join(upstream) or '用户原始需求')]}
    rule=root['verification']
    if root.get('document_output'):
        result['markdown']='\n\n'.join('## '+h+'\n'+('[MOCK 模拟] 此为无网络流程测试文本，不代表真实系统设计或模型推理。'*5) for h in ('需求分析','系统架构设计','数据库设计','技术选型','方案审查'))
    if rule=='source_quotes':result['evidence']=[{'quote':source['data']['text'][:100],'comment':'[MOCK 模拟] 引用来自输入原文；不代表真实语义审查。'}]
    if rule=='precedence':
        pending=set(source['data']['tasks']);order=[]
        while pending:
            ready=sorted(i for i in pending if not any(b==i and a in pending for a,b in source['data']['dependencies']))
            if not ready:break
            order.extend(ready);pending-=set(ready)
        result['order']=order  # Pure test-output simulation; production never substitutes this for a real model answer.
        if source['data'].get('windows'):
            from itertools import permutations
            from futurecup.adapters.workspace_validation import schedule_errors
            result['order']=list(next(p for p in permutations(source['data']['tasks']) if not schedule_errors(source['data'],p)))
    return result
