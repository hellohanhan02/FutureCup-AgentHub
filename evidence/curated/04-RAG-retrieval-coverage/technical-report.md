# v1.1 / 14-A: isolated development and bounded RAG coverage

## Isolation and baseline

- Original repository: `D:\competition\FutureCup`, read-only throughout this task.
- Authorized original untracked competition ZIP / two DOCX materials are preserved, never cloned or committed.
- New repository: `D:\competition\FutureCup-v1.1`; branch `feature/v1.1-rag`.
- Base: `v1.0.0`, `bb8c418baaa7e9dbf210d4aae6895f2e95bf9e3b`.
- Clone used `--no-hardlinks --no-local --no-checkout`; independent object database, no alternates, no remotes, complete 7-commit baseline history. No original private database / `.env` / environment was copied.
- Independent Python 3.11.9 environment built from installed `D:\study\311\python.exe`; independent `npm ci` from the committed lockfile. No original venv or node_modules copied.
- Baseline before code changes: **671 backend / 57 frontend tests passed**, typecheck, build, compile, pip check passed. See `baseline-14a.json`.
- Installed dependency ranges selected FastAPI 0.163.1 instead of frozen metadata's 0.162.4. Both retrieval versions in the comparison use the SAME new environment. Exact new versions are recorded in `acceptance-14a.json`.

## Mechanism and module boundaries

`KnowledgeStore` retains FTS5 OR matching and BM25, Chinese bigrams, authorized KB scopes, source versions, chunk IDs and old citation format. **No vector embeddings or semantic retrieval are claimed.**

1. `coverage.identify` extracts generic punctuation/conjunction-separated clauses. Long unseparated clauses use overlapping 160-character windows (step 140). Max 8 topics; truncation is audited. It does not infer all unstated business subtopics. Callers can supply reviewed topics; source documents cannot supply configuration.
2. Each topic uses the SAME existing `KnowledgeStore.search`, initially top-2. If no strong normative evidence is found, a bounded deeper top-8 query supplements it. No secret reference answer or future model result is used.
3. Candidates rank by: presence of a matching normative sentence, query token overlap, BM25, stable chunk ID. Each topic selects at most ONE key normative sentence. Topics contribute chunks in round-robin order; duplicate chunk IDs are injected once. Full candidate/query history remains in audit; only compact topic requirements go to the model.
4. An application `KnowledgeGateway` port supplies retrieval/context projection. Runtime does not import storage adapters. Existing workflow, CAAS weights, Provider retry, budget and Self-Loop algorithms are unchanged.
5. All model messages include the retrieved context in the existing input/resource/cost admission path. The context is untrusted user-role source data; it cannot alter system instructions, grant tools or permissions. This is containment and deterministic permission testing, **not a guarantee that every model ignores every prompt injection**.

### Coverage definitions and limits

For topic token set T, chunk token set C: `overlap = |T intersection C| / max(1, |T|)`.

- `COVERED`: overlap >= .75; there is an explicit normative sentence (`must/shall/required/prohibited` or generic Chinese normative markers) with sentence overlap >= .35; its actual source chunk is injected.
- `PARTIAL`: candidates exist but strong evidence is absent, or the selected rule was dropped by the context cap.
- `UNCOVERED`: no candidates were returned. **Never equate this with proof no policy exists.**
- `search_state` separately identifies a completed query, query budget exhaustion and context exhaustion. Search errors/deadline failures raise explicit errors rather than empty successful results.
- Max 8 topics, 16 queries, 4 injected chunks by default (programmatic max 8), 6,000 source characters (configurable 128..16,000), .5s retrieval deadline (max 2s). Deadline checks are cooperative before/after each bounded SQLite call, not an OS-level hard preemption.
- The whole serialized context, including topic metadata, must ALSO fit Runtime's existing input/resource limits and Provider byte ceiling; exceeding those rejects the call. No extra LLM calls are used for retrieval.
- Lexical evidence coverage is not semantic completeness, truth, or final document quality. No guarantees for implicit topics, synonyms, negation, rules spanning chunks, non-normative prose, or more than one key rule per topic. Overlap thresholds are documented heuristics, not calibrated probabilities.
- With 8 disjoint required topics and top-4 context, complete coverage may be impossible. Resource limitations remain explicit; no cap is silently expanded.

### Independent result verification

`knowledge-citations-v1` still checks THIS attempt's actual chunk IDs and exact source substrings. Coverage checks add separate result states:

