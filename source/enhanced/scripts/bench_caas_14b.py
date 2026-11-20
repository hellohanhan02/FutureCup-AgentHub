"""14-B offline SIMULATION using production kernels, derived from 10-A event driver.
No LLM, environment credentials, Runtime or historical SQLite are opened.
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, random, statistics, subprocess, sys
from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from time import perf_counter_ns
import heapq
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import bench_scheduler_10a as b
from futurecup.application.scheduling import evaluate_candidate, effective_priority, CAASSelection
from futurecup.domain.scheduling import CAASConfig, AdaptiveConfig, TaskFeatures, SchedulingTask, CapabilityProfile, CapacitySnapshot, PerformanceStats
from futurecup.domain.models import VersionRef
EPOCH,KINDS=b.EPOCH,b.KINDS
outcome,update,metrics,stats_as_list,digest=b.outcome,b.update,b.metrics,b.stats_as_list,b.digest
SCENARIOS=(*b.SCENARIOS,'E_mixed_types','F_cost_latency','G_cold_start','H_long_recovery')
VARIANTS={p:(p,AdaptiveConfig()) for p in ('fixed','round_robin','caas','adaptive_caas')}
VARIANTS.update({'no_weights':('adaptive_caas',AdaptiveConfig(dynamic_weights=False)),
 'no_recency':('adaptive_caas',AdaptiveConfig(recency=False)),
 'no_health':('adaptive_caas',AdaptiveConfig(health=False)),
 'no_attribution':('adaptive_caas',AdaptiveConfig(attribution=False))})


def workload(scenario,seed,count=64):
    data=b.workload(scenario if scenario in b.SCENARIOS else 'A_uniform',seed,count,0 if scenario=='G_cold_start' else 4)
    data['scenario']=scenario
    rng=random.Random(seed+701)
    if scenario in ('E_mixed_types','G_cold_start'):
        specialty=list(KINDS);rng.shuffle(specialty)
        for a,kind in zip(data['agents'],specialty):
            if scenario=='E_mixed_types':a['skills']=[kind]
            for k in KINDS:data['latent'][a['id']][k]={'success':.95 if k==kind else .7,'duration_ms':35 if k==kind else 75}
    if scenario=='F_cost_latency':
        variants=[(.5,90),(1.,50),(2.,30)];rng.shuffle(variants)
        for a,(cost,latency) in zip(data['agents'],variants):
            a.update(declared_cost=cost,declared_latency_ms=latency)
            for k in KINDS:data['latent'][a['id']][k]['duration_ms']=latency
    if scenario in ('E_mixed_types','F_cost_latency'):
        for task in data['tasks']:
            task['features']={'complexity':rng.choice([.2,.6,1.]),'reliability_target':rng.choice([.6,.9]),
                              'latency_target_ms':rng.choice([40,100,200])}
            if scenario=='F_cost_latency':task['features']['cost_budget']=rng.choice([.75,1.5,3.])
    if scenario=='H_long_recovery':
        data=b.workload('A_uniform',seed,min(512,count*3));data['scenario']=scenario
        data['fault']={'agent':rng.choice(data['agents'])['id'],'degrade_start':800,'unavailable_start':1000,
                       'unavailable_end':1500,'recovered_at':1800}
        data['timeout_ms']=10000
        for i,t in enumerate(data['tasks']):t['arrival_ms']=i*35;t['priority']=20 if t['kind']=='review' else 80
        for a in data['agents']:data['latent'][a['id']]['review']['duration_ms']=250
    return data


def category(data,tid,aid,now,success):
    if not aid:return 'unknown'
    if success:return 'success'
    f=data['fault']
    if f and aid==f['agent'] and f['degrade_start']<=now<f['recovered_at']:return 'unavailable'
    # Observable synthetic error types, shared across every strategy.
    u=b.draw(data['seed'],tid,aid,'error_type')
    return 'tool_failure' if u<.2 else 'timeout' if u<.35 else 'agent_failure'


def history_item(success,duration,when,kind):
    return {'succeeded':success,'duration_ms':duration,'occurred_at':when.isoformat(),'outcome_category':kind}


def trial(data, strategy, options=None):
    config, policy = CAASConfig(), (CAASSelection() if strategy=='adaptive_caas' else b.FACTORIES[strategy]())
    options=options or AdaptiveConfig()
    histories={(a['id'],k):[] for a in data['agents'] for k in KINDS}
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
                histories[a['id'],kind].append(history_item(success,duration,EPOCH-timedelta(milliseconds=1),'success' if success else 'agent_failure'))
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
            histories[row['agent_id'],row['kind']].append(history_item(row['success'],row['service_ms'],EPOCH+timedelta(milliseconds=finish),row['category']))
            rows.append(row)
        while cursor < len(tasks) and tasks[cursor]['arrival_ms'] <= now:
            t = tasks[cursor]
            ready[t['id']] = SchedulingTask(t['id'], 'SIMULATION10A', t['id'], tuple(r.id for r in refs), refs,
                EPOCH + timedelta(milliseconds=t['arrival_ms']), task_type=t['kind'],
                preferred_skills=(t['kind'],), priority=t['priority'], strategy=strategy,
                no_candidate_policy='wait', timeout_seconds=data['timeout_ms'] / 1000,
                features=TaskFeatures(**t['features']) if 'features' in t else None)
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
                assessments.append(evaluate_candidate(task, profile, config, tuple(histories[aid,task.task_type][-32:]), options))
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
                                     'caas':'maximum_production_caas_score_then_id','adaptive_caas':'maximum_production_adaptive_score_then_id'}[strategy] if selected else 'scheduling_timeout',
                'features':asdict(task.features) if task.features else {},
                'weights':next((a.adaptation['weights'] for a in assessments if a.adaptation),None),
                'candidates': [{'agent_id': a.agent_id, 'eligible': a.eligible, 'reasons': a.reasons,
                    'components': a.components, 'score': a.score, 'profile_version': a.profile.profile_version,
                    'sample_count': a.profile.stats.sample_count, 'successes': a.profile.stats.successes,
                    'mean_duration_ms': a.profile.stats.mean_duration_ms, 'active': a.profile.capacity.active,
                    'capacity': a.profile.capacity.capacity,
                    'adaptive_history':{k:a.adaptation[k] for k in ('effective_samples','cooldown_remaining_seconds','recent_category_counts') if k in a.adaptation} if a.adaptation else None} for a in assessments]}
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
                'error_code':None if success else ('simulated_failure' if selected else 'scheduling_timeout'),
                'category':category(data,task.task_id,selected,now,success),
                'simulated_cost':next((a['declared_cost'] for a in data['agents'] if a['id']==selected),0)*duration/50,
                'latency_target_ms':task.features.latency_target_ms if task.features else None}
            if selected:
                heapq.heappush(running, (finish, task.task_id, row))
            else:
                rows.append(row)
        candidates = ([running[0][0]] if running else []) + ([tasks[cursor]['arrival_ms']] if cursor < len(tasks) else [])
        candidates += [x for x in boundaries if x > now]
        if strategy=='adaptive_caas' and options.health:
            candidates += [now+100] if ready else []  # Bounded virtual recheck, like the real pump.
        candidates += [(t.ready_at-EPOCH).total_seconds()*1000+data['timeout_ms'] for t in ready.values()]
        future = [x for x in candidates if x > now]
        if cursor < len(tasks) or ready or running:
            if not future:
                raise AssertionError('Simulator cannot make progress')
            now = min(future)
    rows.sort(key=lambda r:r['task_id'])
    measured = metrics(rows, data, decisions, peaks, peak_total, capacity_violations, busy_ms)
    measured.update(total_simulated_cost=sum(r['simulated_cost'] for r in rows), max_waiting_ms=max(r['waiting_ms'] for r in rows),
        starvation_count=sum(r['waiting_ms']>=1000 for r in rows),remaining_capacity_claims=sum(active.values()),
        latency_target_violations=sum(r['latency_target_ms'] is not None and r['latency_ms']>r['latency_target_ms'] for r in rows),
        by_task_type={kind:{'tasks':len([r for r in rows if r['kind']==kind]),'success_rate':statistics.mean(r['success'] for r in rows if r['kind']==kind),
        'mean_latency_ms':statistics.mean(r['latency_ms'] for r in rows if r['kind']==kind)} for kind in KINDS if any(r['kind']==kind for r in rows)})
    deterministic = {'rows':rows,'decisions':decisions,'metrics':measured,'initial_stats_digest':initial_stats_digest,
                     'warmup_digest':digest(training),'wait_reasons':dict(waits)}
    return {'scenario':data['scenario'],'seed':data['seed'],'strategy':strategy,'workload_sha256':digest(data),
        **deterministic,'deterministic_sha256':digest(deterministic),
        'wall_clock':{'planning_total_ms':planning_ns/1e6,'planning_passes':planning_passes,
                      'mean_scheduling_pass_ms':planning_ns/1e6/planning_passes}}


METRICS=('success_rate','mean_latency_ms','p95_latency_ms','total_simulated_cost','max_waiting_ms',
         'starvation_count','latency_target_violations','mean_waiting_ms','post_fault_success_rate','fault_agent_recovery_delay_ms')


def summarize(trials):
    groups=[]; comparisons=[]
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            selected=[t for t in trials if t['scenario']==scenario and t['variant']==variant]
            if not selected:continue
            groups.append({'scenario':scenario,'variant':variant,
                'metrics':{m:b.interval([t['metrics'][m] for t in selected],[scenario,variant,m],500) for m in METRICS},
                'overhead_ms':b.interval([t['wall_clock']['mean_scheduling_pass_ms'] for t in selected],[scenario,variant,'overhead'],500),
                'capacity_violations':sum(t['metrics']['capacity_violations'] for t in selected),
                'remaining_claims':sum(t['metrics']['remaining_capacity_claims'] for t in selected),
                'by_type':{k:{m:b.interval([t['metrics']['by_task_type'][k][m] for t in selected if k in t['metrics']['by_task_type']],
                    [scenario,variant,k,m],500) for m in ('success_rate','mean_latency_ms')} for k in KINDS}})
        for baseline in ('fixed','round_robin','caas'):
            for metric in ('success_rate','mean_latency_ms'):
                a={t['seed']:t['metrics'][metric] for t in trials if t['scenario']==scenario and t['variant']=='adaptive_caas'}
                c={t['seed']:t['metrics'][metric] for t in trials if t['scenario']==scenario and t['variant']==baseline}
                differences=[a[seed]-c[seed] for seed in sorted(a)]
                comparisons.append({'scenario':scenario,'baseline':baseline,'metric':metric,
                    'adaptive_minus_baseline':b.interval(differences,[scenario,baseline,metric],1000),
                    'raw_p':b.sign_p(differences),'non_tied_seeds':sum(abs(d)>1e-12 for d in differences)})
    running=0.
    for i,c in enumerate(sorted(comparisons,key=lambda c:c['raw_p'])):
        running=max(running,min(1.,(len(comparisons)-i)*c['raw_p']))
        c['holm_p']=running;c['significant_at_05']=running<.05
    return {'groups':groups,'paired_comparisons':comparisons,
        'method':'Seed-level paired comparisons. 500 percentile bootstrap draws for means/SD/unadjusted 95% intervals; 1000 for paired effect intervals. Exact two-sided sign tests, ties removed, Holm over all 48 predeclared success/mean-latency comparisons. No task-level pseudo-replication.'}


def write_report(summary,out):
    lines=['# 14-B offline SIMULATION ONLY','', 'Logical milliseconds and artificial relative cost, NOT LLM time or CNY. No real calls.',
           '',summary['method'],'', '| Scenario | Strategy | Success | Mean ms | P95 ms | Sim cost | Max wait ms | Capacity violations | Overhead ms/pass |',
           '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for g in summary['groups']:
        if g['variant'] not in ('fixed','round_robin','caas','adaptive_caas'):continue
        m=g['metrics'];lines.append(f"| {g['scenario']} | {g['variant']} | {m['success_rate']['mean']:.4f} | {m['mean_latency_ms']['mean']:.3f} | {m['p95_latency_ms']['mean']:.3f} | {m['total_simulated_cost']['mean']:.3f} | {m['max_waiting_ms']['mean']:.3f} | {g['capacity_violations']} | {g['overhead_ms']['mean']:.5f} |")
    lines+=['','## Ablations (all seeds/scenarios retained)','',
            '| Scenario | Variant | Success | Mean ms | Cost | Starvation count |','|---|---|---:|---:|---:|---:|']
    for g in summary['groups']:
        if g['variant'] in ('fixed','round_robin','caas'):continue
        m=g['metrics'];lines.append(f"| {g['scenario']} | {g['variant']} | {m['success_rate']['mean']:.4f} | {m['mean_latency_ms']['mean']:.3f} | {m['total_simulated_cost']['mean']:.3f} | {m['starvation_count']['mean']:.3f} |")
    significant=[c for c in summary['paired_comparisons'] if c['significant_at_05']]
    lines+=['',f'{len(significant)} of 48 Holm-adjusted comparisons significant; direction/effect must be read separately (significant does NOT always mean better).',
            '', 'All outcomes, timeouts, costs, per-type metrics and waits retained in raw files. Max wait >=1000 logical ms is the predeclared starvation indicator, not a proof of infinite starvation.',
            'Cold-start priors do not provide hidden ground truth. No assertion of production benefit or real model quality.']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Static all-scenario plot, all four main policies, no extra dependency.
    colors=['#8a9c98','#527f91','#63895b','#138368'];parts=[]
    for i,scenario in enumerate(SCENARIOS):
        y=70+i*95;parts.append(f'<text x="15" y="{y}">{scenario}</text>')
        for j,variant in enumerate(('fixed','round_robin','caas','adaptive_caas')):
            g=next(g for g in summary['groups'] if g['scenario']==scenario and g['variant']==variant)
            rate=g['metrics']['success_rate']['mean'];parts.append(f'<rect x="210" y="{y-15+j*17}" width="{rate*480:.2f}" height="12" fill="{colors[j]}"/><text x="710" y="{y-5+j*17}">{variant}: {rate:.1%}</text>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="900"><rect width="1040" height="900" fill="white"/><g font-family="sans-serif" font-size="12" fill="#173e36"><text x="15" y="25">Offline SIMULATION success rate; all scenarios/policies (see CSV/JSON for intervals)</text>'+''.join(parts)+'</g></svg>'
    (out/'success.svg').write_text(svg,encoding='utf-8')


def run(args):
    out=Path(args.output).resolve()
    if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Use a NEW directory in this clone; never overwrite evidence')
    seed_start=11 if args.split=='development' else 50001
    if not 2<=args.seeds<=30 or not 16<=args.tasks<=128:raise ValueError('Bounded seed/task counts required')
    seeds=list(range(seed_start,seed_start+args.seeds))
    config={'split':args.split,'seeds':seeds,'tasks':args.tasks,'scenarios':SCENARIOS,
        'variants':{k:asdict(v[1]) for k,v in VARIANTS.items()},'caas':asdict(CAASConfig()),
        'tuning':'No parameter search; development and final seed sets disjoint. Constants fixed before final evaluation.',
        'starvation_threshold_logical_ms':1000,'primary':['success_rate','mean_latency_ms'],
        'cost':'Artificial relative rate * service_ms/50, NOT currency. Task budget constrains declared nominal cost, not real invoices.'}
    out.mkdir(parents=True);(out/'configuration.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    trials=[];workloads=[]
    with gzip.open(out/'raw-trials.jsonl.gz','wt',encoding='utf-8') as raw, (out/'raw-tasks.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=None
        for scenario in SCENARIOS:
            for index,seed in enumerate(seeds):
                data=workload(scenario,seed,args.tasks);workloads.append(data)
                variants=list(VARIANTS);variants=variants[index%8:]+variants[:index%8]
                group=[]
                for variant in variants:
                    policy,options=VARIANTS[variant]
                    t=trial(data,policy,options);t['variant']=variant
                    again=trial(data,policy,options)
                    assert t['deterministic_sha256']==again['deterministic_sha256']
                    t['repeat_check']={'sha256':again['deterministic_sha256'],'wall_clock':again['wall_clock']}
                    raw.write(json.dumps(t,separators=(',',':'))+'\n')
                    for row in t['rows']:
                        row={'scenario':scenario,'seed':seed,'variant':variant,**row}
                        if writer is None:writer=csv.DictWriter(stream,list(row));writer.writeheader()
                        writer.writerow(row)
                    compact={k:v for k,v in t.items() if k not in ('rows','decisions')};trials.append(compact);group.append(compact)
                for field in ('workload_sha256','initial_stats_digest','warmup_digest'):
                    assert len({t[field] for t in group})==1
            print('Completed SIMULATION',args.split,scenario,flush=True)
    (out/'workloads.json').write_text(json.dumps(workloads,separators=(',',':')),encoding='utf-8')
    (out/'trial-metrics.json').write_text(json.dumps(trials,indent=2),encoding='utf-8')
    summary=summarize(trials);(out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    write_report(summary,out)
    names=['backend/src/futurecup/application/scheduling.py','backend/src/futurecup/application/adaptive.py','backend/src/futurecup/domain/scheduling.py','scripts/bench_caas_14b.py','scripts/bench_scheduler_10a.py']
    meta={'base_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'python':sys.version,
        'source_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names},'paired_inputs_verified':True,
        'deterministic_repeat_verified':True,'trials':len(trials),'evaluated_tasks':sum(t['metrics']['tasks'] for t in trials),
        'scope':'Production decision kernels + virtual resources. Does not run async Coordinator/Runtime; separate Mock tests cover them.'}
    (out/'metadata.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()},indent=2),encoding='utf-8')
    print(json.dumps(meta),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split',choices=['development','evaluation'],default='evaluation')
    parser.add_argument('--seeds',type=int,default=20);parser.add_argument('--tasks',type=int,default=64)
    parser.add_argument('--output',default='results/caas-14b/evaluation')
    run(parser.parse_args())
