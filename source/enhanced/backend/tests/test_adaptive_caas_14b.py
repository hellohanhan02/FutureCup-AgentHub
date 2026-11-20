"""14-B: pure synthetic profiles, real Runtime admission and explicit Mock only."""
import ast
import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from futurecup.application.adaptive import weights, classify
from futurecup.application.scheduling import assess, evaluate_candidate, CAASSelection
from futurecup.domain.scheduling import CAASConfig, AdaptiveConfig, TaskFeatures, CapacitySnapshot
from futurecup.domain.models import RunLimits, ExecutionState, VersionRef
from futurecup.domain.errors import RuntimeFault
from runtime_support import agent, service, ScriptedMockProvider, response
from scheduling_support import coordinator, request, close
from test_scheduling_algorithm import profile, NOW
from test_scheduling_coordinator import DelayedMock
from test_scheduling_api import create_agents, definition, finish, SETTINGS
from futurecup.api.app import create_app


def task(**kwargs):return request(task_type='analysis',strategy='adaptive_caas',**kwargs)

def event(category,age=0,success=None,duration=50):
    return {'outcome_category':category,'succeeded':category=='success' if success is None else success,
            'occurred_at':(NOW-timedelta(seconds=age)).isoformat(),'duration_ms':duration}


def test_legacy_production_functions_unchanged_from_14a():
    from futurecup.application import scheduling
    root=Path(__file__).resolve().parents[2]
    old=subprocess.check_output(['git','show','8bd2caf8c0d6c5f860287849a19d70412024562b:backend/src/futurecup/application/scheduling.py'],cwd=root)
    new=Path(scheduling.__file__).read_text('utf-8')
    for name in ('assess','effective_priority','FixedSelection','RoundRobinSelection','CAASSelection'):
        nodes=[]
        for text in (old,new):nodes.append(next(n for n in ast.parse(text).body if getattr(n,'name',None)==name))
        assert ast.dump(nodes[0])==ast.dump(nodes[1]),name


@pytest.mark.parametrize('f',[{}, {'complexity':1}, {'latency_target_ms':1}, {'cost_budget':.01},
    {'reliability_target':1}, {'complexity':1,'latency_target_ms':1,'cost_budget':.01,'reliability_target':1}])
def test_weights_bounded_normalized_and_deterministic(f):
    t=task(features=TaskFeatures(**f));cfg=CAASConfig();options=AdaptiveConfig()
    w=weights(t,cfg,options)
    assert sum(w.values())==pytest.approx(1) and all(.05<=v<.8 for v in w.values())
    assert w==weights(t,cfg,options)
    assert weights(task(),cfg,options)=={'match':.3,'reliability':.25,'load':.2,'cost':.1,'latency':.15}
    zero=weights(task(),CAASConfig(match_weight=1,reliability_weight=0,load_weight=0,cost_weight=0,latency_weight=0),options)
    assert min(zero.values())>=.05


@pytest.mark.parametrize('f',[{'complexity':True},{'complexity':2},{'reliability_target':-1},
    {'latency_target_ms':0},{'cost_budget':float('inf')},{'cost_budget':'2'}])
def test_bad_features_rejected(f):
    with pytest.raises((TypeError,ValueError)):TaskFeatures(**f)


def test_weight_reasons_and_ablations():
    cfg=CAASConfig();p=profile()
    costly=task(features=TaskFeatures(cost_budget=.1))
    assert weights(costly,cfg,AdaptiveConfig())['cost']>cfg.cost_weight
    reliable=task(features=TaskFeatures(reliability_target=1))
    assert weights(reliable,cfg,AdaptiveConfig())['reliability']>cfg.reliability_weight
    assert weights(reliable,cfg,AdaptiveConfig(dynamic_weights=False))['reliability']==cfg.reliability_weight
    c=evaluate_candidate(reliable,p,cfg)
    assert c.adaptation['features']==asdict(reliable.features)
    assert c.score==pytest.approx(sum(c.components[k]*w for k,w in c.adaptation['weights'].items()))


@pytest.mark.parametrize('category',['tool_failure','cancelled','validation_failure','unknown','timeout'])
def test_unattributed_and_external_errors_not_ability_failures(category):
    c=evaluate_candidate(task(),profile(),CAASConfig(),(event(category),))
    assert c.components['reliability']==.5 and c.adaptation['effective_samples']==0


@pytest.mark.parametrize('state,code,success,category',[
 ('succeeded',None,True,'success'),('succeeded',None,False,'validation_failure'),
 ('failed','tool_failed',False,'tool_failure'),('failed','provider_response_invalid',False,'unknown'),
 ('failed','agent_execution_failed',False,'agent_failure'),('failed','provider_connection_failed',False,'unavailable'),
 ('timed_out',None,False,'timeout'),('cancelled',None,False,'cancelled')])
def test_error_attribution(state,code,success,category):assert classify(state,code,success)==category


