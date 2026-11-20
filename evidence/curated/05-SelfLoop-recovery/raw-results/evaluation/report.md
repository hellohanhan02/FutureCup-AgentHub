# Actual old node-local vs precise SelfLoop (MOCK ONLY)

Actual old node-local SelfLoop; NOT an invented full-workflow restart. Terminal cross-run recovery has no legacy equivalent and is covered separately by tests.

Simulated token units = UTF8 message/output bytes, NOT API tokens; cost = units/1e6 artificial units, NOT CNY. Logical latency = fixed 10ms per model invocation on graph critical path, manual wait unknown/null.

| Case | Mode | Verified rate | Repeated nodes | Model calls | Token proxy | Logical ms | Unsafe retries | Termination evidence accuracy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| format | legacy | 1.00 | 1.00 | 3.00 | 1594.3 | 30.0 | 0.00 | 1.00 |
| format | precise | 1.00 | 1.00 | 3.00 | 1594.3 | 30.0 | 0.00 | 1.00 |
| schema | legacy | 1.00 | 1.00 | 3.00 | 1735.3 | 30.0 | 0.00 | 1.00 |
| schema | precise | 1.00 | 1.00 | 3.00 | 1723.3 | 30.0 | 0.00 | 1.00 |
| business | legacy | 1.00 | 1.00 | 3.00 | 1604.1 | 30.0 | 0.00 | 1.00 |
| business | precise | 1.00 | 1.00 | 3.00 | 1610.1 | 30.0 | 0.00 | 1.00 |
| permanent | legacy | 0.00 | 1.00 | 3.00 | 1722.4 | 30.0 | 0.00 | 1.00 |
| permanent | precise | 0.00 | 1.00 | 3.00 | 1710.4 | 30.0 | 0.00 | 1.00 |
| parallel_schema | legacy | 1.00 | 1.00 | 3.00 | 1641.5 | 20.0 | 0.00 | 1.00 |
| parallel_schema | precise | 1.00 | 1.00 | 3.00 | 1629.5 | 20.0 | 0.00 | 1.00 |
| tool_switch | legacy | 1.00 | 1.00 | 4.00 | 2923.9 | 40.0 | 0.00 | 1.00 |
| tool_switch | precise | 1.00 | 1.00 | 4.00 | 2923.9 | 40.0 | 0.00 | 1.00 |
| tool_timeout | legacy | 0.00 | 1.00 | 3.00 | 1448.4 | 30.0 | 0.00 | 1.00 |
| tool_timeout | precise | 0.00 | 1.00 | 3.00 | 1448.4 | 30.0 | 0.00 | 1.00 |
| permission | legacy | 1.00 | 1.00 | 3.00 | 1504.3 | 30.0 | 0.00 | 1.00 |
| permission | precise | 0.00 | 0.00 | 2.00 | 297.6 | 20.0 | 0.00 | 1.00 |
| budget | legacy | 0.00 | 1.00 | 1.00 | 137.8 | 10.0 | 0.00 | 1.00 |
| budget | precise | 0.00 | 0.00 | 1.00 | 137.8 | 10.0 | 0.00 | 1.00 |
| service_unavailable | legacy | 1.00 | 1.00 | 3.00 | 1482.3 | 30.0 | 0.00 | 1.00 |
| service_unavailable | precise | 1.00 | 1.00 | 3.00 | 1506.3 | 30.0 | 0.00 | 1.00 |
| verifier_error | legacy | 0.00 | 0.00 | 1.00 | 137.8 | 10.0 | 0.00 | 1.00 |
| verifier_error | precise | 0.00 | 0.00 | 1.00 | 137.8 | 10.0 | 0.00 | 1.00 |
| citation | legacy | 1.00 | 1.00 | 3.00 | 7381.1 | 30.0 | 0.00 | 1.00 |
| citation | precise | 1.00 | 1.00 | 3.00 | 7381.1 | 30.0 | 0.00 | 1.00 |
| conflict | legacy | 1.00 | 1.00 | 3.00 | 7590.1 | 30.0 | 0.00 | 1.00 |
| conflict | precise | 1.00 | 1.00 | 3.00 | 7584.1 | 30.0 | 0.00 | 1.00 |
| idempotent_effect | legacy | 1.00 | 1.00 | 4.00 | 3154.8 | 40.0 | 0.00 | 1.00 |
| idempotent_effect | precise | 1.00 | 1.00 | 3.00 | 2575.4 | 30.0 | 0.00 | 1.00 |
| unknown_effect | legacy | 1.00 | 1.00 | 4.00 | 3154.8 | 40.0 | 1.00 | 1.00 |
| unknown_effect | precise | 0.00 | 0.00 | 2.00 | 480.5 | None | 0.00 | 1.00 |
