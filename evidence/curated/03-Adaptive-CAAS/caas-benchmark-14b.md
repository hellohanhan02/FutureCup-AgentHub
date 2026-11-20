# 14-B offline SIMULATION ONLY

Logical milliseconds and artificial relative cost, NOT LLM time or CNY. No real calls.

Seed-level paired comparisons. 500 percentile bootstrap draws for means/SD/unadjusted 95% intervals; 1000 for paired effect intervals. Exact two-sided sign tests, ties removed, Holm over all 48 predeclared success/mean-latency comparisons. No task-level pseudo-replication.

| Scenario | Strategy | Success | Mean ms | P95 ms | Sim cost | Max wait ms | Capacity violations | Overhead ms/pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A_uniform | fixed | 0.9375 | 49.876 | 58.595 | 63.842 | 0.000 | 0 | 0.01969 |
| A_uniform | round_robin | 0.9398 | 50.030 | 58.775 | 64.038 | 0.000 | 0 | 0.02063 |
| A_uniform | caas | 0.9320 | 49.858 | 58.775 | 63.818 | 0.000 | 0 | 0.01961 |
| A_uniform | adaptive_caas | 0.9352 | 49.887 | 58.815 | 63.705 | 3.316 | 0 | 0.06556 |
| B_heterogeneous | fixed | 0.7562 | 65.343 | 92.172 | 75.040 | 26.608 | 0 | 0.01806 |
| B_heterogeneous | round_robin | 0.7625 | 64.164 | 91.069 | 74.197 | 26.280 | 0 | 0.01909 |
| B_heterogeneous | caas | 0.7937 | 59.277 | 88.099 | 70.624 | 21.563 | 0 | 0.01999 |
| B_heterogeneous | adaptive_caas | 0.8352 | 52.257 | 84.615 | 62.706 | 31.673 | 0 | 0.07048 |
| C_dynamic_fault | fixed | 0.8812 | 58.844 | 92.101 | 68.025 | 29.206 | 0 | 0.02057 |
| C_dynamic_fault | round_robin | 0.8945 | 58.276 | 90.841 | 67.949 | 27.392 | 0 | 0.02038 |
| C_dynamic_fault | caas | 0.8914 | 58.782 | 91.914 | 68.026 | 27.776 | 0 | 0.02404 |
| C_dynamic_fault | adaptive_caas | 0.8930 | 60.983 | 95.370 | 68.013 | 36.071 | 0 | 0.06121 |
| D_bursty | fixed | 0.9398 | 197.802 | 491.664 | 63.947 | 511.495 | 0 | 0.01581 |
| D_bursty | round_robin | 0.9391 | 201.450 | 497.658 | 64.285 | 518.219 | 0 | 0.01585 |
| D_bursty | caas | 0.9375 | 197.657 | 493.591 | 63.960 | 508.295 | 0 | 0.01544 |
| D_bursty | adaptive_caas | 0.9352 | 197.439 | 492.349 | 63.980 | 508.314 | 0 | 0.04731 |
| E_mixed_types | fixed | 0.7930 | 76.411 | 113.341 | 78.830 | 43.463 | 0 | 0.01889 |
| E_mixed_types | round_robin | 0.7875 | 72.556 | 105.340 | 77.866 | 37.093 | 0 | 0.01872 |
| E_mixed_types | caas | 0.8227 | 64.225 | 102.193 | 71.394 | 34.988 | 0 | 0.02092 |
| E_mixed_types | adaptive_caas | 0.8508 | 60.120 | 114.525 | 65.897 | 60.250 | 0 | 0.09423 |
| F_cost_latency | fixed | 0.9398 | 135.692 | 427.148 | 64.985 | 394.784 | 0 | 0.02132 |
| F_cost_latency | round_robin | 0.9391 | 134.185 | 424.231 | 65.042 | 393.554 | 0 | 0.02178 |
| F_cost_latency | caas | 0.9391 | 134.454 | 421.415 | 64.831 | 390.189 | 0 | 0.02095 |
| F_cost_latency | adaptive_caas | 0.9383 | 143.074 | 461.949 | 64.921 | 435.629 | 0 | 0.07891 |
| G_cold_start | fixed | 0.7930 | 76.411 | 113.341 | 78.830 | 43.463 | 0 | 0.01732 |
| G_cold_start | round_robin | 0.7875 | 72.556 | 105.340 | 77.866 | 37.093 | 0 | 0.01714 |
| G_cold_start | caas | 0.7820 | 73.127 | 106.280 | 78.103 | 37.272 | 0 | 0.01741 |
| G_cold_start | adaptive_caas | 0.8031 | 74.551 | 107.235 | 76.853 | 85.306 | 0 | 0.05407 |
| H_long_recovery | fixed | 0.9221 | 118.579 | 285.743 | 452.152 | 51.659 | 0 | 0.02898 |
| H_long_recovery | round_robin | 0.9086 | 119.816 | 287.750 | 457.012 | 46.594 | 0 | 0.02583 |
| H_long_recovery | caas | 0.9203 | 119.180 | 287.050 | 454.797 | 43.562 | 0 | 0.02540 |
| H_long_recovery | adaptive_caas | 0.9190 | 119.090 | 287.225 | 453.333 | 57.324 | 0 | 0.10532 |

