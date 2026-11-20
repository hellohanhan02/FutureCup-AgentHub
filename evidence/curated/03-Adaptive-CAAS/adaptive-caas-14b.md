# Adaptive CAAS / 14-B: design, interfaces and limits

## Opt-in integration

New strategy: `adaptive_caas`. Original `fixed`, `round_robin`, `caas` remain. Workflow/Runtime interfaces are preserved with optional fields. Workflow JSON `scheduling_strategy` selects the strategy; ordinary TaskPlanner still defaults to original CAAS. The professional editor adds a selectable Adaptive CAAS option; overview labels it separately from Round Robin. No new UI scope was added.

Example workflow node (use registered Mock agents in this clone):

```json
{
  "id":"adaptive-example","version":"1","name":"Adaptive example",
  "scheduling_strategy":"adaptive_caas",
  "nodes":[{"id":"work","kind":"task","config":{
    "candidate_agents":["reader-a","reader-b"],"task_type":"analysis",
    "required_skills":["analysis"],"no_candidate_policy":"wait",
    "schedule_timeout_seconds":10,
    "scheduling_features":{"complexity":0.8,"reliability_target":0.9,"latency_target_ms":100,"cost_budget":1}
  }}]
}
```

Features are caller-supplied generic numeric requirements, NOT inferred using paid LLMs or benchmark labels. Complexity/reliability are [0,1]; latency/cost positive finite <=1,000,000. Unknown properties, strings, booleans, NaN/infinity rejected before execution. Required skills/types/tools remain existing first-class gates.

`cost_budget` gates DECLARED nominal relative estimates for every strategy (unknown estimate is rejected); it is not the CNY task budget or an invoice guarantee. `latency_target_ms` and `reliability_target` guide preference, not guaranteed SLAs/probabilities. Actual execution limits and monetary protections remain the existing independent Runtime budgets.

## Dynamic weights

Factors: M match, R classified execution reliability, L free capacity fraction, C inverse normalized declared cost, D inverse predicted latency. Base b=(.30,.25,.20,.10,.15), default positive scales cost=1, latency=1000ms.

Let c=complexity, q=reliability preference, u=1000/(1000+latency_target_ms), v=1/(1+cost_budget). For PRESENT features only:

```
r_M = b_M + .30*c
r_R = b_R + .15*c + .40*q
r_L = b_L + .15*u
r_C = b_C + .30*v
r_D = b_D + .30*u
w_i = .05 + .75 * r_i / sum(r)
score = sum(w_i * component_i)
```

Missing feature terms are zero. No features -> original base weights. A custom base with zero factors receives the same positive-floor projection. Weights sum to one, each factor stays positive (default floor .05, maximum .80 even for extreme bases). Configuration and reasons are saved, not just a final score. Higher complexity favors match/reliability; tight latency favors latency/load; tighter estimate budget increases cost preference. This is transparent engineering policy, not learned optimal weights. Final evaluation did NOT demonstrate a general independent benefit from dynamic weighting.

## Classified history, cold start and bounded health

