"""Pure, explainable Adaptive CAAS. No payload, Provider, storage or RNG access."""
from dataclasses import asdict, replace
from datetime import datetime

from futurecup.domain.scheduling import AdaptiveConfig, CAASConfig, CandidateAssessment, SchedulingTask

FACTORS = ('match', 'reliability', 'load', 'cost', 'latency')
CATEGORIES = {'success','agent_failure','tool_failure','timeout','unavailable','cancelled','validation_failure','unknown'}


def classify(state: str, code: str | None, success: bool, http_category: str | None = None) -> str:
    if state == 'cancelled': return 'cancelled'
    if success: return 'success'
    if state == 'succeeded': return 'validation_failure'
    if state == 'timed_out' or code in ('model_timeout','provider_timeout'): return 'timeout'
    if code and code.startswith('tool_'): return 'tool_failure'
    if code in ('agent_execution_failed','simulated_failure'): return 'agent_failure'
    if http_category in ('rate_limited','server_error','model_unavailable'): return 'unavailable'
    if code in ('provider_connection_failed','agent_unavailable','provider_unavailable','provider_network_error','provider_rate_limited','provider_server_error'): return 'unavailable'
    return 'unknown'


def weights(task: SchedulingTask, config: CAASConfig, options: AdaptiveConfig) -> dict[str, float]:
    base = {k: getattr(config, k+'_weight') for k in FACTORS}
    f = task.features
    if not options.dynamic_weights or f is None or all(v is None for v in asdict(f).values()):
        if min(base.values()) >= options.weight_floor: return base
        return {k:options.weight_floor+(1-5*options.weight_floor)*v for k,v in base.items()}
    raw = dict(base)
    if f.complexity is not None:
        raw['match'] += .3*f.complexity
        raw['reliability'] += .15*f.complexity
    if f.reliability_target is not None: raw['reliability'] += .4*f.reliability_target
    if f.latency_target_ms is not None:
        urgency = config.latency_scale_ms/(config.latency_scale_ms+f.latency_target_ms)
        raw['latency'] += .3*urgency
        raw['load'] += .15*urgency
    if f.cost_budget is not None: raw['cost'] += .3*config.cost_scale/(config.cost_scale+f.cost_budget)
    total = sum(raw.values())
    return {k: options.weight_floor+(1-5*options.weight_floor)*v/total for k,v in raw.items()}


def constrain(task: SchedulingTask, candidate: CandidateAssessment) -> CandidateAssessment:
    # NEW estimate-based constraint shared by ALL strategies. Old featureless tasks unchanged.
    budget = task.features.cost_budget if task.features else None
    if candidate.eligible and budget is not None:
        cost = candidate.profile.estimated_cost
        if cost is None or cost > budget:
            return replace(candidate, eligible=False, reasons=('task_cost_unknown' if cost is None else 'task_cost_budget',), score=None)
    return candidate


def adapt(task: SchedulingTask, candidate: CandidateAssessment, config: CAASConfig,
          history: tuple[dict, ...], options: AdaptiveConfig) -> CandidateAssessment:
    w = weights(task, config, options)
    explanation = {'revision':options.revision,'features':asdict(task.features) if task.features else {},
        'weights':w,'options':asdict(options),'reason':'highest_adaptive_score_then_id',
        'reliability_scope':'classified_execution_outcomes_not_verified_quality'}
    if not candidate.eligible: return replace(candidate, adaptation=explanation)
    profile = candidate.profile
    recent = []
    for item in history[-32:]:
        when = datetime.fromisoformat(item['occurred_at']) if isinstance(item['occurred_at'], str) else item['occurred_at']
        age = (profile.observed_at-when).total_seconds()
        if age < 0: continue  # Never use future evidence.
        category = item.get('outcome_category','unknown')
        if category not in CATEGORIES: category = 'unknown'
        recent.append((item, age, category))
    quality = [(item, 2.0**(-age/options.recency_seconds) if options.recency else 1.)
        for item,age,category in recent
        if category in ('success','agent_failure') or not options.attribution and category != 'cancelled']
    n = sum(weight for item,weight in quality)
    succeeded = sum(weight for item,weight in quality if item['succeeded'])
    components = dict(candidate.components)
    components['reliability'] = (config.prior_samples*.5+succeeded)/(config.prior_samples+n)
    if quality and n > 0:
        duration = sum(item['duration_ms']*weight for item,weight in quality)/n
        confidence = n/(n+config.prior_samples)
        predicted = confidence*duration+(1-confidence)*(profile.declared_latency_ms if profile.declared_latency_ms is not None else config.latency_scale_ms)
    else: predicted = profile.declared_latency_ms
    scale = task.features.latency_target_ms if task.features and task.features.latency_target_ms else config.latency_scale_ms
    components['latency'] = 1/(1+predicted/scale) if predicted is not None else .5
    # A short time-bounded circuit is not a permanent capacity reservation.
    streak = 0
    last_failure_age = None
    for item,age,category in reversed(recent):
        if category == 'success': break
        if category in ('agent_failure','timeout','unavailable'):
            streak += 1
            if last_failure_age is None: last_failure_age = age
    cooldown = (options.health and streak >= options.failure_threshold and last_failure_age is not None
                and last_failure_age < options.cooldown_seconds)
    explanation.update(effective_samples=n, low_samples=n<config.prior_samples,
        recent_category_counts={c:sum(category==c for _,_,category in recent) for c in sorted(CATEGORIES)},
        cooldown_remaining_seconds=max(0,options.cooldown_seconds-last_failure_age) if cooldown else 0,
        predicted_latency_ms=predicted)
    score = sum(components[k]*w[k] for k in FACTORS)
    return replace(candidate, eligible=not cooldown, reasons=('adaptive_cooldown',) if cooldown else (),
                   components=components, score=None if cooldown else score, adaptation=explanation)
