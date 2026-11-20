# Offline retrieval comparison (synthetic only)

Same frozen v1.0.0 adapter and current adapter, same indexed documents/query/IDs/top-4. No model calls.

| Metric (case-weighted) | v1.0.0 | v1.1 |
|---|---:|---:|
| key_rule_recall | 0.575000 | 0.800000 |
| necessary_knowledge_coverage | 0.575000 | 0.800000 |
| extractive_mock_citation_validity | 1.000000 | 1.000000 |
| extractive_mock_unsupported_ratio | 0.000000 | 0.000000 |
| latency_ms | 0.926930 | 1.371004 |
| injected_token_budget_upper_proxy | 1986.181818 | 2394.636364 |
| retrieval_calls | 1.000000 | 4.090909 |

| Case | v1.0.0 rule recall | v1.1 rule recall |
|---|---:|---:|
| operations-single | 1.0000 | 1.0000 |
| operations-four | 0.5000 | 1.0000 |
| operations-eight | 0.2500 | 0.5000 |
| library-single | 1.0000 | 1.0000 |
| library-four | 0.5000 | 1.0000 |
| library-eight | 0.2500 | 0.5000 |
| laboratory-single | 1.0000 | 1.0000 |
| laboratory-four | 0.5000 | 1.0000 |
| laboratory-eight | 0.2500 | 0.5000 |
| no-match | N/A (no reference rules) | N/A (no reference rules) |
| operations-natural | 0.5000 | 0.5000 |

NOT_TESTED: small curated corpus; repeated timings are not independent tasks

No LLM generated answers. Extractive Mock source checks are true by construction; no real hallucination-rate claim.

UTF-8 bytes of injected evidence JSON; conservative proxy, not API token usage

Deterministic outcomes: True. All 440 measurements retained.
