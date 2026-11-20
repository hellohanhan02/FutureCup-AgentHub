"""14-C: real engines with explicit deterministic Mock faults; never real LLM."""
import asyncio
from dataclasses import replace,asdict
import json
import pytest
from futurecup.application.local_recovery import LocalRecoveryService
from futurecup.application.feedback import FeedbackEngine
from futurecup.domain.errors import RuntimeFault
from futurecup.domain.models import VersionRef,NodeKind,WorkflowEdge,ExecutionState
from futurecup.domain.verification import RecoveryLimits,VerificationCheck,VerificationPolicy,ResourceBudget
from futurecup.adapters.tools import ToolDefinition
from verification_support import safety_engine,setup,definition,finish,close,REF,CHECK
from runtime_support import agent,response,ScriptedMockProvider,tool_call
from workflow_support import task,workflow


class NodeMock:
    is_simulated=True
    def __init__(self, failures=()):self.failures=list(failures);self.calls=[]
    async def infer(self,r):
        value=json.loads(r.messages[1]['content'])
        original=value.get('original_input',value);tag=original['tag'];self.calls.append((tag,value))
        if tag in self.failures:self.failures.remove(tag);return response('{invalid JSON')
        return response(json.dumps({'answer':42,'source':original.get('data')}))


def jsonnode(key,parents=(),**cfg):
    return task(key,verification=asdict(REF),output_mode='json',input={'tag':key,'data':{'$ref':'nodes.'+parents[0]+'.output'} if parents else {'$ref':'input'}},**cfg)


async def mocksetup(e):
    await setup(e,checks=(VerificationCheck('json_schema',{'schema':{'type':'object','required':['answer'],'properties':{'answer':{'const':42}}}}),))


