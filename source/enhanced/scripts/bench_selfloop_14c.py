"""Actual frozen 14-B vs precise 14-C engines: offline Mock-only evaluation.
No invented full restart baseline. Root budgets and fault fixtures are identical.
"""
import argparse,asyncio,csv,gzip,hashlib,io,json,os,subprocess,sys,zipfile,random
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE='14f801946a01773b71e78b5c2533103bc705e975'
CASES=('format','schema','business','permanent','parallel_schema','tool_switch','tool_timeout',
       'permission','budget','service_unavailable','verifier_error','knowledge_missing','citation','conflict','idempotent_effect','unknown_effect')


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


async def evaluate(mode,seed,case):
    from dataclasses import replace,asdict
    sys.path.insert(0,str(ROOT/'backend/tests'))
    from verification_support import safety_engine,close
    from runtime_support import agent,response,tool_call
    from workflow_support import task,workflow
    from futurecup.domain.models import VersionRef
    from futurecup.domain.verification import VerificationPolicy,VerificationCheck,CheckEvidence
    from futurecup.domain.errors import RuntimeFault
    from futurecup.adapters.tools import ToolDefinition
    from futurecup.adapters.knowledge import KnowledgeStore,check_citations
    from futurecup.adapters.coverage import assess_output
    base=random.Random(seed).randint(1,100)
    one=case in ('idempotent_effect','unknown_effect')
    parallel=case=='parallel_schema'
    spec={'seed':seed,'case':case,'base':base,'calls':6,'repairs':1,'node_attempts':2,'graph':'single' if one else 'parallel' if parallel else 'sequential'}
    effects=[];requests=[];feedback_observed=[]
    class Model:
        is_simulated=True
        async def infer(self,r):
            original=json.loads(r.messages[1]['content']);repair=original.get('recovery');original=original.get('original_input',original)
            tag=original['tag'];raw=json.dumps(r.messages,ensure_ascii=False).encode();entry={'tag':tag,'input_units':len(raw),'output_units':0,'phase':'repair' if repair else 'initial'};requests.append(entry)
            if repair:feedback_observed.append(bool(repair.get('previous_artifact') and repair.get('verification')))
            fail=tag=='b' and (not repair or case in ('permanent','permission'))
            if fail and case=='permission':raise RuntimeFault('tool_unauthorized','SIMULATED permission refusal')
            if fail and case=='service_unavailable':raise RuntimeFault('provider_rate_limited','SIMULATED outage')
            value=(original.get('upstream') or {}).get('value',original.get('base',base))+1
            if tag=='b' and case in ('tool_switch','tool_timeout','idempotent_effect','unknown_effect'):
                if r.messages[-1]['role']!='tool':
                    names=[t['function']['name'] for t in r.tools];name='fallback' if 'broken' not in names and 'fallback' in names else 'broken' if case=='tool_switch' else 'effect'
                    return response('',(tool_call(name,{'value':value}),))
                value=json.loads(r.messages[-1]['content'])['value']
            output={'value':value,'tag':tag}
            if case in ('citation','conflict'):
                data=json.loads(r.messages[-1]['content'])['untrusted_knowledge']
                h=data['hits'][0];rule=data['coverage'][0]['critical_rule'] or h['text']
                output.update(items=[rule],citations=[{'chunk_id':h['id'],'quote':rule}])
                if fail and case=='citation':output['citations']=[]
                if fail and case=='conflict':output['items']=['Order lock must expire after 30 minutes']
            if fail and case=='format':content='{invalid-json'
            else:
                if fail and case in ('schema','parallel_schema','permanent'):output.pop('value')
                if fail and case in ('business','idempotent_effect','unknown_effect'):output['value']+=1
                content=json.dumps(output)
            entry['output_units']=len(content.encode());return response(content)
    model=Model();e=safety_engine(model);knowledge=None
    try:
        if case in ('citation','conflict','knowledge_missing'):
            knowledge=KnowledgeStore(':memory:');e.runtime.knowledge=knowledge;kb=knowledge.create('SYNTHETIC')['id']
            knowledge.import_document(kb,'synthetic.md',b'Unrelated synthetic archive' if case=='knowledge_missing' else b'Order lock must expire after 15 minutes.')
        else:kb=None
        tools=()
        if case in ('tool_switch','tool_timeout','idempotent_effect','unknown_effect'):
            async def handler(value):
                effects.append(value)
                if case=='tool_timeout':await asyncio.sleep(.02)
                return {'value':value}
            async def broken(value):raise RuntimeFault('tool_execution_failed','SIMULATED broken read')
            names=['broken','fallback'] if case=='tool_switch' else ['effect']
            for name in names:
                ref=VersionRef(name,'1');args=[ref,'SIMULATED predefined test tool',{'type':'object','properties':{'value':{'type':'integer'}},'required':['value'],'additionalProperties':False}]
                if mode=='precise':args.append('idempotent' if case=='idempotent_effect' else 'unknown' if case=='unknown_effect' else 'none')
                e.runtime.tools.register(ToolDefinition(*args),broken if name=='broken' else handler)
            tools=tuple(VersionRef(n,'1') for n in names)
        await e.runtime.registry.register(agent(tool_refs=tools,max_concurrency=4,knowledge_base_ids=(kb,) if kb else ()))
        async def independent(artifact):
            if case=='verifier_error':raise RuntimeError('SIMULATED validator fault')
            node=artifact.ref.id.rsplit(':',1)[0];session=await e.selfloop.repository.session(node);a=await e.selfloop.repository.attempt(session['current_attempt']);execution=await e.runtime.get(a['execution_id'])
            coverage=assess_output(artifact.content,execution.retrieval_records)
            ok=check_citations(artifact.content,execution.retrieval_records) and all(c['status'] in ('RULE_REFLECTED','SOURCE_UNAVAILABLE_ADVICE') for c in coverage)
            return CheckEvidence('external_tests','PASSED' if ok else 'FAILED','knowledge_citation_valid' if ok else 'knowledge_citation_invalid',reference=artifact.digest,details={'coverage':coverage})
        e.selfloop.backend.external['independent-test']=independent
        nodes=[]
        for tag in (('b',) if one else ('a','b')):
            expected=base+1 if tag=='a' or one or parallel else base+2
            ref=VersionRef('policy-'+tag,'1')
            checks=[VerificationCheck('json_schema',{'schema':{'type':'object','required':['value'],'properties':{'value':{'type':'integer'}}}}),
                    VerificationCheck('numeric',{'path':'value','expected':expected})]
            if case in ('citation','conflict','verifier_error'):checks.append(VerificationCheck('external_tests',{'test_id':'independent-test'}))
            await e.selfloop.verifier.register(VerificationPolicy(ref,'Independent arithmetic/source rule',tuple(checks)))
            recovery={'max_attempts':2,'max_feedback_loops':1,'on_failure':'tool_switch' if case=='tool_switch' else 'prompt_retry','timeout_seconds':.25 if case=='unknown_effect' else 3,'feedback_mode':'errors_only'}
            if mode=='precise':recovery['precise']=True
            if case=='tool_switch':recovery.update(max_replans=1,tool_options=[[asdict(VersionRef('fallback','1'))]])
            inp={'tag':tag,'base':base}
            if tag=='b' and not one and not parallel:inp['upstream']={'$ref':'nodes.a.output'}
            if kb:inp['request']={'messages':['order lock'],'retrieval_topics':[{'id':'lock','query':'order lock','required':True}]}
            nodes.append(task(tag,input=inp,output_mode='json',verification=asdict(ref),recovery=recovery,
                limits={'max_calls':3,'max_model_calls':2,'tool_timeout_seconds':.005 if case=='tool_timeout' else .5}))
        flow=workflow(nodes,[] if one or parallel else [('a','b')],scheduling_strategy='adaptive_caas',
            resource_limits={'max_calls':1 if case=='budget' else 6,'max_feedback_loops':1,'max_attempts':6,'max_replans':1})
        await e.register(flow);report=await e.run(flow.ref,{'base':base})
        audit=[]
        for n in report.nodes:
            attempts=await e.selfloop.repository.attempts(n.id)
            results=await e.selfloop.repository.results(n.id);feedbacks=await e.selfloop.repository.feedbacks(n.id)
            audit.append({'node':n.node_id,'state':n.state.value,'error_code':n.error_code,'attempts':attempts,'verification':results,'feedback':feedbacks})
        repeat_attempts=sum(max(0,len(a['attempts'])-1) for a in audit)
        repeated=sum(max(0,sum(bool(t.get('model_calls')) or t.get('resources',{}).get('tool_calls',0)>0 for t in a['attempts'])-1) for a in audit)
        units=sum(r['input_units']+r['output_units'] for r in requests)
        expected_reason={'format':'output_format_error','schema':'schema_mismatch','business':'assertion_failed','permanent':'schema_mismatch',
            'parallel_schema':'schema_mismatch','tool_switch':'tool_execution_failed','tool_timeout':'tool_timeout','permission':'tool_unauthorized',
            'budget':'shared_call_budget','service_unavailable':'provider_rate_limited','verifier_error':'verifier_internal_error',
            'knowledge_missing':'knowledge_no_matches','citation':'knowledge_citation_invalid','conflict':'knowledge_citation_invalid',
            'idempotent_effect':'assertion_failed','unknown_effect':'assertion_failed'}[case]
        causes={reason for a in audit for v in a['verification'] for reason in v['record']['reason_codes']}
        assert expected_reason in causes, 'Actual evidence does not match injected fault; reject the trial'
        b_node=next(n for n in report.nodes if n.node_id=='b')
        expected_value=base+1 if one or parallel else base+2
        known_stops={expected_reason,'recovery_limit','verification_fail','verification_error','repair_human_timeout','repair_human_required',
                     'repair_knowledge_missing','repair_schema_failed','repair_business_constraint','repair_verifier_error'}
        accurate=(b_node.output.get('value')==expected_value if report.verified else
                  expected_reason in causes and all(a['error_code'] in known_stops for a in audit if a['state']=='failed'))
        inconsistent=int((await e.runtime.capacity('analyst')).active!=0)
        for node,a in zip(report.nodes,audit):
            inconsistent+=int(node.verified and node.state.value!='succeeded')+int(node.state.value!='succeeded' and node.output is not None)
            if node.verified:
                accepted=next((v for v in a['verification'] if v['record']['id']==node.verification_ids[-1]),None)
                inconsistent+=int(not accepted or accepted['record']['verdict']!='pass' or accepted['artifact_digest']!=e.selfloop.backend.digest(node.output))
        if not one and not parallel and b_node.input is not None:
            inconsistent+=int(b_node.input['upstream']!=report.nodes[0].output)
        costs=report.resource_usage
        work_by_tag={t:sum(r['tag']==t for r in requests)*10 for t in ('a','b')}
        logical=max(work_by_tag.values()) if parallel else sum(work_by_tag.values())
        manual=any(f['repair_action']=='human' for a in audit for f in a['feedback'])
        result={'mode':mode,'seed':seed,'case':case,'input_sha256':digest(spec),'spec':spec,
            'metrics':{'verified':report.verified,'repeated_nodes':repeated,'repeat_attempts':repeat_attempts,'model_calls':costs.get('model_calls',0),
                'tool_calls':costs.get('tool_calls',0),'simulated_token_units':units,'simulated_cost_units':units/1000000,
                'logical_execution_latency_ms':None if manual else logical,
                'state_inconsistencies':inconsistent,
                'unsafe_effect_retries':max(0,len(effects)-1) if case=='unknown_effect' else 0,
                'actual_effect_executions':len(effects),'termination_reason_accurate':bool(accurate),
                'feedback_contains_actual_artifact_and_evidence':all(feedback_observed) if feedback_observed else None},
            'state':report.run.state.value,'error_code':report.error_code,'node_audit':audit,
            'model_requests_summary':requests,
            'engine_source_sha256':hashlib.sha256(Path(sys.modules['futurecup.application.selfloop'].__file__).read_bytes()).hexdigest(),
            'source_code':str(Path(sys.modules['futurecup.application.selfloop'].__file__).relative_to(ROOT))}
        return result
    finally:
        await close(e)
        if knowledge and hasattr(knowledge,'connection'):knowledge.connection.close()