## Ablations (all seeds/scenarios retained)

| Scenario | Variant | Success | Mean ms | Cost | Starvation count |
|---|---|---:|---:|---:|---:|
| A_uniform | adaptive_caas | 0.9352 | 49.887 | 63.705 | 0.000 |
| A_uniform | no_weights | 0.9352 | 49.887 | 63.705 | 0.000 |
| A_uniform | no_recency | 0.9352 | 49.887 | 63.705 | 0.000 |
| A_uniform | no_health | 0.9359 | 49.792 | 63.734 | 0.000 |
| A_uniform | no_attribution | 0.9305 | 49.976 | 63.802 | 0.000 |
| B_heterogeneous | adaptive_caas | 0.8352 | 52.257 | 62.706 | 0.000 |
| B_heterogeneous | no_weights | 0.8352 | 52.257 | 62.706 | 0.000 |
| B_heterogeneous | no_recency | 0.8352 | 52.257 | 62.706 | 0.000 |
| B_heterogeneous | no_health | 0.7977 | 59.301 | 70.726 | 0.000 |
| B_heterogeneous | no_attribution | 0.8336 | 52.232 | 62.842 | 0.000 |
| C_dynamic_fault | adaptive_caas | 0.8930 | 60.983 | 68.013 | 0.000 |
| C_dynamic_fault | no_weights | 0.8930 | 60.983 | 68.013 | 0.000 |
| C_dynamic_fault | no_recency | 0.8930 | 60.983 | 68.013 | 0.000 |
| C_dynamic_fault | no_health | 0.8883 | 58.785 | 67.980 | 0.000 |
| C_dynamic_fault | no_attribution | 0.8945 | 60.916 | 67.995 | 0.000 |
| D_bursty | adaptive_caas | 0.9352 | 197.439 | 63.980 | 0.000 |
| D_bursty | no_weights | 0.9352 | 197.439 | 63.980 | 0.000 |
| D_bursty | no_recency | 0.9352 | 197.439 | 63.980 | 0.000 |
| D_bursty | no_health | 0.9359 | 198.125 | 64.045 | 0.000 |
| D_bursty | no_attribution | 0.9367 | 196.971 | 63.896 | 0.000 |
| E_mixed_types | adaptive_caas | 0.8508 | 60.120 | 65.897 | 0.000 |
| E_mixed_types | no_weights | 0.8508 | 60.120 | 65.897 | 0.000 |
| E_mixed_types | no_recency | 0.8508 | 60.120 | 65.897 | 0.000 |
| E_mixed_types | no_health | 0.8227 | 64.225 | 71.394 | 0.000 |
| E_mixed_types | no_attribution | 0.8523 | 60.108 | 65.882 | 0.000 |
| F_cost_latency | adaptive_caas | 0.9383 | 143.074 | 64.921 | 0.000 |
| F_cost_latency | no_weights | 0.9398 | 143.389 | 64.890 | 0.000 |
| F_cost_latency | no_recency | 0.9383 | 143.074 | 64.921 | 0.000 |
| F_cost_latency | no_health | 0.9375 | 134.033 | 65.115 | 0.000 |
| F_cost_latency | no_attribution | 0.9375 | 143.080 | 64.896 | 0.000 |
| G_cold_start | adaptive_caas | 0.8031 | 74.551 | 76.853 | 0.000 |
| G_cold_start | no_weights | 0.8031 | 74.551 | 76.853 | 0.000 |
| G_cold_start | no_recency | 0.8031 | 74.551 | 76.853 | 0.000 |
| G_cold_start | no_health | 0.7820 | 73.127 | 78.103 | 0.000 |
| G_cold_start | no_attribution | 0.8031 | 74.551 | 76.853 | 0.000 |
| H_long_recovery | adaptive_caas | 0.9190 | 119.090 | 453.333 | 0.000 |
| H_long_recovery | no_weights | 0.9190 | 119.090 | 453.333 | 0.000 |
| H_long_recovery | no_recency | 0.9193 | 119.117 | 453.444 | 0.000 |
| H_long_recovery | no_health | 0.9185 | 119.210 | 454.694 | 0.000 |
| H_long_recovery | no_attribution | 0.9187 | 119.360 | 453.996 | 0.000 |

