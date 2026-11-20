# 14-B offline SIMULATION ONLY

Logical milliseconds and artificial relative cost, NOT LLM time or CNY. No real calls.

Seed-level paired comparisons. 500 percentile bootstrap draws for means/SD/unadjusted 95% intervals; 1000 for paired effect intervals. Exact two-sided sign tests, ties removed, Holm over all 48 predeclared success/mean-latency comparisons. No task-level pseudo-replication.

| Scenario | Strategy | Success | Mean ms | P95 ms | Sim cost | Max wait ms | Capacity violations | Overhead ms/pass |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A_uniform | fixed | 0.9500 | 50.553 | 59.048 | 64.708 | 0.000 | 0 | 0.01890 |
| A_uniform | round_robin | 0.9375 | 50.310 | 58.537 | 64.396 | 0.000 | 0 | 0.02079 |
| A_uniform | caas | 0.9375 | 50.007 | 59.240 | 64.009 | 0.000 | 0 | 0.01868 |
| A_uniform | adaptive_caas | 0.9313 | 50.096 | 59.157 | 64.123 | 0.000 | 0 | 0.06259 |
| B_heterogeneous | fixed | 0.7781 | 66.040 | 95.475 | 74.725 | 26.537 | 0 | 0.01522 |
| B_heterogeneous | round_robin | 0.8031 | 65.238 | 92.639 | 73.480 | 24.358 | 0 | 0.01533 |
| B_heterogeneous | caas | 0.7875 | 57.007 | 83.378 | 70.118 | 14.949 | 0 | 0.01654 |
| B_heterogeneous | adaptive_caas | 0.7937 | 55.989 | 83.305 | 68.637 | 16.119 | 0 | 0.05453 |
| C_dynamic_fault | fixed | 0.9000 | 60.422 | 89.511 | 68.989 | 31.112 | 0 | 0.01518 |
| C_dynamic_fault | round_robin | 0.8906 | 57.343 | 88.536 | 68.417 | 22.247 | 0 | 0.01638 |
| C_dynamic_fault | caas | 0.8875 | 58.632 | 88.470 | 68.125 | 26.228 | 0 | 0.01531 |
| C_dynamic_fault | adaptive_caas | 0.8844 | 60.180 | 90.565 | 68.068 | 29.850 | 0 | 0.04913 |
| D_bursty | fixed | 0.9406 | 203.256 | 516.722 | 64.308 | 537.517 | 0 | 0.01344 |
| D_bursty | round_robin | 0.9625 | 197.251 | 508.830 | 63.965 | 516.261 | 0 | 0.01282 |
| D_bursty | caas | 0.9469 | 198.247 | 513.111 | 63.776 | 535.848 | 0 | 0.01261 |
| D_bursty | adaptive_caas | 0.9625 | 197.944 | 514.109 | 63.728 | 535.433 | 0 | 0.03983 |
| E_mixed_types | fixed | 0.7625 | 80.388 | 111.493 | 79.915 | 41.848 | 0 | 0.01695 |
| E_mixed_types | round_robin | 0.7969 | 75.376 | 108.981 | 79.009 | 40.420 | 0 | 0.01617 |
| E_mixed_types | caas | 0.7875 | 65.228 | 97.686 | 72.779 | 33.228 | 0 | 0.01678 |
| E_mixed_types | adaptive_caas | 0.8219 | 58.505 | 91.096 | 68.660 | 28.818 | 0 | 0.09123 |
| F_cost_latency | fixed | 0.9313 | 166.342 | 487.022 | 65.125 | 458.145 | 0 | 0.01983 |
| F_cost_latency | round_robin | 0.9313 | 162.640 | 478.895 | 65.452 | 450.272 | 0 | 0.01867 |
| F_cost_latency | caas | 0.9344 | 158.563 | 469.852 | 65.598 | 440.670 | 0 | 0.01933 |
| F_cost_latency | adaptive_caas | 0.9313 | 167.432 | 645.359 | 65.618 | 627.638 | 0 | 0.07546 |
| G_cold_start | fixed | 0.7625 | 80.388 | 111.493 | 79.915 | 41.848 | 0 | 0.01702 |
| G_cold_start | round_robin | 0.7969 | 75.376 | 108.981 | 79.009 | 40.420 | 0 | 0.01735 |
| G_cold_start | caas | 0.7812 | 77.988 | 114.236 | 79.731 | 44.441 | 0 | 0.01668 |
| G_cold_start | adaptive_caas | 0.7562 | 74.962 | 107.445 | 77.966 | 40.299 | 0 | 0.04851 |
| H_long_recovery | fixed | 0.9323 | 120.373 | 291.903 | 458.960 | 39.986 | 0 | 0.02574 |
| H_long_recovery | round_robin | 0.9302 | 118.272 | 290.170 | 451.747 | 36.585 | 0 | 0.02313 |
| H_long_recovery | caas | 0.9240 | 117.863 | 287.735 | 449.528 | 36.233 | 0 | 0.02331 |
| H_long_recovery | adaptive_caas | 0.9219 | 118.584 | 288.503 | 452.004 | 40.121 | 0 | 0.10005 |

