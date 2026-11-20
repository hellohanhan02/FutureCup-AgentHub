"""Offline benchmark fairness, statistics and production-policy binding tests."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import statistics

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/bench_scheduler_10a.py'
    spec = importlib.util.spec_from_file_location('benchmark_10a', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.mark.parametrize('scenario', ['A_uniform','B_heterogeneous','C_dynamic_fault','D_bursty'])
def test_shared_inputs_initial_history_and_repeated_results(scenario):
    b = module()
    data = b.workload(scenario,17,32)
    before = deepcopy(data)
    trials = [b.trial(data,p) for p in b.POLICIES]
    for field in ('workload_sha256','initial_stats_digest','warmup_digest'):
        assert len({t[field] for t in trials}) == 1
    for policy,first in zip(b.POLICIES,trials):
        second = b.trial(b.workload(scenario,17,32),policy)
        assert first['deterministic_sha256'] == second['deterministic_sha256']
        assert first['rows'] == second['rows'] and first['decisions'] == second['decisions']
        assert first['metrics']['capacity_violations'] == 0
    assert data == before


def test_separate_seed_streams_and_counter_draws():
    b = module()
    assert b.workload('A_uniform',1) != b.workload('A_uniform',2)
    assert b.draw(1,'task','agent','success') != b.draw(2,'task','agent','success')
    assert b.draw(1,'task','agent','success') == b.draw(1,'task','agent','success')
    assert b.draw(1,'task','agent','success') != b.draw(1,'warmup','agent','success')


def test_exact_production_classes_formulas_and_no_custom_weights():
    from futurecup.application import scheduling
    from futurecup.domain.scheduling import CAASConfig
    b = module()
    assert b.assess is scheduling.assess
    assert b.effective_priority is scheduling.effective_priority
    assert b.FACTORIES == {'fixed':scheduling.FixedSelection,'round_robin':scheduling.RoundRobinSelection,'caas':scheduling.CAASSelection}
    result = b.trial(b.workload('B_heterogeneous',5,20),'caas')
    config = CAASConfig()
    for decision in result['decisions']:
        eligible = [c for c in decision['candidates'] if c['eligible']]
        selected = next(c for c in eligible if c['agent_id']==decision['selected'])
        assert selected['score'] == sum(v*getattr(config,k+'_weight') for k,v in selected['components'].items())
        assert selected['score'] == max(c['score'] for c in eligible)


def test_baselines_are_lowest_eligible_and_true_round_robin():
    b = module()
    data = b.workload('D_bursty',2,32)
    for policy in b.POLICIES[:2]:
        result = b.trial(data,policy)
        last = None
        for d in result['decisions']:
            ids = sorted(c['agent_id'] for c in d['candidates'] if c['eligible'])
            expected = ids[0] if policy=='fixed' else next((i for i in ids if last is None or i>last),ids[0])
            assert d['selected'] == expected
            last = expected


def test_capacity_sweep_and_unavailable_agents_not_selected():
    b = module()
    data = b.workload('C_dynamic_fault',19,64)
    fault = data['fault']
    for policy in b.POLICIES:
        trial = b.trial(data,policy)
        for d in trial['decisions']:
            if fault['unavailable_start'] <= d['time_ms'] < fault['unavailable_end']:
                c = next(c for c in d['candidates'] if c['agent_id']==fault['agent'])
                assert not c['eligible'] and 'agent_unavailable' in c['reasons']
                assert d['selected'] != fault['agent']
        for a in data['agents']:
            events = [(r['start_ms'],1) for r in trial['rows'] if r['agent_id']==a['id']]
            events += [(r['finish_ms'],-1) for r in trial['rows'] if r['agent_id']==a['id']]
            count = 0
            for _,change in sorted(events):
                count += change
                assert 0 <= count <= a['capacity']
            assert count == 0
        assert trial['metrics']['post_fault_tasks'] > 0


def test_bursts_enforce_global_capacity_by_independent_sweep():
    b = module()
    data = b.workload('D_bursty',71,64)
    for policy in b.POLICIES:
        t = b.trial(data,policy)
        events = [(r['start_ms'],1) for r in t['rows']] + [(r['finish_ms'],-1) for r in t['rows']]
        count = peak = 0
        for _,change in sorted(events):
            count += change
            peak = max(peak,count)
            assert 0 <= count <= data['runtime_capacity']
        assert peak == t['metrics']['peak_total_active'] == data['runtime_capacity']


def test_no_candidates_time_out_without_querying_latent_results(monkeypatch):
    b = module()
    data = b.workload('A_uniform',1,3,warmup=0)
    for a in data['agents']:
        a['task_types'] = []
    data['timeout_ms'] = 5
    monkeypatch.setattr(b,'outcome',lambda *a,**k:pytest.fail('No Agent was selected; latent outcome must not be inspected'))
    for policy in b.POLICIES:
        t = b.trial(data,policy)
        assert t['metrics']['unassigned_tasks'] == 3 and t['metrics']['success_rate'] == 0
        assert all(r['latency_ms']==5 and r['error_code']=='scheduling_timeout' for r in t['rows'])


def test_profiles_contain_only_completed_past_observations():
    b = module()
    data = b.workload('B_heterogeneous',2,24,warmup=0)
    t = b.trial(data,'caas')
    for decision in t['decisions']:
        kind = next(r['kind'] for r in t['rows'] if r['task_id']==decision['task_id'])
        for candidate in decision['candidates']:
            observed = [r for r in t['rows'] if r['agent_id']==candidate['agent_id'] and r['kind']==kind
                        and r['finish_ms'] <= decision['time_ms']]
            assert candidate['sample_count'] == len(observed)
            assert candidate['successes'] == sum(r['success'] for r in observed)
            if not observed:
                assert candidate['mean_duration_ms'] is None
                if candidate['eligible']:
                    assert candidate['components']['reliability'] == .5


def test_summary_statistics_include_failures_and_sign_test_holm():
    b = module()
    assert b.nearest_rank([10,20]) == 20
    assert b.nearest_rank(list(range(1,101))) == 95
    assert b.sign_p([1]*10) == 2/1024
    assert b.sign_p([0]*10) == 1
    assert b.sign_p([-1,1]*5) == 1
    interval = b.interval([1,2,3],'test',500)
    assert interval['mean'] == 2 and interval['sd'] == 1
    assert interval == b.interval([1,2,3],'test',500)
    trials = [b.trial(b.workload(s,seed,12),p) for s in b.SCENARIOS for seed in (1,2,3) for p in b.POLICIES]
    summary = b.summarize(trials,100)
    assert len(summary['paired_comparisons']) == 16
    ranked = sorted(summary['paired_comparisons'],key=lambda c:c['p_exact_sign'])
    assert all(a['p_holm'] <= z['p_holm'] for a,z in zip(ranked,ranked[1:]))
    assert all(c['p_holm'] >= c['p_exact_sign'] for c in ranked)
    t = trials[0]
    assert t['metrics']['mean_latency_ms'] == statistics.mean(r['latency_ms'] for r in t['rows'])
    assert t['metrics']['success_rate'] == sum(r['success'] for r in t['rows'])/len(t['rows'])


def test_existing_output_rejected_before_writes(tmp_path):
    from types import SimpleNamespace
    b = module()
    with pytest.raises(ValueError,match='NEW directory'):
        b.run(SimpleNamespace(output=str(tmp_path)))


def test_metric_denominators_explicitly_include_unassigned_failure():
    b = module()
    data = b.workload('A_uniform',1,2)
    rows = [
        {'success':True,'agent_id':'agent-0','finish_ms':10,'latency_ms':10,'waiting_ms':2},
        {'success':False,'agent_id':None,'finish_ms':20,'latency_ms':20,'waiting_ms':4},
    ]
    result = b.metrics(rows,data,[],{'agent-0':1},1,0,{'agent-0':8})
    assert result['success_rate'] == result['failure_rate'] == .5
    assert result['mean_latency_ms'] == 15 and result['p95_latency_ms'] == 20
    assert result['mean_waiting_ms'] == 3 and result['throughput_per_second'] == 100
    assert result['unassigned_tasks'] == 1
    assert result['assignment_counts'] == {'agent-0':1,'agent-1':0,'agent-2':0}
