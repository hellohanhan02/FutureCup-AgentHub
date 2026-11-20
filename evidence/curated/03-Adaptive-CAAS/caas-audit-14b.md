# 14-B: original CAAS audit (before changes)

All development is in `D:/competition/FutureCup-v1.1`, starting at 14-A commit `8bd2caf8c0d6c5f860287849a19d70412024562b`. The worktree was clean; branch `feature/v1.1-caas` was created without cloning or losing RAG. Baseline: 696 backend tests passed. No operation targets the frozen original project.

## Actual production behavior

Source: `application/scheduling.py`, `domain/scheduling.py`, `adapters/scheduling_store.py`, `application/runtime.py`.

- Eligibility: dependency readiness, enabled/available Agent, Provider/tool registration availability, task type or wildcard, required skills/tool versions/permission subset, Agent and Runtime concurrency capacities. These are hard gates, not scoring preferences.
- Match M = .5 * (1 for exact task type, .5 for wildcard) + .5 * preferred-skill fraction (default .5 without preferences).
- Reliability R = (successes + 2.5)/(successes + failures + 5). Zero samples gives .5, never a claimed high empirical rate. All non-cancelled execution failures previously enter the same cumulative failure count. There is no recent-fault window. Execution success is distinct from independently verified correctness.
- Load L = max(0, 1-active/capacity); actual runtime/global capacity is additionally a hard gate.
- Cost C = 1/(1+declared_estimate/1), unknown .5. Relative declared units, not precise money.
- Predicted latency blends cumulative mean and declaration with confidence n/(n+5); unknown fallback scale 1000ms. D = 1/(1+predicted_ms/1000), or .5 for unknown.
- Score = .30 M + .25 R + .20 L + .10 C + .15 D. Actual defaults match the historical record.
- Task queue priority = configured priority + .1*min(64, dependents) + 5*waiting_seconds. Aging is unbounded, compensating bounded base priorities. There is no shortest-job-first rule. Admission can still time out in an overloaded or infeasible workload; this is not an absolute starvation-free guarantee.
- Fixed chooses the lowest eligible ID; RR advances through the current eligible set; CAAS maximizes score and breaks ties by ID/version. Baselines have full capacity/fallback access, not artificially restricted to one Agent.
- Coordinator locks pending/assignment state. Runtime owns the atomic admission lock and execution-task idempotency claim, checks capacity again, increments actual occupancy, and decrements via completion callback on success/failure/cancellation. Changed claims retry; failed audit persistence cancels the newly submitted execution.
- State is single-process/event-loop. This is not a distributed lease, multi-process capacity protocol or crash-proof execution persistence guarantee.

## Pre-change experiment

Actual original 10-A was rerun BEFORE functional edits: 20 seeds `2026111201..2026111220`, 64 tasks, four workloads, three policies; 240 primary trials plus deterministic repeats. Saved separately at `results/caas-14b/baseline-10a/`. Core source matched the original frozen comparison at the time of measurement.

All seed-level logical metrics reproduce the saved 10-A results exactly; host scheduling overhead naturally changes. Existing 10-A data and 05 data were not modified. 10-A's 16 Holm-adjusted tests still show no significant CAAS advantage. Stage 05's overall CAAS success 79.56% vs Fixed/RR 79.96% remains an unfavorable historical result; it is a different full-engine wall-clock simulation and must not be divided into current logical-time metrics to advertise improvement.

## Safe optimization boundary

The old `assess`, `effective_priority`, `FixedSelection`, `RoundRobinSelection`, `CAASSelection` ASTs remain identical to 14-A (automatically tested). Legacy definitions with no new features preserve behavior. New declared-cost constraints are shared by ALL four policies only when explicitly supplied. New outcome categories do not change the old cumulative statistics formula. No core Runtime, CAAS weights, RAG, Self-Loop or paid Provider code is replaced.