def test_http_error_attribution_is_operational_not_agent_quality():
    assert classify('failed','provider_http_error',False,'rate_limited')=='unavailable'
    assert classify('failed','provider_http_error',False,'authentication_failed')=='unknown'


def test_cold_start_low_sample_and_unknown_old_observations():
    one=evaluate_candidate(task(),profile(),CAASConfig(),(event('success'),))
    assert .5<one.components['reliability']<.6 and one.adaptation['low_samples']
    old=event('unknown',success=False)
    assert evaluate_candidate(task(),profile(),CAASConfig(),(old,)).components['reliability']==.5
    future=event('agent_failure',age=-10)
    assert evaluate_candidate(task(),profile(),CAASConfig(),(future,)).components['reliability']==.5


def test_recent_failure_recovers_without_permanent_exclusion():
    hist=(event('agent_failure',.2),event('agent_failure',.1))
    blocked=evaluate_candidate(task(),profile(),CAASConfig(),hist)
    assert not blocked.eligible and blocked.reasons==('adaptive_cooldown',)
    recovered=evaluate_candidate(task(),profile(observed_at=NOW+timedelta(seconds=2)),CAASConfig(),hist)
    assert recovered.eligible and recovered.components['reliability']<.5
    reset=evaluate_candidate(task(),profile(),CAASConfig(),hist+(event('success'),))
    assert reset.eligible
    no_health=evaluate_candidate(task(),profile(),CAASConfig(),hist,AdaptiveConfig(health=False))
    assert no_health.eligible


def test_recency_and_attribution_mechanisms_independent():
    hist=(event('agent_failure',age=600),event('success'))
    a=evaluate_candidate(task(),profile(),CAASConfig(),hist)
    b=evaluate_candidate(task(),profile(),CAASConfig(),hist,AdaptiveConfig(recency=False))
    assert a.components['reliability']>b.components['reliability']
    history=(event('tool_failure'),)
    assert evaluate_candidate(task(),profile(),CAASConfig(),history,AdaptiveConfig(attribution=False)).components['reliability']<.5


@pytest.mark.parametrize('strategy',['fixed','round_robin','caas','adaptive_caas'])
def test_same_new_cost_constraint_for_all_policies(strategy):
    t=task(features=TaskFeatures(cost_budget=1));t=replace(t,strategy=strategy)
    assert not evaluate_candidate(t,profile(estimated_cost=2),CAASConfig()).eligible
    assert not evaluate_candidate(t,profile(estimated_cost=None),CAASConfig()).eligible
    assert evaluate_candidate(t,profile(estimated_cost=.5),CAASConfig()).eligible
    full=profile(estimated_cost=.5,capacity=CapacitySnapshot(2,2,2,10,1))
    assert 'agent_capacity' in evaluate_candidate(t,full,CAASConfig()).reasons


def test_runtime_atomic_duplicate_claims_cancel_and_capacity_release():
    async def run():
        s=coordinator(service(DelayedMock()))
        try:
            await s.runtime.registry.register(agent(max_concurrency=2))
            t=request(strategy='adaptive_caas')
            assignments=await asyncio.gather(*(s.dispatch(t,'private task text',RunLimits()) for _ in range(12)))
            assert len({a.execution_id for a in assignments})==1
            await s.runtime.cancel(assignments[0].execution_id);await s.complete(t.task_id)
            assert (await s.runtime.capacity('analyst')).active==0
            history=await s.repository.adaptive_history(VersionRef('analyst','1'),'default','observed_mock')
            assert history[-1]['outcome_category']=='cancelled'
            jobs=[s.dispatch(request('batch'+str(i),strategy='adaptive_caas'),'work',RunLimits()) for i in range(8)]
            accepted=await asyncio.gather(*jobs)
            await asyncio.gather(*(s.runtime.wait(a.execution_id) for a in accepted))
            capacity=await s.runtime.capacity('analyst')
            assert capacity.active==0 and capacity.peak_active<=2
            records=await s.repository.decisions()
            assert 'private task text' not in json.dumps(records)
        finally:await close(s)
    asyncio.run(run())


def test_coordinator_short_cooldown_recovers_and_records_categories():
    async def run():
        provider=ScriptedMockProvider(RuntimeFault('agent_execution_failed','SIMULATED'),RuntimeFault('agent_execution_failed','SIMULATED'),response())
        s=coordinator(service(provider));s.adaptive_config=AdaptiveConfig(cooldown_seconds=.02)
        try:
            await s.runtime.registry.register(agent())
            for i in range(3):
                t=request(str(i),strategy='adaptive_caas')
                a=await s.dispatch(t,'mock',RunLimits());await s.runtime.wait(a.execution_id);await s.complete(t.task_id)
            history=await s.repository.adaptive_history(VersionRef('analyst','1'),'default','observed_mock')
            assert [h['outcome_category'] for h in history]==['agent_failure','agent_failure','success']
            assert (await s.runtime.capacity('analyst')).active==0
            assert any(d['outcome']=='waiting_capacity' for d in await s.repository.decisions())
        finally:await close(s)
    asyncio.run(run())