- `RULE_REFLECTED`: the selected key sentence is cited from its real chunk AND appears verbatim in markdown/summary/items.
- `SOURCE_NOT_CITED` / `SOURCE_NOT_REFLECTED`: an actual selected source exists but was not properly cited / reflected.
- `SOURCE_UNAVAILABLE_ADVICE`: a required topic with no hits is explicitly labeled `design suggestion` / Chinese equivalent on the SAME line. It is not treated as an objective policy. If ALL retrieval is empty, existing `knowledge_no_matches` still stops before the model.
- `SOURCE_UNAVAILABLE_UNMARKED` / `INSUFFICIENT_EVIDENCE`: absence/partial evidence cannot be silently promoted to policy.
- `COVERAGE_RESOURCE_LIMIT`: query/context caps prevented assessing a required topic; not accepted as successful coverage.
- `POSSIBLE_CONFLICT`: a narrow same-topic numeric duration discrepancy (same minute/hour/day unit, decimal normalized) is detected. This is a conservative conflict signal, **not arbitrary natural-language contradiction detection**.

Explicit topics can set `required:true/false`. Automatic topics enforce only strongly relevant normative sentences actually discovered. Not every retrieved fragment is mandatory. Strict verbatim reflection may reject good paraphrases; this conservative behavior is intentional and documented, not semantic grading.

All failing required checks enter the EXISTING VerifierEngine -> FeedbackEngine -> SelfLoop path. The real selected sentence, topic ID, chunk reference and actual failed status are available to the next attempt. No successful model answer is computed or substituted. The Mock provider copies actual retrieved rules and is explicitly simulated; this validates wiring, not real LLM reasoning.

Full retrieval queries, source/doc/KB versions, context hash and coverage state live in `RunReport.retrieval_records` and each attempt snapshot. Verification evidence stores coverage outcomes on both pass/fail. Workspace `knowledge_coverage` summarizes **latest attempts per node**, without mixing failed and current attempt evidence. Existing `knowledge_enhanced` still means valid retrieval/citation integration; it does not promise all business knowledge is covered.

## Compatible interfaces

- Existing `POST /api/v1/knowledge-bases/{id}/search` is unchanged.
- New `POST /api/v1/knowledge-bases/{id}/coverage` accepts:

```json
{"query":"order lock; privacy access","topics":[
  {"id":"lock","query":"order lock","required":true},
  {"id":"privacy","query":"privacy access","required":true}
]}
```

`topics` is optional. The endpoint is read-only retrieval (no inference). Invalid IDs/config/unknown fields are rejected; it cannot expand the KB scope.

- Existing `POST /api/v1/workspace/tasks` adds optional `retrieval_topics` (same shape). It is persisted with the task and passed through confirmed workflow inputs to ALL agents; planner output cannot override these requirements. Existing Agent KB binding and version mechanism remain.
- Example body (use a NEW KB created in this clone):

```json
{"request":"Analyse order lock; design architecture; review requirements",
 "scenario":"architecture","mode":"mock","knowledge_base_ids":["NEW_KB_ID"],
 "max_calls":6,"budget_cny":0.5,
 "retrieval_topics":[{"id":"lock","query":"order lock","required":true}]}
```

Create/import via the existing KB page, generate plan, confirm plan, then inspect `/api/v1/workspace/tasks/{id}` and `/api/v1/self-loops/{node_id}/attempts`. Generic automatic coverage works through the existing UI. Explicit reviewed topics and preview currently use API; no new topic-editing UI is claimed.

## Independent Mock startup and free checks

From **new clone only**, open TWO PowerShell terminals:

```powershell
Set-Location D:\competition\FutureCup-v1.1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-V11Mock.ps1 -Component Backend
```

```powershell
Set-Location D:\competition\FutureCup-v1.1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Start-V11Mock.ps1 -Component Frontend
```

- Backend `http://127.0.0.1:18024/api/v1/health`.
- Workspace `http://127.0.0.1:15194/#/workspace`; KB `http://127.0.0.1:15194/#/knowledge`.
- Only `.data/v1.1/{workflows,knowledge}.sqlite3` in the new clone are used. Only Mock is registered as an executable Provider; the real global switch is false. Existing disabled Provider catalog presets remain for compatibility. The launcher removes known credential variables in its child process WITHOUT reading their contents, and pins the legacy credential variable name to an empty unused variable. Script refuses an occupied port, never stops any service.
- Keep both terminals open. Only empty credential-variable lookups can occur under this launcher; original process environments are not changed. Original ports/services and original `.data` remain untouched. This clone intentionally starts without historical private records.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m compileall -q backend/src backend/tests scripts/evaluate_rag_14a.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/evaluate_rag_14a.py --repeats 20 --output results/rag-14a/reproduction
Set-Location frontend
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

## Offline evaluation (no LLM)

See the [initial report](../../results/rag-14a/report.md), [final reproduction](../../results/rag-14a/final/report.md), and their complete `raw.json`, `raw.csv`, `recall.svg`.