## Ablations (all seeds/scenarios retained)

| Scenario | Variant | Success | Mean ms | Cost | Starvation count |
|---|---|---:|---:|---:|---:|
| A_uniform | adaptive_caas | 0.9313 | 50.096 | 64.123 | 0.000 |
| A_uniform | no_weights | 0.9313 | 50.096 | 64.123 | 0.000 |
| A_uniform | no_recency | 0.9313 | 50.096 | 64.123 | 0.000 |
| A_uniform | no_health | 0.9313 | 50.096 | 64.123 | 0.000 |
| A_uniform | no_attribution | 0.9375 | 50.007 | 64.009 | 0.000 |
| B_heterogeneous | adaptive_caas | 0.7937 | 55.989 | 68.637 | 0.000 |
| B_heterogeneous | no_weights | 0.7937 | 55.989 | 68.637 | 0.000 |
| B_heterogeneous | no_recency | 0.7937 | 55.989 | 68.637 | 0.000 |
| B_heterogeneous | no_health | 0.8094 | 55.845 | 68.980 | 0.000 |
| B_heterogeneous | no_attribution | 0.7875 | 56.250 | 68.949 | 0.000 |
| C_dynamic_fault | adaptive_caas | 0.8844 | 60.180 | 68.068 | 0.000 |
| C_dynamic_fault | no_weights | 0.8844 | 60.180 | 68.068 | 0.000 |
| C_dynamic_fault | no_recency | 0.8844 | 60.180 | 68.068 | 0.000 |
| C_dynamic_fault | no_health | 0.8906 | 58.832 | 68.193 | 0.000 |
| C_dynamic_fault | no_attribution | 0.8844 | 59.257 | 68.133 | 0.000 |
| D_bursty | adaptive_caas | 0.9625 | 197.944 | 63.728 | 0.000 |
| D_bursty | no_weights | 0.9625 | 197.944 | 63.728 | 0.000 |
| D_bursty | no_recency | 0.9625 | 198.029 | 63.738 | 0.000 |
| D_bursty | no_health | 0.9625 | 197.944 | 63.728 | 0.000 |
| D_bursty | no_attribution | 0.9469 | 198.162 | 63.766 | 0.000 |
| E_mixed_types | adaptive_caas | 0.8219 | 58.505 | 68.660 | 0.000 |
| E_mixed_types | no_weights | 0.8219 | 58.505 | 68.660 | 0.000 |
| E_mixed_types | no_recency | 0.8219 | 58.505 | 68.660 | 0.000 |
| E_mixed_types | no_health | 0.7875 | 65.228 | 72.779 | 0.000 |
| E_mixed_types | no_attribution | 0.8219 | 58.505 | 68.660 | 0.000 |
| F_cost_latency | adaptive_caas | 0.9313 | 167.432 | 65.618 | 0.800 |
| F_cost_latency | no_weights | 0.9344 | 167.510 | 65.698 | 0.800 |
| F_cost_latency | no_recency | 0.9313 | 167.432 | 65.618 | 0.800 |
| F_cost_latency | no_health | 0.9313 | 157.740 | 65.618 | 0.000 |
| F_cost_latency | no_attribution | 0.9313 | 167.432 | 65.618 | 0.800 |
| G_cold_start | adaptive_caas | 0.7562 | 74.962 | 77.966 | 0.000 |
| G_cold_start | no_weights | 0.7562 | 74.962 | 77.966 | 0.000 |
| G_cold_start | no_recency | 0.7562 | 74.962 | 77.966 | 0.000 |
| G_cold_start | no_health | 0.7812 | 77.988 | 79.731 | 0.000 |
| G_cold_start | no_attribution | 0.7562 | 74.962 | 77.966 | 0.000 |
| H_long_recovery | adaptive_caas | 0.9219 | 118.584 | 452.004 | 0.000 |
| H_long_recovery | no_weights | 0.9219 | 118.584 | 452.004 | 0.000 |
| H_long_recovery | no_recency | 0.9219 | 118.658 | 452.290 | 0.000 |
| H_long_recovery | no_health | 0.9177 | 119.014 | 454.165 | 0.000 |
| H_long_recovery | no_attribution | 0.9198 | 118.527 | 451.757 | 0.000 |

0 of 48 Holm-adjusted comparisons significant; direction/effect must be read separately (significant does NOT always mean better).

All outcomes, timeouts, costs, per-type metrics and waits retained in raw files. Max wait >=1000 logical ms is the predeclared starvation indicator, not a proof of infinite starvation.
Cold-start priors do not provide hidden ground truth. No assertion of production benefit or real model quality.