def test_precise_schema_and_format_feedback_is_actual_and_legacy_unchanged():
    async def run():
        for precise,expected in [(False,'OUTPUT_FORMAT_ERROR'),(True,'SCHEMA_FAILED')]:
            e=safety_engine(ScriptedMockProvider(response('{}'),response('{"answer":42}')))
            try:
                await setup(e)
                report,attempts,verifications,feedbacks=await finish(e,definition(precise=precise,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry'))
                assert report.verified and len(attempts)==2
                assert feedbacks[0]['category']==expected
                if precise:
                    assert feedbacks[0]['diagnosis']['evidence_refs']==[verifications[0]['record']['id']]
                    assert report.nodes[0].repair_diagnosis['category']=='SCHEMA_FAILED'
            finally:await close(e)
    asyncio.run(run())


@pytest.mark.parametrize('code,category,allowed',[
 ('tool_timeout','TIMEOUT',True),('tool_execution_failed','TOOL_EXECUTION_ERROR',True),
 ('tool_unauthorized','PERMISSION_DENIED',False),('shared_call_budget','BUDGET_EXHAUSTED',False),
 ('provider_rate_limited','SERVICE_UNAVAILABLE',True),('knowledge_no_matches','KNOWLEDGE_MISSING',False),
 ('unknown_runtime','AGENT_RUNTIME_ERROR',False)])
def test_precise_execution_attribution_and_retryability(code,category,allowed):
    async def run():
        e=safety_engine(ScriptedMockProvider(RuntimeFault(code,'SIMULATED no private data'),response('{"answer":42}')))
        try:
            await setup(e)
            report,attempts,results,feedback=await finish(e,definition(precise=True,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry'))
            assert feedback[0]['category']==category
            assert feedback[0]['diagnosis']['repairable']==allowed
            assert len(attempts)==(2 if allowed else 1)
            assert report.verified==allowed
        finally:await close(e)
    asyncio.run(run())


@pytest.mark.parametrize('coverage,expected',[('SOURCE_NOT_CITED','CITATION_MISSING'),('SOURCE_NOT_REFLECTED','CITATION_MISSING'),('POSSIBLE_CONFLICT','RULE_CONFLICT'),('INSUFFICIENT_EVIDENCE','KNOWLEDGE_MISSING')])
def test_rag_business_evidence_precise_categories(coverage,expected):
    from futurecup.domain.verification import CheckEvidence
    async def check(artifact):return CheckEvidence('external_tests','FAILED','knowledge_citation_invalid',reference=artifact.digest,details={'coverage':[{'status':coverage}]})
    async def run():
        e=safety_engine(ScriptedMockProvider(response('{"answer":42}')),external={'knowledge-test':check})
        try:
            await setup(e,checks=(VerificationCheck('external_tests',{'test_id':'knowledge-test'}),))
            report,a,v,f=await finish(e,definition(precise=True))
            assert not report.verified and f[0]['category']==expected
            assert f[0]['diagnosis']['artifact_digest']==v[0]['artifact_digest']
        finally:await close(e)
    asyncio.run(run())


def test_terminal_resume_reuses_only_verified_prefix_new_run_and_same_request_id():
    async def run():
        provider=NodeMock(['b']);e=safety_engine(provider)
        try:
            await mocksetup(e)
            flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')])
            await e.register(flow);source=await e.run(flow.ref,{'value':'MOCK'})
            assert source.run.state.value=='failed' and source.nodes[0].verified
            service=LocalRecoveryService(e)
            resumes=await asyncio.gather(*(service.resume(source.run.id,'repair-key',source.run.revision) for _ in range(8)))
            assert len({r.run.id for r in resumes})==1
            current=await e.wait(resumes[0].run.id)
            assert current.run.id!=source.run.id and current.verified
            assert current.run.policy.deadline<=source.run.policy.deadline
            assert current.nodes[0].reused_from['run_id']==source.run.id
            assert current.nodes[0].execution_id is None and not current.nodes[0].model_calls
            assert [tag for tag,_ in provider.calls]==['a','b','b']
            assert provider.calls[-1][1]['recovery']['previous_artifact']['content']=='{invalid JSON'
            assert current.nodes[1].prior_feedback['previous_attempt_id']==source.nodes[1].attempt_ids[-1]
            assert current.nodes[1].input['data']==source.nodes[0].output
            assert current.recovery_audit['retained_nodes']==['a']
            assert current.recovery_audit['reexecuted_nodes']==['b']
            assert (await e.get(source.run.id))==source
            with pytest.raises(RuntimeFault) as err:await service.resume(source.run.id,'repair-key',source.run.revision,('a',))
            assert err.value.code=='recovery_identity_conflict'
        finally:await close(e)
    asyncio.run(run())


def test_invalidated_ancestor_transitively_reexecutes_downstream_not_old_output():
    async def run():
        p=NodeMock(['f']);e=safety_engine(p)
        try:
            await mocksetup(e)
            flow=workflow([jsonnode('a'),jsonnode('b',('a',)),jsonnode('f')],[('a','b')],max_concurrency=1)
            await e.register(flow);old=await e.run(flow.ref,1)
            new=await LocalRecoveryService(e).resume(old.run.id,'refresh',old.run.revision,('a',))
            final=await e.wait(new.run.id)
            assert final.verified and final.recovery_audit['retained_nodes']==[]
            assert final.recovery_audit['invalidated_nodes']==['a','b','f']
            assert len([tag for tag,_ in p.calls if tag=='a'])==2
            assert final.nodes[1].input['data']==final.nodes[0].output
        finally:await close(e)
    asyncio.run(run())


def test_parallel_single_failure_preserves_other_validated_branch_and_rebuilds_join():
    async def run():
        p=NodeMock(['b']);e=safety_engine(p)
        try:
            await mocksetup(e)
            join=task('join');join=replace(join,kind=NodeKind.PARALLEL,config={'join':'all_selected'})
            flow=workflow([jsonnode('a'),jsonnode('b'),join],[('a','join'),('b','join')])
            await e.register(flow);old=await e.run(flow.ref,0)
            resumed=await LocalRecoveryService(e).resume(old.run.id,'parallel',old.run.revision)
            result=await e.wait(resumed.run.id)
            assert result.run.state.value=='succeeded' and result.nodes[0].reused_from
            assert result.nodes[2].output['b']==result.nodes[1].output
        finally:await close(e)
    asyncio.run(run())


def test_exhausted_shared_budget_and_real_unknown_refuse_before_calls():
    async def run():
        p=NodeMock(['b']);e=safety_engine(p)
        try:
            await mocksetup(e)
            flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')],resource_limits={'max_feedback_loops':0})
            await e.register(flow);old=await e.run(flow.ref,0)
            with pytest.raises(RuntimeFault) as err:await LocalRecoveryService(e).resume(old.run.id,'budget',old.run.revision)
            assert err.value.code=='shared_feedback_loops_budget'
            assert len(p.calls)==2
        finally:await close(e)
    asyncio.run(run())


def test_idempotent_tool_singleflight_conflicting_args_and_failed_call_not_replayed():
    from futurecup.adapters.tools import InMemoryToolRegistry
    async def run():
        registry=InMemoryToolRegistry();count=0
        async def effect(value):
            nonlocal count
            count+=1;await asyncio.sleep(.005);return {'value':value}
        ref=VersionRef('effect','1')
        registry.register(ToolDefinition(ref,'SIMULATED effect',{'type':'object','properties':{'value':{'type':'integer'}},'required':['value']},'idempotent'),effect)
        values=await asyncio.gather(*(registry.invoke_replay((ref,),'effect',{'value':3},'node-key') for _ in range(12)))
        assert count==1 and all(v=={'value':3} for v in values)
        assert registry.resume_safe((ref,),'node-key')
        with pytest.raises(RuntimeFault) as err:await registry.invoke_replay((ref,),'effect',{'value':4},'node-key')
        assert err.value.code=='tool_replay_conflict' and count==1
    asyncio.run(run())


def test_precise_unknown_effect_requires_human_before_second_model_call():
    async def run():
        provider=ScriptedMockProvider(response('{}'),response('{"answer":42}'))
        e=safety_engine(provider)
        try:
            ref=VersionRef('effect','1')
            async def effect():return None
            e.runtime.tools.register(ToolDefinition(ref,'unknown effect',{'type':'object'}),effect)
            await setup(e,agents=[agent(tool_refs=(ref,))])
            flow=definition(precise=True,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry',timeout_seconds=2)
            await e.register(flow)
            initial=await e.submit(flow.ref, 'MOCK input')
            async with asyncio.timeout(2):
                while not (tickets:=await e.selfloop.repository.tickets()):await asyncio.sleep(.005)
            ticket=tickets[0]
            assert ticket['state']=='pending'
            await e.selfloop.repository.resolve_ticket(ticket['id'],ticket['revision'],ticket['attempt_id'],VersionRef(**ticket['artifact_ref']),'abort','test-operator')
            report=await e.wait(initial.run.id)
            f=await e.selfloop.repository.feedbacks(report.nodes[0].id)
            assert not report.verified and len(provider.requests)==1
            assert f[0]['repair_action']=='human'
        finally:await close(e)
    asyncio.run(run())


@pytest.mark.parametrize('kind',['branch','subworkflow','loop'])
def test_controls_recomputed_and_new_child_ids_preserve_verified_prefix(kind):
    from futurecup.domain.models import WorkflowNode
    async def run():
        p=NodeMock(['b']);e=safety_engine(p)
        try:
            await mocksetup(e)
            if kind=='branch':
                condition=WorkflowNode('condition',NodeKind.CONDITION,{'predicate':{'left':{'$ref':'nodes.a.output.answer'},'op':'eq','right':42}})
                join=WorkflowNode('join',NodeKind.PARALLEL,{'join':'all_selected'})
                flow=workflow([jsonnode('a'),condition,jsonnode('b'),jsonnode('x'),join],
                    [('a','condition'),('condition','b','true'),('condition','x','false'),('b','join'),('x','join')])
            else:
                child=workflow([jsonnode('b')],identifier='child');await e.register(child)
                config={'workflow':asdict(child.ref)}
                if kind=='loop':config.update(max_iterations=2,until={'left':{'$ref':'result.b.answer'},'op':'eq','right':42})
                control=WorkflowNode('control',NodeKind.SUBWORKFLOW if kind=='subworkflow' else NodeKind.LOOP,config)
                flow=workflow([jsonnode('a'),control],[('a','control')])
            await e.register(flow);old=await e.run(flow.ref,{'value':1})
            new=await LocalRecoveryService(e).resume(old.run.id,'control',old.run.revision)
            final=await e.wait(new.run.id)
            assert final.verified and final.nodes[0].reused_from
            if kind=='branch':assert next(n for n in final.nodes if n.node_id=='x').state.value=='skipped'
            else:
                assert final.nodes[1].child_run_ids[0]!=old.nodes[1].child_run_ids[0]
                assert (await e.get(final.nodes[1].child_run_ids[0])).verified
            assert [tag for tag,_ in p.calls].count('a')==1
        finally:await close(e)
    asyncio.run(run())


@pytest.mark.parametrize('safety',['idempotent','irreversible'])
def test_actual_tool_effect_is_not_automatically_duplicated(safety):
    async def run():
        calls=0
        class Model:
            is_simulated=True
            def __init__(self):self.requests=[]
            async def infer(self,r):
                self.requests.append(r)
                repair='recovery' in json.loads(r.messages[1]['content'])
                if r.messages[-1]['role']=='tool':return response(json.dumps({'answer':42 if repair else 0}))
                return response('',(tool_call('effect',{}),))
        model=Model();e=safety_engine(model)
        try:
            ref=VersionRef('effect','1')
            async def effect():
                nonlocal calls
                calls+=1;return {'counter':calls}
            e.runtime.tools.register(ToolDefinition(ref,'SIMULATED side effect',{'type':'object','additionalProperties':False},safety),effect)
            await setup(e,agents=[agent(tool_refs=(ref,))])
            flow=definition(precise=True,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry',timeout_seconds=1)
            flow=replace(flow,nodes=(replace(flow.nodes[0],config={**flow.nodes[0].config,'input':{'marker':'MOCK'}}),))
            report,a,v,f=await finish(e,flow)
            assert calls==1
            assert report.verified==(safety=='idempotent')
            assert len(model.requests)==(4 if safety=='idempotent' else 2)
            if safety=='irreversible':assert report.nodes[0].error_code=='repair_human_timeout'
        finally:await close(e)
    asyncio.run(run())


def test_cancelled_idempotent_effect_becomes_uncertain_and_never_reexecutes():
    from futurecup.adapters.tools import InMemoryToolRegistry
    async def run():
        registry=InMemoryToolRegistry();started=asyncio.Event();stopped=asyncio.Event();count=0;ref=VersionRef('effect','1')
        async def effect():
            nonlocal count
            count+=1;started.set()
            try:await asyncio.sleep(10)
            finally:stopped.set()
        registry.register(ToolDefinition(ref,'SIMULATED',{'type':'object'},'idempotent'),effect)
        job=asyncio.create_task(registry.invoke_replay((ref,),'effect',{},'scope'))
        await started.wait();job.cancel()
        with pytest.raises(asyncio.CancelledError):await job
        assert stopped.is_set()
        with pytest.raises(RuntimeFault) as err:await registry.invoke_replay((ref,),'effect',{},'scope')
        assert err.value.code=='tool_replay_uncertain' and count==1
    asyncio.run(run())


def test_business_rule_feedback_and_repair_failure_are_not_format_errors():
    async def run():
        e=safety_engine(ScriptedMockProvider(response('{"answer":41}'),response('{"answer":41}')))
        try:
            await setup(e,checks=(VerificationCheck('numeric',{'path':'answer','expected':42}),))
            r,a,v,f=await finish(e,definition(precise=True,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry'))
            assert not r.verified and len(a)==2
            assert f[0]['category']=='BUSINESS_CONSTRAINT' and f[-1]['repair_action']=='abort'
        finally:await close(e)
    asyncio.run(run())


def test_persistent_unique_source_claim_blocks_fresh_key_after_service_restart(tmp_path):
    async def run():
        p=NodeMock(['b']);e=safety_engine(p,path=tmp_path/'own.sqlite3')
        try:
            await mocksetup(e);flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            old=await e.run(flow.ref,0)
            result=await LocalRecoveryService(e).resume(old.run.id,'original-key',old.run.revision);await e.wait(result.run.id)
            independent=LocalRecoveryService(e)
            assert (await independent.resume(old.run.id,'original-key',old.run.revision)).run.id==result.run.id
            with pytest.raises(RuntimeFault) as err:await independent.resume(old.run.id,'fresh-key',old.run.revision)
            assert err.value.code=='recovery_source_claimed' and len(p.calls)==3
        finally:await close(e)
    asyncio.run(run())


def test_new_runs_isolated_and_cancel_propagates_to_runtime():
    async def run():
        p=NodeMock(['b','b']);e=safety_engine(p)
        try:
            await mocksetup(e);flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            sources=await asyncio.gather(e.run(flow.ref,'first'),e.run(flow.ref,'second'))
            service=LocalRecoveryService(e)
            runs=await asyncio.gather(*(service.resume(s.run.id,'key',s.run.revision) for s in sources))
            results=await asyncio.gather(*(e.wait(r.run.id) for r in runs))
            assert all(r.verified for r in results)
            assert results[0].nodes[1].input['data']['source']!=results[1].nodes[1].input['data']['source']
            assert not e.runtime._jobs
        finally:await close(e)
    asyncio.run(run())


def test_knowledge_version_change_invalidates_cached_verified_output():
    from futurecup.adapters.knowledge import KnowledgeStore
    async def run():
        p=NodeMock(['b']);e=safety_engine(p);s=KnowledgeStore(':memory:');e.runtime.knowledge=s
        try:
            kb=s.create('Synthetic')['id'];s.import_document(kb,'rule.md',b'Order lock must expire after 15 minutes')
            await mocksetup(e)
            await e.runtime.registry.register(agent('reader',knowledge_base_ids=(kb,)))
            flow=workflow([replace(jsonnode('a'),config={**jsonnode('a').config,'agent_id':'reader'}),jsonnode('b',('a',))],[('a','b')])
            # Task helper already supplies agent_id; build explicit replacement.
            await e.register(flow);old=await e.run(flow.ref,'Order lock')
            s.import_document(kb,'revision.md',b'Order lock requires audit')
            new=await LocalRecoveryService(e).resume(old.run.id,'kb-refresh',old.run.revision);final=await e.wait(new.run.id)
            assert final.verified and final.recovery_audit['retained_nodes']==[]
            assert [t for t,_ in p.calls].count('a')==2
        finally:
            await close(e)
            if hasattr(s,'connection'):s.connection.close()
    asyncio.run(run())


def test_cancel_local_repair_stops_new_execution_and_keeps_original_evidence():
    async def run():
        started=asyncio.Event();stopped=asyncio.Event()
        class Model(NodeMock):
            async def infer(self,r):
                val=json.loads(r.messages[1]['content']);tag=val.get('original_input',val)['tag']
                if tag=='b' and any(t=='b' for t,_ in self.calls):
                    started.set()
                    try:await asyncio.sleep(10)
                    finally:stopped.set()
                return await super().infer(r)
        p=Model(['b']);e=safety_engine(p)
        try:
            await mocksetup(e);flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            source=await e.run(flow.ref,0)
            new=await LocalRecoveryService(e).resume(source.run.id,'cancel-key',source.run.revision)
            await asyncio.wait_for(started.wait(),1);cancelled=await e.cancel(new.run.id)
            assert cancelled.run.state.value=='cancelled' and not cancelled.verified
            assert stopped.is_set() and (await e.runtime.capacity('analyst')).active==0
            assert cancelled.nodes[1].repair_diagnosis['category']=='CANCELLED'
            assert (await e.get(source.run.id))==source
        finally:await close(e)
    asyncio.run(run())


def test_recovery_api_authorization_revision_and_original_interface():
    from fastapi.testclient import TestClient
    from test_workspace_11a import application
    from test_scheduling_api import finish as http_finish
    with TestClient(application(NodeMock(['b']))) as c:
        e=c.app.state.workflows
        c.portal.call(mocksetup,e)
        flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);c.portal.call(e.register,flow)
        source=c.portal.call(e.run,flow.ref,0)
        body={'request_id':'api','expected_revision':source.run.revision}
        url='/api/v1/workflow-runs/'+source.run.id+'/recover'
        assert c.post(url,json=body).status_code==409
        assert c.post(url,json={**body,'authorize_recovery':True,'expected_revision':0}).status_code==409
        accepted=c.post(url,json={**body,'authorize_recovery':True});assert accepted.status_code==202
        current=http_finish(c,accepted.json()['run']['id'])
        assert current['verified'] and current['nodes'][0]['reused_from']['run_id']==source.run.id
        assert c.get('/api/v1/workflow-runs/'+source.run.id).json()['run']['state']=='failed'


def test_precise_reassignment_uses_compatible_agent_and_shared_budget():
    from futurecup.domain.models import WorkflowNode
    class Model:
        is_simulated=True
        def __init__(self):self.models=[]
        async def infer(self,r):self.models.append(r.model);return response('{}' if r.model=='bad' else '{"answer":42}')
    async def run():
        model=Model();e=safety_engine(model)
        try:
            await setup(e,agents=[agent('bad-agent',model='bad'),agent('good-agent',model='good')])
            config={'candidate_agents':['bad-agent','good-agent'],'verification':asdict(REF),'output_mode':'json',
                'recovery':{'precise':True,'on_failure':'reassign','reassign_after':1,'max_attempts':2,'max_feedback_loops':1,'max_replans':1}}
            flow=workflow([WorkflowNode('work',NodeKind.TASK,config)],scheduling_strategy='fixed')
            r,a,v,f=await finish(e,flow)
            assert r.verified and model.models==['bad','good'] and f[0]['repair_action']=='reassign'
            assert r.resource_usage['model_calls']==2 and r.resource_usage['replans']==1
        finally:await close(e)
    asyncio.run(run())


def test_stale_feedback_not_injected_after_upstream_output_changes():
    class Versioned(NodeMock):
        async def infer(self,r):
            result=await super().infer(r)
            value=json.loads(r.messages[1]['content']);value=value.get('original_input',value)
            if value['tag']=='a':
                obj=json.loads(result.content);obj['generation']=len([t for t,_ in self.calls if t=='a']);return response(json.dumps(obj))
            return result
    async def run():
        p=Versioned(['b']);e=safety_engine(p)
        try:
            await mocksetup(e);flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            source=await e.run(flow.ref,0)
            new=await LocalRecoveryService(e).resume(source.run.id,'fresh-input',source.run.revision,('a',));final=await e.wait(new.run.id)
            assert final.verified and final.nodes[0].output['generation']==2
            assert final.nodes[1].input['data']['generation']==2 and final.nodes[1].prior_feedback is None
            assert 'recovery' not in p.calls[-1][1]
        finally:await close(e)
    asyncio.run(run())


def test_unprovable_real_cross_run_budget_denied_without_runtime_or_key_access():
    from types import SimpleNamespace
    from datetime import datetime,timezone,timedelta
    class Repo:
        async def recovery_claim(self,key):return None
    class Engine:
        repository=Repo()
        selfloop=SimpleNamespace(backend=SimpleNamespace(digest=lambda value:'fixed-test-signature'))
        async def start(self):pass
        async def get(self,key):return SimpleNamespace(run=SimpleNamespace(revision=1,state=__import__('futurecup.domain.models',fromlist=['WorkflowState']).WorkflowState.FAILED,
            policy=SimpleNamespace(deadline=datetime.now(timezone.utc)+timedelta(seconds=10))),simulated=False)
    async def run():
        with pytest.raises(RuntimeFault) as err:await LocalRecoveryService(Engine()).resume('synthetic-source','request',1)
        assert err.value.code=='recovery_real_budget_unprovable'
    asyncio.run(run())


@pytest.mark.parametrize('status,category,expected,repairs',[
    (401,'authentication_failed','PERMISSION_DENIED',0),
    (402,'insufficient_balance','BUDGET_EXHAUSTED',0),
    (404,'model_unavailable','RESOURCE_UNAVAILABLE',0),
    (429,'rate_limited','SERVICE_UNAVAILABLE',1),
    (500,'server_error','SERVICE_UNAVAILABLE',1),
])
def test_safe_http_error_metadata_guides_repair(status,category,expected,repairs):
    from futurecup.domain.errors import ProviderHTTPDiagnostic,HTTPErrorCategory
    async def run():
        fault=RuntimeFault('provider_http_error','SIMULATED safe failure',provider_http=ProviderHTTPDiagnostic(status,HTTPErrorCategory(category),None))
        provider=ScriptedMockProvider(fault,response('{"answer":42}'));e=safety_engine(provider)
        try:
            await setup(e)
            r,a,v,f=await finish(e,definition(precise=True,max_attempts=2,max_feedback_loops=1,on_failure='prompt_retry'))
            assert r.verified==bool(repairs) and len(provider.requests)==1+repairs
            assert f[0]['category']==expected
            assert v[0]['evidence'][0]['details']=={'http_status_code':status,'http_error_category':category}
        finally:await close(e)
    asyncio.run(run())


def test_verifier_discards_unapproved_execution_metadata():
    from futurecup.domain.verification import ArtifactVersion
    async def run():
        e=safety_engine()
        try:
            await setup(e)
            artifact=ArtifactVersion(VersionRef('artifact','1'),e.selfloop.backend.digest(None),None,True,execution_error='provider_http_error')
            from futurecup.domain.verification import AttemptRecord
            policy=await e.selfloop.repository.policy(REF)
            await e.selfloop.repository.create_session('node',{'run_id':'test'})
            await e.selfloop.repository.begin_attempt(AttemptRecord('attempt','test','node',1,VersionRef('input','1'),e.selfloop.backend.digest(None),None,
                policy.ref,e.selfloop.backend.digest(asdict(policy)),execution_id='exec',artifact=artifact))
            v=await e.selfloop.verifier.verify(policy,artifact,run_id='test',node_id='node',attempt_id='attempt',execution_id='exec',budget=ResourceBudget(),
                execution_details={'http_status_code':True,'http_error_category':'authentication_failed','Authorization':'PRIVATE TEST FIXTURE','body':'PRIVATE TEST FIXTURE','url':'PRIVATE TEST FIXTURE'})
            assert v.evidence[0].details=={'http_status_code':None,'http_error_category':'authentication_failed'}
            assert 'PRIVATE TEST FIXTURE' not in json.dumps(asdict(v),default=str)
        finally:await close(e)
    asyncio.run(run())


@pytest.mark.parametrize('forks',[0,2])
def test_simulation_guard_blocks_non_simulated_provider_before_validation(forks):
    from runtime_support import service
    class NonNetworkDouble:
        is_simulated=False
        def validate_agent(self,a):raise AssertionError('must not reach provider validation')
        async def infer(self,r):raise AssertionError('must not infer')
    async def run():
        runtime=service(NonNetworkDouble())
        try:
            await runtime.registry.register(agent())
            budget=ResourceBudget(simulation_only=True)
            for _ in range(forks):budget=budget.fork()
            with pytest.raises(RuntimeFault) as err:await runtime.submit('analyst','synthetic',shared_budget=budget)
            assert err.value.code=='recovery_real_budget_unprovable'
            assert (await runtime.capacity('analyst')).active==0
        finally:await runtime.close()
    asyncio.run(run())


def test_mock_source_cannot_resume_into_new_non_simulated_agent_version():
    class NonNetworkDouble:
        is_simulated=False
        async def infer(self,r):raise AssertionError('must not infer')
    async def run():
        provider=NodeMock(['b']);e=safety_engine(provider)
        try:
            await mocksetup(e)
            flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            source=await e.run(flow.ref,0)
            e.runtime.providers['forbidden-real']=NonNetworkDouble()
            await e.runtime.registry.update(replace(agent(),ref=VersionRef('analyst','2'),provider='forbidden-real'),'1')
            with pytest.raises(RuntimeFault) as err:await LocalRecoveryService(e).resume(source.run.id,'changed-provider',source.run.revision)
            assert err.value.code=='recovery_real_budget_unprovable' and len(provider.calls)==2
            assert await e.repository.recovery_claim(source.run.id) is None
        finally:await close(e)
    asyncio.run(run())


def test_unique_repair_claim_survives_new_engine_connection(tmp_path):
    async def run():
        path=tmp_path/'isolated.sqlite3';e=safety_engine(NodeMock(['b']),path=path)
        try:
            await mocksetup(e);flow=workflow([jsonnode('a'),jsonnode('b',('a',))],[('a','b')]);await e.register(flow)
            source=await e.run(flow.ref,0)
            result=await LocalRecoveryService(e).resume(source.run.id,'persisted',source.run.revision)
            await e.wait(result.run.id)
        finally:await close(e)
        restarted=safety_engine(path=path)
        try:
            service=LocalRecoveryService(restarted)
            assert (await service.resume(source.run.id,'persisted',source.run.revision)).run.id==result.run.id
            with pytest.raises(RuntimeFault) as err:await service.resume(source.run.id,'another-key',source.run.revision)
            assert err.value.code=='recovery_source_claimed'
            assert not restarted.runtime._jobs
        finally:await close(restarted)
    asyncio.run(run())



def test_replay_ledger_owns_argument_and_result_snapshots():
    from futurecup.adapters.tools import InMemoryToolRegistry
    async def run():
        registry=InMemoryToolRegistry();entered=asyncio.Event();release=asyncio.Event();count=0
        ref=VersionRef('snapshot-effect','1');shared_result={'value':None}
        async def handler(value):
            nonlocal count
            count+=1;shared_result['value']=value;return shared_result
        registry.register(ToolDefinition(ref,'SIMULATED snapshot test',{'type':'object','properties':{'value':{'type':'integer'}},'required':['value']},'idempotent'),handler)
        original_invoke=registry.invoke
        async def gated(*args):entered.set();await release.wait();return await original_invoke(*args)
        registry.invoke=gated
        arguments={'value':3}
        job=asyncio.create_task(registry.invoke_replay((ref,),'snapshot-effect',arguments,'scope'))
        await asyncio.wait_for(entered.wait(),2)
        arguments['value']=7;release.set()
        result=await job
        assert result=={'value':3}
        shared_result['value']=99;result['value']=88
        assert await registry.invoke_replay((ref,),'snapshot-effect',{'value':3},'scope')=={'value':3}
        assert count==1
    asyncio.run(run())


def test_benchmark_terminal_metric_accepts_bound_verifier_fault_in_both_modes():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/'scripts/bench_selfloop_14c.py'
    spec=importlib.util.spec_from_file_location('benchmark14c',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    async def run():
        for mode in ('legacy','precise'):
            record=await module.evaluate(mode,101,'verifier_error')
            assert not record['metrics']['verified']
            assert record['metrics']['termination_reason_accurate']
            assert record['metrics']['state_inconsistencies']==0
    asyncio.run(run())
