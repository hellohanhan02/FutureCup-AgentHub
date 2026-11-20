# Mechanism ablations: removed variant MINUS full adaptive

No significance claim for these exploratory ablations. All scenarios included.

| Scenario | Removed mechanism | Success delta pp | Mean latency delta logical ms | Cost delta |
|---|---|---:|---:|---:|
| A_uniform | no_weights | +0.0000 | +0.0000 | +0.0000 |
| A_uniform | no_recency | +0.0000 | +0.0000 | +0.0000 |
| A_uniform | no_health | +0.0781 | -0.0953 | +0.0292 |
| A_uniform | no_attribution | -0.4688 | +0.0889 | +0.0979 |
| B_heterogeneous | no_weights | +0.0000 | +0.0000 | +0.0000 |
| B_heterogeneous | no_recency | +0.0000 | +0.0000 | +0.0000 |
| B_heterogeneous | no_health | -3.7500 | +7.0444 | +8.0205 |
| B_heterogeneous | no_attribution | -0.1563 | -0.0250 | +0.1365 |
| C_dynamic_fault | no_weights | +0.0000 | +0.0000 | +0.0000 |
| C_dynamic_fault | no_recency | +0.0000 | +0.0000 | +0.0000 |
| C_dynamic_fault | no_health | -0.4687 | -2.1985 | -0.0331 |
| C_dynamic_fault | no_attribution | +0.1563 | -0.0676 | -0.0186 |
| D_bursty | no_weights | +0.0000 | +0.0000 | +0.0000 |
| D_bursty | no_recency | +0.0000 | +0.0000 | +0.0000 |
| D_bursty | no_health | +0.0781 | +0.6858 | +0.0645 |
| D_bursty | no_attribution | +0.1563 | -0.4684 | -0.0842 |
| E_mixed_types | no_weights | +0.0000 | +0.0000 | +0.0000 |
| E_mixed_types | no_recency | +0.0000 | +0.0000 | +0.0000 |
| E_mixed_types | no_health | -2.8125 | +4.1055 | +5.4970 |
| E_mixed_types | no_attribution | +0.1562 | -0.0115 | -0.0147 |
| F_cost_latency | no_weights | +0.1563 | +0.3153 | -0.0310 |
| F_cost_latency | no_recency | +0.0000 | +0.0000 | +0.0000 |
| F_cost_latency | no_health | -0.0781 | -9.0411 | +0.1949 |
| F_cost_latency | no_attribution | -0.0781 | +0.0060 | -0.0247 |
| G_cold_start | no_weights | +0.0000 | +0.0000 | +0.0000 |
| G_cold_start | no_recency | +0.0000 | +0.0000 | +0.0000 |
| G_cold_start | no_health | -2.1094 | -1.4243 | +1.2505 |
| G_cold_start | no_attribution | +0.0000 | +0.0000 | +0.0000 |
| H_long_recovery | no_weights | +0.0000 | +0.0000 | +0.0000 |
| H_long_recovery | no_recency | +0.0260 | +0.0271 | +0.1113 |
| H_long_recovery | no_health | -0.0521 | +0.1207 | +1.3616 |
| H_long_recovery | no_attribution | -0.0260 | +0.2706 | +0.6634 |