def worker(mode,out,seeds):
    out=Path(out).resolve()
    if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Worker output must be NEW and inside v1.1')
    with gzip.open(out,'wt',encoding='utf-8') as stream:
        for seed in seeds:
            for case in CASES:stream.write(json.dumps(asyncio.run(evaluate(mode,seed,case)),separators=(',',':'))+'\n')
    print('Completed actual engine worker',mode,len(seeds)*len(CASES),flush=True)


def run(output,seed_count):
    out=Path(output).resolve()
    if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Output must be a NEW directory inside v1.1')
    seeds=list(range(101,101+seed_count));out.mkdir(parents=True)
    config={'seeds':seeds,'cases':CASES,'base_commit':BASE,'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/src/futurecup').rglob('*.py')},
        'max_calls':6,'budget_case_max_calls':1,'max_repairs':1,
        'metrics':'Simulated token units = UTF8 message/output bytes, NOT API tokens; cost = units/1e6 artificial units, NOT CNY. Logical latency = fixed 10ms per model invocation on graph critical path, manual wait unknown/null.',
        'baseline':'Actual old node-local SelfLoop; NOT an invented full-workflow restart. Terminal cross-run recovery has no legacy equivalent and is covered separately by tests.',
        'termination_metric':'Independent successful value, or expected injected leaf evidence plus a compatible terminal stop; not old/new enum-name equality.',
        'repeat_metric':'repeated_nodes counts additional attempts with model/tool execution evidence; repeat_attempts also includes admission-only attempts.'}
    (out/'configuration.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    frozen=ROOT/'.data'/('frozen-14b-14c-'+digest(str(out))[:12])
    if frozen.exists():raise ValueError('Frozen source directory already exists; do not overwrite')
    archive=(ROOT/'reproduction-baselines/backend-14b.zip').read_bytes()
    frozen.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as z:z.extractall(frozen)
    env={'SystemRoot':os.environ.get('SystemRoot','C:\\Windows'),'PYTHONUTF8':'1','TEMP':str(ROOT/'.data'),'TMP':str(ROOT/'.data')}
    for mode,path in [('legacy',frozen/'backend/src'),('precise',ROOT/'backend/src')]:
        child={**env,'PYTHONPATH':str(path)}
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',mode,'--output',str(out/(mode+'.jsonl.gz')),'--seeds',str(seed_count)],cwd=ROOT,env=child,check=True)
    records=[]
    for mode in ('legacy','precise'):
        with gzip.open(out/(mode+'.jsonl.gz'),'rt',encoding='utf-8') as stream:records.extend(json.loads(line) for line in stream)
    for seed in seeds:
        for case in CASES:assert len({r['input_sha256'] for r in records if r['seed']==seed and r['case']==case})==1
    with (out/'raw-metrics.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=['mode','seed','case','state','error_code',*records[0]['metrics']];writer=csv.DictWriter(stream,fields);writer.writeheader()
        for r in records:writer.writerow({k:r[k] for k in ('mode','seed','case','state','error_code')}|r['metrics'])
    summary=[]
    for case in CASES:
        for mode in ('legacy','precise'):
            group=[r for r in records if r['mode']==mode and r['case']==case]
            metrics={k:sum(r['metrics'][k] for r in group if r['metrics'][k] is not None)/sum(r['metrics'][k] is not None for r in group) if any(r['metrics'][k] is not None for r in group) else None for k in group[0]['metrics']}
            summary.append({'case':case,'mode':mode,'n':len(group),'metrics':metrics})
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# Actual old node-local vs precise SelfLoop (MOCK ONLY)','',config['baseline'],'',config['metrics'],'',
        '| Case | Mode | Verified rate | Repeated nodes | Model calls | Token proxy | Logical ms | Unsafe retries | Termination evidence accuracy |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in summary:
        m=g['metrics'];lines.append(f"| {g['case']} | {g['mode']} | {m['verified']:.2f} | {m['repeated_nodes']:.2f} | {m['model_calls']:.2f} | {m['simulated_token_units']:.1f} | {m['logical_execution_latency_ms']} | {m['unsafe_effect_retries']:.2f} | {m['termination_reason_accurate']:.2f} |")
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()},indent=2),encoding='utf-8')
    print('Retained all',len(records),'actual engine trials; source baseline',BASE,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker',choices=['legacy','precise']);p.add_argument('--seeds',type=int,default=20);p.add_argument('--output',default='results/selfloop-14c/evaluation');args=p.parse_args()
    if not 1<=args.seeds<=30:raise ValueError('Bounded seed count required')
    if args.worker:worker(args.worker,args.output,list(range(101,101+args.seeds)))
    else:run(args.output,args.seeds)
