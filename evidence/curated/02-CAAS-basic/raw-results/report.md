# 10-A SIMULATION ONLY

Seed is independent unit; sample SD; percentile paired/unpaired bootstrap mean CI; two-sided exact paired sign test; Holm across 16 primary comparisons. No significance test of wall-clock overhead.

| Scenario | Policy | Success mean | SD | Mean latency ms | P95 ms | Scheduling pass ms | Capacity violations |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A_uniform | fixed | 0.9422 | 0.0348 | 49.966 | 58.765 | 0.0352 | 0 |
| A_uniform | round_robin | 0.9391 | 0.0316 | 49.974 | 58.815 | 0.0370 | 0 |
| A_uniform | caas | 0.9367 | 0.0280 | 50.015 | 58.809 | 0.0362 | 0 |
| B_heterogeneous | fixed | 0.7492 | 0.0646 | 65.729 | 91.757 | 0.0293 | 0 |
| B_heterogeneous | round_robin | 0.7570 | 0.0440 | 64.798 | 91.138 | 0.0292 | 0 |
| B_heterogeneous | caas | 0.7703 | 0.0613 | 60.679 | 88.158 | 0.0311 | 0 |
| C_dynamic_fault | fixed | 0.8922 | 0.0415 | 58.490 | 95.348 | 0.0305 | 0 |
| C_dynamic_fault | round_robin | 0.8875 | 0.0298 | 58.340 | 93.515 | 0.0316 | 0 |
| C_dynamic_fault | caas | 0.8914 | 0.0357 | 59.061 | 97.019 | 0.0301 | 0 |
| D_bursty | fixed | 0.9375 | 0.0296 | 201.055 | 517.515 | 0.0248 | 0 |
| D_bursty | round_robin | 0.9367 | 0.0246 | 200.134 | 516.343 | 0.0243 | 0 |
| D_bursty | caas | 0.9516 | 0.0243 | 199.506 | 511.550 | 0.0244 | 0 |

See summary.json for all seed-level confidence intervals, loads, censored recovery and 16 Holm-adjusted tests.
Raw failures, timeouts and every policy are retained. Virtual time is reproducible; measured host overhead is not bit-identical.