def test_timeout_releases_actual_capacity_and_is_neutral_quality():
    async def run():
        s=coordinator(service(DelayedMock()))
        try:
            await s.runtime.registry.register(agent())
            t=request(strategy='adaptive_caas')
            a=await s.dispatch(t,'mock',RunLimits(model_timeout_seconds=.001))
            r=await s.runtime.wait(a.execution_id);await s.complete(t.task_id)
            assert r.execution.state==ExecutionState.TIMED_OUT
            assert (await s.runtime.capacity('analyst')).active==0
            h=await s.repository.adaptive_history(VersionRef('analyst','1'),'default','observed_mock')
            assert h[0]['outcome_category']=='timeout'
        finally:await close(s)
    asyncio.run(run())


def test_workflow_api_optional_strategy_metadata_and_features_validation():
    with TestClient(create_app(SETTINGS)) as c:
        create_agents(c)
        assert 'adaptive_caas' not in c.get('/api/v1/scheduling/strategies').json()['strategies']
        assert 'adaptive_caas' in c.get('/api/v1/scheduling/strategies?include_adaptive=true').json()['strategies']
        data=definition('adaptive_caas',scheduling_features={'complexity':.8,'reliability_target':.9})
        assert c.post('/api/v1/workflows',json=data).status_code==201
        r=c.post('/api/v1/workflows/scheduled/executions',json={'input':'sensitive user requirement'})
        report=finish(c,r.json()['run']['id']);assert report['run']['state']=='succeeded'
        records=c.get('/api/v1/scheduling/decisions',params={'run_id':report['run']['id']}).json()
        assert records[0]['algorithm_revision']=='adaptive-caas-v1'
        candidate=next(a for a in records[0]['candidates'] if a['eligible'])
        assert candidate['adaptation']['features']['complexity']==.8
        assert 'sensitive user requirement' not in json.dumps(records)
        overview=c.get('/api/v1/console/overview')
        assert overview.status_code==200 and overview.json()['strategies']['adaptive_caas']==1
        bad=definition('adaptive_caas',scheduling_features={'complexity':2});bad['id']='bad'
        assert c.post('/api/v1/workflows',json=bad).status_code==422


def benchmark():
    p=Path(__file__).resolve().parents[2]/'scripts/bench_caas_14b.py'
    spec=importlib.util.spec_from_file_location('bench14b_test',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


@pytest.mark.parametrize('scenario',['A_uniform','B_heterogeneous','C_dynamic_fault','D_bursty','E_mixed_types','F_cost_latency','G_cold_start','H_long_recovery'])
def test_benchmark_determinism_capacity_and_shared_inputs(scenario):
    m=benchmark();data=m.workload(scenario,17,16)
    trials=[m.trial(data,p) for p in ('fixed','round_robin','caas','adaptive_caas')]
    for key in ('workload_sha256','initial_stats_digest','warmup_digest'):assert len({t[key] for t in trials})==1
    for t in trials:
        assert t['metrics']['capacity_violations']==0 and t['metrics']['remaining_capacity_claims']==0
        assert t['deterministic_sha256']==m.trial(data,t['strategy'])['deterministic_sha256']
        # Independent sweep, not just simulator's own counter.
        for agent in data['agents']:
            events=[(r['start_ms'],1) for r in t['rows'] if r['agent_id']==agent['id']]
            events += [(r['finish_ms'],-1) for r in t['rows'] if r['agent_id']==agent['id']]
            active=0
            for _,delta in sorted(events):active+=delta;assert 0<=active<=agent['capacity']
            assert active==0
    if scenario in m.b.SCENARIOS:
        for t in trials[:3]:
            old=m.b.trial(data,t['strategy'])
            assert [{k:r[k] for k in old['rows'][0]} for r in t['rows']]==old['rows']


def test_benchmark_binding_stats_and_seed_separation():
    m=benchmark()
    from futurecup.application import scheduling
    assert m.evaluate_candidate is scheduling.evaluate_candidate
    assert m.workload('G_cold_start',11)!=m.workload('G_cold_start',50001)
    assert m.b.sign_p([1]*20)<.001
    assert m.b.nearest_rank([1,2,3,4,5],.95)==5


def test_long_clock_decay_and_capacity_recovery():
    m=benchmark();data=m.workload('H_long_recovery',90001,16)
    for i,t in enumerate(data['tasks']):t['arrival_ms']=i*300000
    data['fault'].update(degrade_start=1800000,unavailable_start=2400000,unavailable_end=3000000,recovered_at=3600000)
    for strategy in ('fixed','round_robin','caas','adaptive_caas'):
        result=m.trial(data,strategy)
        assert result['metrics']['makespan_ms']>3*3600000
        assert result['metrics']['capacity_violations']==result['metrics']['remaining_capacity_claims']==0
    c=evaluate_candidate(task(),profile(),CAASConfig(),(event('agent_failure',age=86400),))
    assert c.eligible and c.components['reliability']==.5