- Observe the actual terminal Runtime report. Record enum category only, no raw prompt/error body: success, agent_failure, tool_failure, timeout, unavailable, cancelled, validation_failure, unknown.
- Only explicitly attributed execution failures (`agent_execution_failed` / synthetic equivalent) count as Agent failures. Provider response-format/protocol errors default to unknown. Safe HTTP rate/server/model-unavailable categories indicate operational unavailability; auth/balance/input faults are not silently blamed on Agent skill.
- Cancelled, tool/environment/timeout/unknown/validator failures are neutral to default ability reliability. A successful Runtime generation remains execution success; later validator failure does not prove Agent incompetence. This engine does NOT claim a measure of verified business quality.
- Existing cumulative legacy counters are unchanged. New categorized observations persist in the same append-only observation table; an expression index supplies last 32 observations per Agent version / task type / observed-real vs observed-Mock vs simulation source. Old uncategorized rows are unknown, not fabricated successes/failures. No historical row is rewritten.
- Weight each attributed sample with d=2^(-age_seconds/60). Ignore future timestamps. N=sum(d), S=sum(d for successes); R=(2.5+S)/(5+N). Low-sample flag N<5; cold start R=.5. This recency-weighted shrinkage is a heuristic estimate, not a calibrated confidence probability.
- Predicted latency: weighted attributed mean blended with declaration by N/(N+5). D=1/(1+predicted/target) if a latency target exists, otherwise original 1000ms scale; unknown .5. Bounds and future rejection prevent post-outcome selection leakage.
- If at least two failure/timeout/unavailability events occur since the last success in the last-32 window, and the latest failure is <1 second old, temporarily exclude with `adaptive_cooldown`. Neutral errors neither extend nor create the streak. Successful execution clears it; cooldown expires by time without requiring a successful probe first. An ancient earlier failure can contribute to the streak; the most recent failure must still be fresh. This deliberately simple rule can overreact to independent random failures.
- Temporary cooldown uses the existing bounded pending loop; capacity-only/cooldown waits stop at the scheduling deadline. No extra capacity reservation is created. The existing atomic Runtime lock remains the only owner of actual capacity. No unlimited retries/probes or paid calls occur.

Task queue aging is unchanged for all policies. With bounded priorities it can eventually overtake fresh arrivals when capacity is feasible; it cannot meet impossible deadlines or guarantee progress under unbounded overload. No task is deprioritized simply because its predicted execution is long.

## Explainability and storage

Each candidate keeps scores, exclusion reasons, actual capacity, capability/profile version and an `adaptation` object: numeric features, weights, config/revision, weighted sample count, low-sample flag, safe category counts, predicted latency and cooldown remaining. Decision revision `adaptive-caas-v1`; deterministic ID/version tie break. No task input, raw error or private prompt is added to scheduling records. Old candidates have `adaptation:null`.

- `GET /api/v1/scheduling/strategies` preserves the legacy three-item `strategies` field; `available_strategies` advertises all four.
- `GET /api/v1/scheduling/strategies?include_adaptive=true` returns all four in `strategies`.
- Existing workflow create/execute/query and `GET /api/v1/scheduling/decisions?run_id=...` expose the new strategy.
- Legacy Agent profile API still shows cumulative CAAS estimates; adaptive per-decision estimates are in `adaptation` and candidate components. These must not be conflated.

## Reproduction / startup

Only in `D:/competition/FutureCup-v1.1`, Python 3.11.9 project venv:

```powershell
.\.venv\Scripts\python.exe scripts/bench_caas_14b.py --split development --seeds 5 --tasks 64 --output results/caas-14b-dev-repeat
.\.venv\Scripts\python.exe scripts/bench_caas_14b.py --split evaluation --seeds 20 --tasks 64 --output results/caas-14b-eval-repeat
node benchmarks/render_caas_14b.mjs results/caas-14b-eval-repeat results/caas-14b-figures-repeat
```

Output directories must be NEW; the runner refuses overwrite or paths outside the clone. Existing `scripts/Start-V11Mock.ps1` still starts only new ports 18024 / 15194 with independent `.data/v1.1` databases, real calls disabled and known credential variables removed in its child process. Use the professional editor `/#/workflows`, choose Adaptive CAAS, and enter optional feature config via JSON. New data need their own registered Mock Agents; no original private audit is copied. Original services are not touched.

## Known limits and next-stage suggestions

No distributed capacity leasing, real model/production benchmark, cost invoice guarantee, semantic task-feature extraction, automatically discovered quality labels or general optimality proof. The 1-second circuit can hurt latency and recovery; weights and recent decay add CPU/query overhead. Last-32 sampling can forget useful history; old unlabeled observations intentionally lose attribution. No arbitrary code execution or new tool permission is introduced.

Next Self-Loop stage should add a SEPARATE, versioned verified-quality observation channel, with validator ERROR/INCONCLUSIVE excluded from blame; correlate attempt IDs and repair actions without counting duplicate observations. Distinguish operational repair from model correction, preserve shared call/cost limits, and compare first-attempt vs final verified success transparently. No next-stage implementation was performed here.
