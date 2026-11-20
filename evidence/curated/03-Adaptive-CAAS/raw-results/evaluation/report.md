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