- 30 synthetic documents in 3 domains; 11 queries (10 with reference rules plus a no-match control). Includes Chinese/English, single/four/eight topics and a natural unseparated request. The corpus/annotations are in `benchmarks/rag-14a/dataset.json`.
- Actual frozen adapter is loaded from this clone's Git object database at the frozen hash; new retrieval uses actual current code. Same environment, documents, deterministic UUIDs, scope, input strings, top-4. Reference rules are used ONLY after retrieval to score; never passed into either retriever.
- 20 repeats per case/version; alternating execution order; all 440 rows retained. Repeated hit IDs/recall/context size are deterministic. Clock measurements naturally vary.
- Case-weighted key-rule recall / necessary knowledge coverage: **57.5% -> 80.0% (+22.5 percentage points)**. Each topic has one annotated rule, so these two metrics coincide in this dataset.
- Single-topic cases stay 100%; four-topic cases 50% -> 100%; eight-topic cases 25% -> 50%; natural unseparated request stays 50%. No-match control remains empty.
- Initial recorded run mean retrieval wall time (including coroutine dispatch): **0.927ms -> 1.371ms**. Mean evidence UTF-8 byte-as-token upper proxy: **1986 -> 2395**. This is NOT measured API usage; exact provider tokens/cost remain untested.
- Same extractive Mock citation generator yields 100% valid citations and 0% unsupported copied claims by construction, BOTH versions. **Actual model unsupported-content ratio is null / not measured.** Never present these synthetic checks as a real model hallucination rate.
- Small curated corpus, finite top-K, no synonym/semantic retrieval, no statistical significance claim. Repeats are not independent task samples. Latency is local hardware/cache dependent and higher-quality evidence costs more context.
- `initial` raw measurements were generated before the final decimal-normalization/port-boundary corrections; these do not change the dataset's retrieval outcomes. The final reproduction is separately preserved, never replacing initial measurements.

## Acceptance and next step

Live Mock smoke checks returned HTTP 200 for backend health, frontend HTML, Vite proxy and the new empty knowledge catalog. They used only new ports, and their processes were stopped after verification.

Final actual test/dependency results are recorded in `acceptance-14a.json` (created after tests finish). Original tests were not removed, skipped or weakened. One development regression (Runtime importing the concrete coverage adapter) was caught by the existing architecture test and fixed through `KnowledgeGateway`; a new port annotation import issue was also fixed before final full regression.

Suitable next step: a SMALL, explicitly authorized real-model validation in this NEW clone with reviewed required topics, an independent newly imported KB, existing 6-call / 1-repair / user-confirmed cost guard, reviewed new ports and new credential entry. No such real test was executed or authorized here. Do not reuse original DB paths or launch old real-test scripts against old services. Budget caps were neither enlarged nor bypassed. Controlled testing is advisable before claiming improved real document coverage or general contradiction reasoning.

## Changed files

- `backend/src/futurecup/adapters/coverage.py`: topic extraction, bounded multiquery merge, coverage, reflection/conflict checks.
- `adapters/knowledge.py`, `adapters/llm.py`: gateway projection / actual retrieval-backed explicit Mock behavior.
- `application/ports.py`, `runtime.py`, `planner.py`: port integration, compact untrusted context, reviewed topic propagation and current-attempt coverage summary.
- `api/app.py`, `api/knowledge.py`, `api/workspace.py`: independent checks, preview API, optional topic configuration.
- `backend/tests/test_rag_14a.py`: no-network new tests, including repair and budgets.
- `benchmarks/rag-14a/dataset.json`, `scripts/evaluate_rag_14a.py`, `results/rag-14a/*`: reference corpus, frozen-vs-new runner, every measurement and reports.
- `scripts/Start-V11Mock.ps1`: independent Mock-only startup.
- `docs/v1.1/*`, `README.md`, `docs/architecture.md`: baseline, acceptance and operation/design documentation.


### Final verified results

- Backend: **696 passed**, zero failures/errors/skips (671 unchanged original tests + 25 new tests).
- Frontend: **57 passed**; TypeScript and production build passed; frontend source unchanged.
- Python compile, pip check, PowerShell launcher parse and Git diff check passed. Cached npm audit: 0 vulnerabilities (not a fresh advisory guarantee).
- Final separate 440-measurement reproduction: recall/coverage still **57.5% -> 80.0%**; mean wall retrieval **1.906ms -> 3.334ms**; evidence token upper proxy **1986.18 -> 2394.64**. The first run's timing is also preserved above: latency is noisy, not a production speed claim.
- One existing TestClient deprecation warning; no new runtime dependency.
- Local Mock HTTP health / frontend / proxy / KB catalog: HTTP 200. Real calls disabled; only Mock executable. Smoke-check processes were stopped; launch manually for browsing.
- New clone locally reuses the user's existing original repository commit identity (no invented identity; original config unchanged).