6 of 48 Holm-adjusted comparisons significant; direction/effect must be read separately (significant does NOT always mean better).

All outcomes, timeouts, costs, per-type metrics and waits retained in raw files. Max wait >=1000 logical ms is the predeclared starvation indicator, not a proof of infinite starvation.
Cold-start priors do not provide hidden ground truth. No assertion of production benefit or real model quality.


## Protocol and provenance

Final seeds: 50001..50020; development seeds: 11..15. No parameter search was performed. Options were fixed before the final run; seeds and config were written before trials. Each seed/scenario shares identical tasks, declared resources, hidden environmental parameters and 36 pretraining observations (zero in cold start). Workload/history/warmup hashes are asserted equal across all eight strategy/ablation variants.

Four main policies: Fixed, RR, original CAAS, Adaptive CAAS. Four ablations remove dynamic weights, recency decay, health cooldown, or error attribution ONE at a time. Each variant runs every held-out seed/scenario and repeats exactly; repeat clocks retained but not extra independent samples. Final totals: 1,280 primary trials / 102,400 tasks, plus identical repeat executions; development 320 trials / 25,600 tasks. H uses 192 tasks vs 64 in other scenarios.

The 10-A discrete-event model was extended, not its historical files. Every score/constraint/selection invokes production `evaluate_candidate`, original `assess`/selection/priority code, or shared pure adaptive functions. Legacy A-D task rows match the original driver under identical inputs (tested). Private simulated success probabilities are consulted ONLY AFTER selection. Counter-based random draws use seed/task/Agent/draw kind, independent of policy order. Observations enter history only on completion. These are decision-kernel experiments, not full Coordinator/Runtime/SQLite/LLM performance. Separate Mock tests cover real admission/cancellation/concurrency.

Scenarios: A homogeneous; B hidden per-type heterogeneous success/latency; C current availability plus temporary degradation; D burst arrivals with finite capacities; E visible skill specializations plus mixed generic numeric features; F different declared cost/latency plus task bounds; G cold histories with hidden heterogeneity; H longer horizon, long review tasks, priority mixing and finite fault recovery. H models about 6.7 seconds of logical operation, not endurance/stability over hours. No task/model/domain answer is hardcoded into the algorithm.

All policies share eligibility, resources, observable declarations and completed history. New declared cost constraints apply equally to all. Faults activate at actual simulated start time. Adaptive health is legitimately strategy-specific and can cause a different dispatch time/outcome. Synthetic error categories are observable completion outcomes supplied to every policy, never hidden capability hints. Future random outcomes are not used to choose a candidate.

## Metrics and statistical interpretation

Success denominator includes all input tasks, failures and scheduling timeouts. Mean/P95 are arrival-to-terminal completion times; P95 is nearest-rank per seed, then summarized across seeds, not a pooled percentile. Cost is declared relative rate * service_ms/50; never CNY. All declined/timeout rows retained. Max wait and predeclared >=1000ms waiting count are starvation indicators, not proof against infinite starvation. Capacity peaks and independent event sweeps verify no over-admission; all variants end with zero active reservations.

Every mean/SD/unadjusted CI, per-task-type result, fault recovery (including censored nulls) and host scheduling overhead is in summary.json / trial-metrics.json. Host computation is excluded from logical simulation time. CPU numbers include profile construction/scoring/selection; they exclude Runtime/SQLite/network overhead. They fluctuate with machine load. Counter draw determinism does not make host timings bit-identical.

Seed is the analysis unit (20 held-out seeds), not thousands of correlated tasks or deterministic repeats. Sign tests describe paired win/loss direction, not the bootstrap mean-difference hypothesis. All 48 predeclared comparisons (8 scenarios * 3 baselines * 2 primary metrics) use Holm correction. Bootstrap effect intervals are unadjusted and do not control familywise error; small bootstrap counts limit endpoint precision. No significance test is claimed for exploratory ablations, cost, P95 or overhead.

