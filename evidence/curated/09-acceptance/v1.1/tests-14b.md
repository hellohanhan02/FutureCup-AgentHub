# 14-B actual validation

- Pre-change backend baseline: 696 passed.
- Final backend: **745 passed**, 0 failed/error/skipped. Original 696 retained +49 new tests in `test_adaptive_caas_14b.py`.
- Frontend: **58 passed** (original 57 +1 DOM regression), TypeScript and production build passed. Actual component regression prevents Adaptive being mislabeled Round Robin.
- Python compile and pip check passed; no dependency added. Cached npm audit: 0 known vulnerabilities (not a fresh advisory guarantee).
- Existing Starlette TestClient deprecation warning remains, no tests weakened.
- New test coverage: original kernel AST preservation; numeric feature validation; positive normalized deterministic weights/default fallback; attribution/unknown HTTP errors; cold start and future rejection; half-life and health ablations; bounded cooldown recovery; common cost eligibility; hard capacities; actual async Runtime duplicate claims/cancellation/timeout/release; actual Workflow API execution/query and private payload exclusion; all 8 simulator scenarios/deterministic repeats; independent capacity sweep; original 10-A row equality; large timestamp gaps and recovery.
- Raw JUnit reports remain private/ignored under `.data/14b-baseline.xml` and `.data/14b-final.xml`; public result summary in `acceptance-14b.json`.
- ECharts standard plots include all policies/scenarios, mean/CI/seed points at `results/caas-14b/echarts`; SVG generation executed. No browser layout or real model inference acceptance is claimed for this task.
- Initial application math import violated the existing dependency whitelist; it was replaced by the documented half-life rule before evaluation, without changing the architecture assertion. The full final architecture check passes.

Reproduce inside the NEW clone only, using its .venv, fresh pytest temp directories, Mock/HTTP fixtures only:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp=.data/pytest-14b-reproduction
.\.venv\Scripts\python.exe -m compileall -q backend/src backend/tests scripts/bench_caas_14b.py scripts/check_caas_endurance_14b.py scripts/render_caas_14b.py
.\.venv\Scripts\python.exe -m pip check
npm.cmd test --prefix frontend
npm.cmd run typecheck --prefix frontend
npm.cmd run build --prefix frontend
```

No original project or historical real database was used. No service was launched and no model credential was needed. Experiments use synthetic relative cost; no real pricing/paid requests.
