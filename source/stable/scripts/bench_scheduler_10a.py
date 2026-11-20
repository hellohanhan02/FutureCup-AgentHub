"""Deterministic discrete-event SIMULATION of the production selection policies.

No Runtime, LLM, environment settings or historical database is opened. The event
driver models resources; assess/effective_priority/selection are production code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
import platform
import statistics
import subprocess
import sys
from time import perf_counter_ns

from futurecup.application.scheduling import (
    assess, effective_priority, FixedSelection, RoundRobinSelection, CAASSelection,
)
from futurecup.domain.models import VersionRef
from futurecup.domain.scheduling import (
    CAASConfig, CapabilityProfile, CapacitySnapshot, PerformanceStats, SchedulingTask,
)

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ('A_uniform', 'B_heterogeneous', 'C_dynamic_fault', 'D_bursty')
POLICIES = ('fixed', 'round_robin', 'caas')
FACTORIES = dict(zip(POLICIES, (FixedSelection, RoundRobinSelection, CAASSelection)))
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
KINDS = ('analysis', 'design', 'review')
PRIMARY = ('success_rate', 'mean_latency_ms')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def draw(seed, *parts):
    """Counter-based common random numbers, independent of dispatch call order."""
    return int(hashlib.sha256(json.dumps([seed, *parts]).encode()).hexdigest()[:16], 16) / 2**64


def workload(scenario, seed, count=64, warmup=4):
    if scenario not in SCENARIOS or not 1 <= count <= 512 or not 0 <= warmup <= 32:
        raise ValueError('Invalid benchmark workload')
    rng = random.Random(seed)
    specialist = list(KINDS)
    rng.shuffle(specialist)
    capacities = [1, 2, 2] if scenario == 'D_bursty' else [2, 2, 2]
    rng.shuffle(capacities)
    agents = [{'id': f'agent-{i}', 'capacity': capacities[i], 'skills': list(KINDS),
               'task_types': list(KINDS), 'declared_cost': 1.0, 'declared_latency_ms': 50.0}
              for i in range(3)]
    # Private simulation parameters are stored for reproduction, never in profiles.
    latent = {}
    for i, a in enumerate(agents):
        latent[a['id']] = {kind: {'success': (.96 if kind == specialist[i] else .65)
                        if scenario == 'B_heterogeneous' else .94,
                        'duration_ms': (35 if kind == specialist[i] else 70)
                        if scenario == 'B_heterogeneous' else 50} for kind in KINDS}
    tasks = []
    for i in range(count):
        arrival = (i // 16) * 150 if scenario == 'D_bursty' else i * 10
        tasks.append({'id': f'task-{i:04}', 'kind': rng.choice(KINDS), 'arrival_ms': arrival,
                      'priority': rng.choice([20, 50, 80]) if scenario == 'D_bursty' else 50})
    fault = {'agent': rng.choice(agents)['id'], 'degrade_start': 180, 'unavailable_start': 260,
             'unavailable_end': 360, 'recovered_at': 420} if scenario == 'C_dynamic_fault' else None
    return {'scenario': scenario, 'seed': seed, 'agents': agents, 'tasks': tasks,
            'latent': latent, 'fault': fault, 'warmup_per_agent_kind': warmup,
            'runtime_capacity': 4 if scenario == 'D_bursty' else 6, 'timeout_ms': 2000}


def outcome(data, task_id, kind, agent_id, started_ms, *, training=False):
    parameters = data['latent'][agent_id][kind]
    probability, duration = parameters['success'], parameters['duration_ms']
    fault = data['fault']
    if not training and fault and agent_id == fault['agent'] and fault['degrade_start'] <= started_ms < fault['recovered_at']:
        probability, duration = .10, duration * 2
    duration = round(duration * (.8 + .4 * draw(data['seed'], task_id, agent_id, 'duration')), 6)
    success = draw(data['seed'], task_id, agent_id, 'success') < probability
    return success, duration


def update(stats, success, duration):
    """Same completed-observation running mean as the production repository."""
    count = stats['successes'] + stats['failures']
    stats['mean_duration_ms'] = ((stats['mean_duration_ms'] or 0) * count + duration) / (count + 1)
    stats['successes' if success else 'failures'] += 1
    stats['revision'] += 1


def nearest_rank(values, percentile=.95):
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)] if values else None


def trial(data, strategy):
    config, policy = CAASConfig(), FACTORIES[strategy]()
    stats = {(a['id'], k): {'successes': 0, 'failures': 0, 'mean_duration_ms': None, 'revision': 0}
             for a in data['agents'] for k in KINDS}
    training = []
    for a in data['agents']:
        for kind in KINDS:
            for i in range(data['warmup_per_agent_kind']):
                tid = f'warmup-{a["id"]}-{kind}-{i}'
                success, duration = outcome(data, tid, kind, a['id'], -1, training=True)
                update(stats[a['id'], kind], success, duration)
                training.append([tid, success, duration])
    initial_stats_digest = digest(stats_as_list(stats))
    active = Counter({a['id']: 0 for a in data['agents']})
    peaks = dict(active)
    busy_ms = dict(active)
    ready, running, rows, decisions = {}, [], [], []
    waits, planning_ns, planning_passes = Counter(), 0, 0
    tasks = sorted(data['tasks'], key=lambda t: (t['arrival_ms'], t['id']))
    refs = tuple(VersionRef(a['id'], '1') for a in data['agents'])
    cursor, now, peak_total, capacity_violations = 0, 0.0, 0, 0
    fault = data['fault']
    boundaries = sorted(set(fault[k] for k in ('degrade_start','unavailable_start','unavailable_end','recovered_at'))) if fault else []
    while cursor < len(tasks) or ready or running:
        # Same-time completions precede arrivals/claims, then task-ID ordering.
        while running and running[0][0] <= now:
            finish, tid, row = heapq.heappop(running)
            active[row['agent_id']] -= 1
            update(stats[row['agent_id'], row['kind']], row['success'], row['service_ms'])
            rows.append(row)
        while cursor < len(tasks) and tasks[cursor]['arrival_ms'] <= now:
            t = tasks[cursor]
            ready[t['id']] = SchedulingTask(t['id'], 'SIMULATION10A', t['id'], tuple(r.id for r in refs), refs,
                EPOCH + timedelta(milliseconds=t['arrival_ms']), task_type=t['kind'],
                preferred_skills=(t['kind'],), priority=t['priority'], strategy=strategy,
                no_candidate_policy='wait', timeout_seconds=data['timeout_ms'] / 1000)
            cursor += 1
        clock = EPOCH + timedelta(milliseconds=now)
        start = perf_counter_ns()
        ordered = sorted(ready.values(), key=lambda t: (-effective_priority(t, clock, config), t.ready_at, t.task_id))
        planning_ns += perf_counter_ns() - start
        for task in ordered:
            start = perf_counter_ns()
            arrival = (task.ready_at - EPOCH).total_seconds() * 1000
            assessments = []
            for a in data['agents']:
                aid = a['id']
                s = stats[aid, task.task_type]
                available = not (fault and aid == fault['agent'] and fault['unavailable_start'] <= now < fault['unavailable_end'])
                capacity = CapacitySnapshot(active[aid], a['capacity'], sum(active.values()), data['runtime_capacity'],
                                            s['revision'] + active[aid], peaks[aid])
                profile = CapabilityProfile(VersionRef(aid, '1'), tuple(a['task_types']), tuple(a['skills']), (),
                    True, available, True, True, capacity, a['declared_cost'], a['declared_latency_ms'],
                    PerformanceStats(task.task_type, 'simulation', **s),
                    f"1:simulation:{s['revision']}:{active[aid]}:{available}", clock)
                assessments.append(assess(task, profile, config))
            eligible = tuple(a for a in assessments if a.eligible)
            expired = now - arrival >= data['timeout_ms']
            selected = policy.select(eligible) if eligible and not expired else None
            planning_ns += perf_counter_ns() - start
            planning_passes += 1
            if not selected and not expired:
                waits.update(reason for a in assessments for reason in a.reasons)
                continue
            # Record predecision inputs before any latent outcome is requested.
            decision = {'task_id': task.task_id, 'time_ms': now, 'priority': task.priority,
                'effective_priority': effective_priority(task, clock, config), 'selected': selected,
                'selection_reason': {'fixed':'lowest_eligible_agent_id', 'round_robin':'next_eligible_id_after_previous',
                                     'caas':'maximum_production_caas_score_then_id'}[strategy] if selected else 'scheduling_timeout',
                'candidates': [{'agent_id': a.agent_id, 'eligible': a.eligible, 'reasons': a.reasons,
                    'components': a.components, 'score': a.score, 'profile_version': a.profile.profile_version,
                    'sample_count': a.profile.stats.sample_count, 'successes': a.profile.stats.successes,
                    'mean_duration_ms': a.profile.stats.mean_duration_ms, 'active': a.profile.capacity.active,
                    'capacity': a.profile.capacity.capacity} for a in assessments]}
            decisions.append(decision)
            ready.pop(task.task_id)
            if selected:
                active[selected] += 1
                capacity_violations += int(active[selected] > next(a['capacity'] for a in data['agents'] if a['id'] == selected)
                                           or sum(active.values()) > data['runtime_capacity'])
                peaks[selected] = max(peaks[selected], active[selected])
                peak_total = max(peak_total, sum(active.values()))
                success, duration = outcome(data, task.task_id, task.task_type, selected, now)
                busy_ms[selected] += duration
                finish = round(now + duration, 6)
            else:
                success, duration, finish = False, 0.0, now
            row = {'task_id':task.task_id,'kind':task.task_type,'agent_id':selected,'arrival_ms':arrival,
                'start_ms':now if selected else None,'finish_ms':finish,'service_ms':duration,
                'latency_ms':round(finish-arrival,6),'waiting_ms':round(now-arrival,6),
                'success':success,'state':'succeeded' if success else 'failed',
                'error_code':None if success else ('simulated_failure' if selected else 'scheduling_timeout')}
            if selected:
                heapq.heappush(running, (finish, task.task_id, row))
            else:
                rows.append(row)
        candidates = ([running[0][0]] if running else []) + ([tasks[cursor]['arrival_ms']] if cursor < len(tasks) else [])
        candidates += [x for x in boundaries if x > now]
        candidates += [(t.ready_at-EPOCH).total_seconds()*1000+data['timeout_ms'] for t in ready.values()]
        future = [x for x in candidates if x > now]
        if cursor < len(tasks) or ready or running:
            if not future:
                raise AssertionError('Simulator cannot make progress')
            now = min(future)
    rows.sort(key=lambda r:r['task_id'])
    measured = metrics(rows, data, decisions, peaks, peak_total, capacity_violations, busy_ms)
    deterministic = {'rows':rows,'decisions':decisions,'metrics':measured,'initial_stats_digest':initial_stats_digest,
                     'warmup_digest':digest(training),'wait_reasons':dict(waits)}
    return {'scenario':data['scenario'],'seed':data['seed'],'strategy':strategy,'workload_sha256':digest(data),
        **deterministic,'deterministic_sha256':digest(deterministic),
        'wall_clock':{'planning_total_ms':planning_ns/1e6,'planning_passes':planning_passes,
                      'mean_scheduling_pass_ms':planning_ns/1e6/planning_passes}}


def stats_as_list(stats):
    return [[aid,kind,value] for (aid,kind),value in sorted(stats.items())]


def metrics(rows, data, decisions, peaks, peak_total, violations, busy):
    n = len(rows)
    end = max(r['finish_ms'] for r in rows)
    fault = data['fault']
    post = [r for r in rows if fault and r['arrival_ms'] >= fault['recovered_at']]
    resumed = [r['finish_ms'] - fault['recovered_at'] for r in rows
        if fault and r['agent_id'] == fault['agent'] and r['start_ms'] >= fault['recovered_at'] and r['success']]
    assignments = {a['id']:sum(r['agent_id']==a['id'] for r in rows) for a in data['agents']}
    return {'tasks':n,'success_rate':sum(r['success'] for r in rows)/n,
        'mean_latency_ms':statistics.mean(r['latency_ms'] for r in rows),
        'p95_latency_ms':nearest_rank([r['latency_ms'] for r in rows]),
        'mean_waiting_ms':statistics.mean(r['waiting_ms'] for r in rows),
        'failure_rate':sum(not r['success'] for r in rows)/n,
        'throughput_per_second':n/(end/1000), 'makespan_ms':end,
        'assignment_counts':assignments,'peak_active':peaks,'peak_total_active':peak_total,
        'capacity_violations':violations,'busy_ms':busy,
        'post_fault_tasks':len(post),'post_fault_success_rate':sum(r['success'] for r in post)/len(post) if post else None,
        'fault_agent_recovery_delay_ms':min(resumed) if resumed else None,
        'unassigned_tasks':sum(r['agent_id'] is None for r in rows)}


def interval(values, key, samples=2000):
    values = [x for x in values if x is not None]
    if not values:
        return {'n':0,'mean':None,'sd':None,'ci95':None}
    rng = random.Random(int(digest(key)[:16],16))
    bootstrap = sorted(statistics.mean(rng.choices(values,k=len(values))) for _ in range(samples))
    return {'n':len(values),'mean':statistics.mean(values),'sd':statistics.stdev(values) if len(values)>1 else 0,
            'ci95':[bootstrap[int(.025*samples)],bootstrap[min(samples-1,int(.975*samples))]]}


def sign_p(differences):
    nonzero = [d for d in differences if abs(d)>1e-12]
    n = len(nonzero)
    wins = sum(d>0 for d in nonzero)
    return min(1.0,2*sum(math.comb(n,k) for k in range(min(wins,n-wins)+1))/2**n) if n else 1.0


def summarize(trials, bootstrap=2000):
    names = ('success_rate','mean_latency_ms','p95_latency_ms','mean_waiting_ms','throughput_per_second',
             'post_fault_success_rate','fault_agent_recovery_delay_ms')
    groups, comparisons = [], []
    for scenario in SCENARIOS:
        for policy in POLICIES:
            selected = [t for t in trials if t['scenario']==scenario and t['strategy']==policy]
            if not selected: continue
            group = {'scenario':scenario,'strategy':policy,'metrics':{m:interval([t['metrics'][m] for t in selected],
                [scenario,policy,m],bootstrap) for m in names},
                'scheduling_overhead_ms':interval([t['wall_clock']['mean_scheduling_pass_ms'] for t in selected],
                    [scenario,policy,'overhead'],bootstrap),
                'assignment_counts':dict(sum((Counter(t['metrics']['assignment_counts']) for t in selected),Counter())),
                'capacity_violations':sum(t['metrics']['capacity_violations'] for t in selected),
                'maximum_active_per_agent':{a:max(t['metrics']['peak_active'][a] for t in selected) for a in selected[0]['metrics']['peak_active']},
                'recovery_censored_seeds':sum(t['metrics']['fault_agent_recovery_delay_ms'] is None for t in selected)}
            groups.append(group)
        caas = {t['seed']:t for t in trials if t['scenario']==scenario and t['strategy']=='caas'}
        for baseline in POLICIES[:2]:
            other = {t['seed']:t for t in trials if t['scenario']==scenario and t['strategy']==baseline}
            for metric in PRIMARY:
                differences=[caas[s]['metrics'][metric]-other[s]['metrics'][metric] for s in sorted(caas)]
                comparisons.append({'scenario':scenario,'baseline':baseline,'metric':metric,
                    'caas_minus_baseline':interval(differences,[scenario,baseline,metric],bootstrap),
                    'non_tied_seeds':sum(abs(d)>1e-12 for d in differences),'p_exact_sign':sign_p(differences)})
    running = 0
    for rank,comparison in enumerate(sorted(comparisons,key=lambda c:c['p_exact_sign'])):
        running=max(running,min(1.,comparison['p_exact_sign']*(len(comparisons)-rank)))
        comparison['p_holm']=running
        comparison['significant_05']=running<.05
    return {'groups':groups,'paired_comparisons':comparisons,
        'method':'Seed is independent unit; sample SD; percentile paired/unpaired bootstrap mean CI; two-sided exact paired sign test; Holm across 16 primary comparisons. No significance test of wall-clock overhead.'}


def write_csv(path, rows):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def run(args):
    out=Path(args.output).resolve()
    if not out.is_relative_to(ROOT) or out.exists():
        raise ValueError('Output must be a NEW directory inside FutureCup; existing evidence is never overwritten')
    seeds=list(range(args.seed,args.seed+args.seeds))
    config={'mode':'SIMULATION ONLY; logical milliseconds, not production/LLM latency',
        'seeds':seeds,'tasks_per_trial':args.tasks,'warmup_per_agent_kind':4,
        'scenarios':list(SCENARIOS),'policies':list(POLICIES),'caas_config':asdict(CAASConfig()),
        'bootstrap_samples':2000,'execution':'production assess/effective_priority/selection; virtual resources, no Runtime/WorkflowEngine/SQLite',
        'primary_metrics':list(PRIMARY),'policy_order':'cyclic rotation per seed; overhead excluded from logical metrics'}
    out.mkdir(parents=True)
    (out/'configuration.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
    trials,workloads=[] ,[]
    for scenario in SCENARIOS:
        for index,seed in enumerate(seeds):
            data=workload(scenario,seed,args.tasks)
            workloads.append(data)
            group=[]
            for policy in POLICIES[index%3:]+POLICIES[:index%3]:
                t=trial(data,policy)
                # Repeat every seed/policy; preserve both wall-clock measurements.
                repeated=trial(data,policy)
                assert t['deterministic_sha256']==repeated['deterministic_sha256']
                t['repeat_check']={'deterministic_sha256':repeated['deterministic_sha256'],'wall_clock':repeated['wall_clock']}
                trials.append(t);group.append(t)
            for field in ('workload_sha256','initial_stats_digest','warmup_digest'):
                assert len({t[field] for t in group})==1
        print('Completed SIMULATION:',scenario,flush=True)
    summary=summarize(trials)
    sources=['scripts/bench_scheduler_10a.py','backend/src/futurecup/application/scheduling.py',
             'backend/src/futurecup/domain/scheduling.py','benchmarks/render_scheduler_10a.mjs']
    metadata={'git_base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'python':sys.version,'platform':platform.platform(),
        'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in sources},
        'production_algorithm_matches_frozen_commit':all(subprocess.check_output(['git','show','97f43b0709b0edf094424cd85cb176af75936cd1:'+n],cwd=ROOT)==(ROOT/n).read_bytes().replace(b'\r\n',b'\n') for n in sources[1:3]),
        'paired_inputs_verified':True,'all_trials_repeated_identically_except_wall_time':True}
    (out/'workloads.json').write_text(json.dumps(workloads,separators=(',',':'))+'\n',encoding='utf-8')
    (out/'raw-trials.json').write_text(json.dumps({'metadata':metadata,'trials':trials},separators=(',',':'))+'\n',encoding='utf-8')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    previous=ROOT/'.data/scheduler-05-results.json'
    if previous.is_file():
        prior=json.loads(previous.read_text('utf-8'))
        reference={'source':'.data/scheduler-05-results.json','source_sha256':hashlib.sha256(previous.read_bytes()).hexdigest(),
            'mode':prior['mode'],'seed':prior['seed'],'repeats':prior['repeats'],'tasks_per_trial':prior['tasks_per_trial'],
            'trial_count':len(prior['trials']),'summaries':prior['summaries'],
            'warning':'05 uses real asyncio/WorkflowEngine wall-clock timing; 10A uses virtual resources and logical time. Not a like-for-like speed comparison.'}
    else:
        reference={'available':False,'reason':'Previous raw local simulation file not present; consult docs/scheduler-05.md'}
    (out/'reference-05.json').write_text(json.dumps(reference,indent=2)+'\n',encoding='utf-8')
    flat=[{'scenario':t['scenario'],'seed':t['seed'],'strategy':t['strategy'],**r} for t in trials for r in t['rows']]
    write_csv(out/'raw-tasks.csv',flat)
    write_csv(out/'trial-metrics.csv',[{'scenario':t['scenario'],'seed':t['seed'],'strategy':t['strategy'],
        **{k:v for k,v in t['metrics'].items() if not isinstance(v,dict)},**t['wall_clock']} for t in trials])
    write_csv(out/'paired-comparisons.csv',[{k:v for k,v in c.items() if k!='caas_minus_baseline'} |
        {'mean_difference':c['caas_minus_baseline']['mean'],'ci95_low':c['caas_minus_baseline']['ci95'][0],
         'ci95_high':c['caas_minus_baseline']['ci95'][1]} for c in summary['paired_comparisons']])
    lines=['# 10-A SIMULATION ONLY','',summary['method'],'',
           '| Scenario | Policy | Success mean | SD | Mean latency ms | P95 ms | Scheduling pass ms | Capacity violations |',
           '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for g in summary['groups']:
        m=g['metrics'];lines.append(f"| {g['scenario']} | {g['strategy']} | {m['success_rate']['mean']:.4f} | {m['success_rate']['sd']:.4f} | {m['mean_latency_ms']['mean']:.3f} | {m['p95_latency_ms']['mean']:.3f} | {g['scheduling_overhead_ms']['mean']:.4f} | {g['capacity_violations']} |")
    lines += ['', 'See summary.json for all seed-level confidence intervals, loads, censored recovery and 16 Holm-adjusted tests.',
              'Raw failures, timeouts and every policy are retained. Virtual time is reproducible; measured host overhead is not bit-identical.']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    subprocess.run(['node',str(ROOT/'benchmarks/render_scheduler_10a.mjs'),str(out)],check=True,cwd=ROOT)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('Saved offline benchmark:',out,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=int,default=2026111201)
    parser.add_argument('--seeds',type=int,choices=range(2,101),default=20)
    parser.add_argument('--tasks',type=int,choices=range(1,513),default=64)
    parser.add_argument('--output',default='results/scheduler-10a')
    run(parser.parse_args())