## Actual benefits and costs

- B vs Fixed: +7.8906 percentage points success, unadjusted paired CI [+5.2344,+10.3906], Holm p=.01811; mean latency -13.086 logical ms, CI [-16.453,-9.771], Holm p=.0000916.
- B vs RR: +7.2656 pp, Holm p=.03133; mean latency -11.906ms, Holm p=.0018425.
- E vs Fixed: mean latency -16.292ms, Holm p=.01811. E vs RR: +6.3281 pp success, Holm p=.0001793.
- **No comparison against original CAAS is significant after correction.** Six of 48 comparisons are significant, all as above; no blanket superiority claim.
- Against CAAS, B point estimates improve 79.375% ->83.516%, mean 59.277 ->52.257ms; E success 82.266% ->85.078%, mean 64.225 ->60.120ms, BUT E P95 worsens 102.193 ->114.525ms.
- A/D/C/F/G/H do not show a consistent broad benefit. F latency worsens 134.454 ->143.074ms and P95 421.415 ->461.949ms; C mean 58.782 ->60.983ms worsens. G success improves but mean latency worsens. H success is slightly lower.
- Mean host scoring-pass overhead grows roughly from .015-.025ms CAAS to .047-.105ms Adaptive in this measurement. The extra history processing is a real cost; no production speedup is claimed.
- Every scenario/variant has zero capacity violations and zero final claims. No observed wait >=1000 logical ms in this finite workload; this does not prove starvation freedom under overload.

## Mechanism ablation conclusions

See `../figures/ablation-effects.md` and the complete ablation table above. Differences mean REMOVED mechanism minus full Adaptive.

- Removing weights changes outcomes only slightly in F (success +.1563pp, latency +.315ms); no independent weighting improvement is established. A-D/G/H lack explicit features, so unchanged weights there are expected. E candidate capacity/match choices already dominate ranking.
- Removing 60-second recency is identical in most short scenarios; H changes only about +.0260pp success and +.027ms latency relative to full. This dataset is too short to establish long-term recency benefit.
- Removing health lowers B success by 3.75pp and raises latency by 7.044ms; E success -2.8125pp / latency +4.106ms. Conversely, removing health improves F latency by 9.041ms and G latency by 1.424ms. Circuit cooldown explains much of the point-estimate benefit AND some regressions.
- Removing attribution has small, mixed effects. Correct attribution remains a semantic safeguard even where finite synthetic performance is not better. These ablations are descriptive; no independent causal/statistical generalization is claimed.

## Evidence and limitations

Every main, unfavorable and ablation trial is in gzip JSONL; all task outcomes in raw CSV; workloads and source hashes/configurations saved separately. Manifests verify output bytes. Initial 10-A rerun is retained under baseline-10a; all historical 10-A/05 artifacts remain unchanged. Figures include all strategies/scenarios with seed scatter and intervals using existing ECharts. No real LLM calls, token cost, paid service, private SQLite or API Key is involved.

This is eight curated distributions, one finite simulator, three Agents, limited seeds/horizons and assumed synthetic error attribution. Real Provider failures can be correlated across Agents; declared cost/latency can be inaccurate; cooldown can overreact to independent random failures. Unknown/unlabeled errors remain neutral, so ability reliability does not automatically encode real business correctness. Appropriate claim: an explainable alternative policy with conditional synthetic benefits and measured tradeoffs, not a universally improved production scheduler.


## Supplementary long-clock robustness (not a performance tuning set)

The primary H workload covers only seconds. To check large timestamp gaps, history decay and capacity recovery without mislabeling it as endurance, a separate post-main-evaluation stress check advances a virtual clock across about 16 hours, 192 tasks spaced 5 minutes apart, fault/unavailability/recovery at 120/140-150/180 minutes. Three separate seeds 90001..90003, every main policy, same inputs, deterministic repeat. Configuration and all raw outcomes are under `results/caas-14b/endurance`. This does not alter the held-out study, scoring constants or its 48 tests; no new superiority hypothesis is inferred. It does NOT prove 16 hours of real service uptime, load or memory stability. Reproduce with `scripts/check_caas_endurance_14b.py --output NEW_DIRECTORY`.
