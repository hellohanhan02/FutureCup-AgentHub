> Public delivery: use the package-root README and launchers. The frozen development instructions below are historical; do not use their original-machine paths.

# FutureCup Agent 编排平台

2026 第五届未来精英创新大赛——Agent 编排创新应用赛道。当前已实现 **Agent Runtime、工作流引擎、CAAS、独立验证、Self-Loop、Vue 控制台及真实 LLM 接入配置**。真实调用默认关闭；用户已完成真实 DeepSeek 联调。阶段性冻结已只读核对五次真实运行，包括 09-C 的 FAILED → PASSED；汇总及证据边界见 [比赛证据索引](docs/competition-evidence-index.md)。本次冻结没有发送真实请求。

## 当前交付

10-B2 展示精修：真实案例卡片、产物数据流、基于实际验证记录的修复时间线、可切换 CAAS 图表及监控概览，详见 [UI 精修与浏览器复核清单](docs/ui-polish-10b2.md)。只修改前端展示，未变更历史证据或核心算法。

10-B 比赛演示：历史真实记录只读回放、脱敏本地证据包、完整 CAAS 模拟结果展示及录屏方案见 [演示指南](docs/demo-guide-10b.md)、[取证清单](docs/evidence-checklist-10b.md)、[视频脚本](docs/demo-video-script-10b.md)。专用回放服务不加载 Provider，不提供模型执行入口；原始数据库不进入 Git。实际浏览器和录屏项尚未验收。

10-A 离线调度公平实验：生产 fixed / round_robin / caas 决策内核、四类合成负载、20 个种子，全部原始数据与局限见 [scheduler-benchmark-10a.md](docs/scheduler-benchmark-10a.md)。不调用模型，不把模拟结果当作真实 LLM 性能；没有校正后显著优胜结论。

- Python 3.11 / FastAPI 后端、进程内多 Agent 配置与版本注册、异步单 Agent 执行服务。
- 模型调用、工具白名单及参数校验、调用预算、超时、取消、执行记录和结果查询。
- 明确标记的 Mock Provider，以及默认关闭的 OpenAI 兼容 HTTP Provider。
- JSON 工作流、顺序依赖、并行汇合、条件分支、有界循环和子工作流；复用同一 Agent Runtime。
- SQLite 工作流定义、状态快照和事件，节点记录查询、超时及取消传播。
- 能力画像、按任务类型及来源隔离的执行统计；fixed、round_robin、caas 共用过滤约束和 Runtime 原子认领。
- 优先级及等待时间补偿、动态容量检查、SQLite 调度解释记录和只读查询 API。
- 独立 JSON Schema、声明式断言、数值一致性和受信任外测适配器；规则与产物版本绑定。
- 结构化失败分类、原 Agent 修复、受约束的 Agent/工具更换、显式备用路径和人工暂停。
- 跨尝试/子工作流共享预算、超时与取消传播、SQLite 尝试/验证/反馈审计和旧反馈拒绝。
- TypeScript / Vue 3 控制台：平台总览、Agent 管理、Vue Flow 工作流编排、运行监控和执行因果链。
- 界面数据来自后端 API，支持版本保存、预检、执行/取消、能力及调度查询、验证/反馈和人工介入。
- DeepSeek、Qwen 与其他兼容服务的服务端模型目录、精确地址限制、能力声明、有限重试、Token 记录与未知费用标识。
- 调度、验证、反馈闭环及持久化设计，详见 [架构文档](docs/architecture.md)。
- 模型与调度压力实验明确标为模拟；模拟数据不能作为真实 LLM 性能结论，真实运行和模拟回放分别保留证据。

## 环境与启动

任务 09-C 的八任务约束排序见 [场景定义](docs/self-loop-challenge-09c.md)。运行 `eb593f4eb2624c40a31777dc1f51e1b5` 两轮失败的证据、v2 验证器与精简反馈、**558 项后端 / 26 项前端**测试结果及新预检命令见 [反馈质量复盘](docs/self-loop-feedback-review-09c.md)。约束和预算不变，不保证真实修复成功。

任务 09-B 的两次调用/一次修复限制与准备记录见 [self-loop-real-09b.md](docs/self-loop-real-09b.md)。用户已完成真实首轮通过、零修复测试；这不代表真实反馈修复已成功。

任务 09-A 的三 Agent 串行协作定义、独立产物一致性检查及手动授权命令见 [serial-collaboration-09a.md](docs/serial-collaboration-09a.md)。用户已完成真实串行协作；本次没有重新执行。

任务 08-B 提供默认关闭的 DeepSeek 安全配置、隐藏输入 Key 的 PowerShell 启动脚本及首次单请求测试定义。尚未输入用户 Key 或发送真实推理，操作和费用限制见 [DeepSeek 安全准备](docs/deepseek-safe-preparation-08b.md)。

目标：Windows 11、Python 3.11、受支持的 Node LTS（22.12+ 或 24.x，本机 24.11.0）、npm、Git；完整 Node 约束见 frontend/package.json。调度公式、统计口径、真实运行的模拟实验及文件清单见 [任务 05](docs/scheduler-05.md)。工作流语义见 [任务 04](docs/workflow-engine-04.md)，Runtime 见 [任务 03](docs/agent-runtime-03.md)，历史环境见 [任务 02-B](docs/backend-integration-02b.md)。

项目 `.venv` 使用 Python 3.11.9，当前 Node 24.11.0 / npm 11.6.1。任务 08 全量 **322 项 pytest**、**22 项前端单元/DOM 模拟测试**、TypeScript 和生产构建通过，保留原 270/16 项测试；没有新增依赖。任务 07 已按要求提交为 `37a9770`，没有 push。真实接入、密钥配置、费用口径、三场景定义与验收见 [real-llm-integration-08.md](docs/real-llm-integration-08.md)。

前端使用 Vue Flow、Naive UI、ECharts 与 Vue Router。启动前后端后打开 `http://127.0.0.1:5173`，进入“工作流编排”→“初始化示例依赖”→加载顺序/并行/Self-Loop 示例→执行。所有示例生成均明确标记 Mock，初始化可重复且不覆盖不同的不可变配置。

“模型与 Provider”页面只读取配置/最近请求观测，不自动测试真实连接，不接收 API Key。任务 08 的需求审查、数据分析和发票修复定义可在该页初始化 Mock 依赖后加载；完整定义在 `frontend/src/examples/llm08/`。

真实服务使用 `config/llm-providers.example.json` 作为无密钥模板，API Key 仅由服务端环境变量读取。需负责人确认模型、地域、能力和价格后，同时启用单个 Provider 与全局 `FUTURECUP_ENABLE_REAL_LLM`，并由用户主动执行任务。仅设置 Key 或选择 REAL 不会发起请求；本阶段不得自动执行真实请求。Token/配置单价估算不是实付账单或精确费用硬预算。

```powershell
# 在项目根目录执行；直接使用已经建立的项目虚拟环境。
$env:FUTURECUP_ENABLE_REAL_LLM = 'false'
$env:FUTURECUP_DATABASE_URL = 'sqlite:///.data/futurecup.sqlite3'
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple --cache-dir .data/pip-cache -e './backend[dev]'
.\.venv\Scripts\python.exe -m pytest backend/tests -v
.\.venv\Scripts\python.exe -m uvicorn futurecup.api.app:app --reload --host 127.0.0.1 --port 8000
```

另一个终端：

```powershell
cd frontend
npm ci --offline=false --cache ../.data/npm-cache
npm run typecheck
npm run build
npm run dev
```

前端地址由 Vite 启动输出确定（默认 127.0.0.1:5173）。`GET http://127.0.0.1:8000/api/v1/health` 是进程存活检查，不验证数据库、模型、工具或调度可用性。前端 `/api` 通过 Vite 代理访问后端，生产环境以后采用同源反向代理，本阶段不部署。

配置读取进程环境变量；`.env.example` 是说明模板，复制为 `.env` **不会自动加载**。Mock 不需要密钥；真实 Provider 必须经负责人批准后明确启用，本阶段没有执行该操作。SQLite 默认位于根目录 `.data/futurecup.sqlite3`，首次访问工作流接口时创建；请从项目根目录启动、仅使用单 worker。

## 自适应调度与对照实验

工作流可在根定义设置 `scheduling_strategy` 为 `fixed`、`round_robin` 或默认 `caas`，子运行继承根策略。旧 `agent_id` 节点仍然有效；动态节点使用以下 config：

```json
{
  "candidate_agents": ["researcher", "reviewer"],
  "task_type": "analysis", "required_skills": ["analysis"],
  "preferred_skills": ["review"], "required_tools": [], "priority": 80,
  "no_candidate_policy": "wait", "schedule_timeout_seconds": 10,
  "input": {"$ref": "input"}
}
```

Agent 注册配置可声明 `task_types`（缺省 `["*"]`）、skills、tools、available、estimated_cost 和 estimated_latency_ms。这些是人工配置；执行成功/失败及耗时另存统计，按 Agent 版本、任务类型、Mock/真实/模拟来源隔离。**没有已收集样本时成功率为 null，评分先验为 0.5**。

不指定 candidate_agents 时，从提交工作流时的注册表固定候选版本；运行中重新读取可用状态和实际负载。缺少合格候选默认失败；wait 策略及容量等待都受 deadline 限制。Agent 配置修改继续使用 PUT 创建新版本。管理响应及调度查询均不回显 system_prompt。

后端启动后可查询：

```powershell
$api = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$api/scheduling/strategies"
Invoke-RestMethod "$api/scheduling/agents?task_type=analysis"
Invoke-RestMethod "$api/scheduling/decisions?limit=100"
# 使用已返回的运行 ID 筛选：
# Invoke-RestMethod "$api/scheduling/decisions?run_id=$runId"
```

以下命令通过原 WorkflowEngine 运行 7 类任务、3 种策略、3 次重复的固定种子模拟实验：

```powershell
.\.venv\Scripts\python.exe benchmarks/run_scheduler_benchmark.py --seed 20261111 --tasks 24 --repeats 3
```

共 1,512 个评估任务；原始任务、源码哈希、逐次决策、负载分布和统计位于 `.data/scheduler-05-results.json`。最终这次运行的总体成功率 fixed/RR 均为 79.96%，CAAS 为 79.56%；不同场景结果各有差异。统计包含无候选和失败任务，没有择取成功样本或保证 CAAS 胜出，详情见阶段文档。操作系统时序及实测历史会影响后续选择，固定种子不等于逐位相同的墙钟运行结果。

## 工作流演示与 API

无需启动 HTTP，即可用真实引擎、Runtime 和计算器运行三个 JSON 案例（模型全部 Mock）：

```powershell
.\.venv\Scripts\python.exe backend/scripts/run_workflow_demos.py
```

实际结果：顺序三个角色成功；并行两 Agent 汇合后第三个成功；条件失败分支的循环执行两次后以 `workflow_loop_limit` 安全失败。最后一例的 FAILED 是预期验收结果。完整报告及子运行结果写入 `.data/workflow-04-demos.json`。

后端启动后，在根目录的另一 PowerShell 终端执行以下 HTTP 示例。工作流相同版本不可覆盖，重复发布返回 409；重启后工作流定义仍在，但 Agent 需重新注册。

```powershell
$api = 'http://127.0.0.1:8000/api/v1'
$agents = Get-Content examples/workflows/agents.json -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($agent in $agents) {
  $body = $agent | ConvertTo-Json -Depth 32
  Invoke-RestMethod -Method Post -Uri "$api/agents" -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}
foreach ($name in @('increment-body','sequential','parallel','branch-loop')) {
  $body = Get-Content "examples/workflows/$name.json" -Raw -Encoding UTF8
  Invoke-RestMethod -Method Post -Uri "$api/workflows" -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}
$body = @{input = @{topic = 'FutureCup workflow demo'}; version = '1'} | ConvertTo-Json -Depth 8
$run = Invoke-RestMethod -Method Post -Uri "$api/workflows/sequential-demo/executions" -ContentType 'application/json' -Body $body
for ($attempt = 0; $attempt -lt 100; $attempt++) {
  $result = Invoke-RestMethod "$api/workflow-runs/$($run.run.id)"
  if ($result.run.state -in @('succeeded','failed','cancelled')) { break }
  Start-Sleep -Milliseconds 50
}
$result | ConvertTo-Json -Depth 32
Invoke-RestMethod "$api/workflow-runs/$($run.run.id)/nodes"
Invoke-RestMethod "$api/workflow-runs/$($run.run.id)/events?after=0"
# 取消：Invoke-RestMethod -Method Post "$api/workflow-runs/$($run.run.id)/cancel"
```

将提交路径中的工作流 ID 改为 `parallel-demo`、输入保持 topic 可运行并行案例；`branch-loop-demo` 的输入为 `@{score=0}`（安全失败）或 `@{score=3}`（选择成功分支）。使用 `http://127.0.0.1:5173/api/v1` 可验证开发代理。

节点状态包括 pending、ready、running、succeeded、failed、skipped、cancelled。全部结果显式 `verified=false`：执行及 Schema 检查成功不等于可信验证通过。默认失败只阻断后继，独立分支可完成；可选 fail_fast 取消其他活动节点。详见阶段文档中的汇合、循环及嵌套预算语义。

## 本地 API 验证（明确使用 Mock）

后端启动后，在另一个 PowerShell 终端执行。重复使用相同 Agent ID 会返回 409，可查询原 Agent 或使用新 ID。

```powershell
$api = 'http://127.0.0.1:8000/api/v1'
Invoke-RestMethod "$api/health"
$agent = @{
  id = 'calculator-agent'; name = 'Calculator demo'; role = 'calculator'
  responsibilities = 'Verify local arithmetic'; system_prompt = 'Use the approved calculator tool.'
  provider = 'mock'; model = 'mock-calculator'; version = '1'
  tools = @(@{id = 'calculator'; version = '1'})
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri "$api/agents" -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($agent))
Invoke-RestMethod "$api/agents"
$body = @{input = '{"operation":"multiply","a":6,"b":7}'} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri "$api/agents/calculator-agent/executions" -ContentType 'application/json' -Body $body
for ($attempt = 0; $attempt -lt 100; $attempt++) {
  $result = Invoke-RestMethod "$api/executions/$($run.execution.id)"
  if ($result.execution.state -in @('succeeded','failed','timed_out','cancelled')) { break }
  Start-Sleep -Milliseconds 50
}
$result | ConvertTo-Json -Depth 12
```

验收标准：`execution.state` 为 `succeeded`，`simulated` 为 `true`，`tool_calls[0].output.result` 为 42，`call_count` 为 3。此过程中的模型响应为模拟输出，计算器通过真实工具执行计算。可以将 `$api` 改为 `http://127.0.0.1:5173/api/v1` 验证开发代理。

创建使用 `model='mock-echo'`、不同 ID / role / system_prompt 的 Agent 可验证多配置共存。`PUT /api/v1/agents/{id}` 修改完整配置时必须提供新 `version` 和当前 `expected_version`；禁用使用 `POST /api/v1/agents/{id}/disable`，取消执行使用 `POST /api/v1/executions/{id}/cancel`。

## 基础检查

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend/src backend/tests backend/scripts benchmarks
.\.venv\Scripts\python.exe -m pytest backend/tests -v
```

后端最近全量结果为 584 项通过，没有跳过；10-B2 未改后端，未重新运行 pytest。原 Starlette/HTTPX 弃用警告保留。没有配置 mypy/Ruff，compileall 不等于完整 Python 类型检查；依赖边界测试未修改。前端 39 项单元/DOM 模拟测试、TypeScript、格式和生产构建通过；只读历史 HTTP/Vite 代理回归通过。用户已确认原 10-B 浏览器人工检查通过，本次精修后的两分辨率布局仍需重新人工复核，没有真实模型请求。

任务 08 HTTP 预演（关闭真实调用，先启动本地服务）：

```powershell
.\.venv\Scripts\python.exe backend/scripts/check_real_llm_integration.py
```

本次默认端口已有用户服务，独立验收使用 18008/15178，原服务保持运行。Vite 可通过仅允许 loopback 的 `FUTURECUP_API_TARGET` 指向独立后端；正常启动默认仍为 8000。已有后端需由使用者重启以加载模型目录接口。证据见 `.data/real-llm-08-http.json` 和阶段文档。

## 验证与反馈演示（全部模型为 Mock）

```powershell
.\.venv\Scripts\python.exe backend/scripts/run_selfloop_demos.py
.\.venv\Scripts\python.exe backend/scripts/check_verification_api.py
```

实际结果：A 首次格式错误后第 2 次通过；B 原 Agent 连续失败 2 次，换候选 Agent 后通过；C 失败 3 次后停止；D 分别输出 INCONCLUSIVE 和 ERROR。验证和恢复逻辑真实运行，模型响应为显式模拟，不代表真实 LLM 自主修复。完整证据位于被 Git 忽略的 `.data/selfloop-06-demos.json` 和 `.data/api-06-http.json`。

任务节点通过 `verification: {id, version}` 选择不可变规则，通过 `recovery` 显式开启有限重试；旧工作流继续 `verified=false`。新增 `/api/v1/verification-policies`、`/self-loops/{节点实例ID}/attempts`、`/verifications`、`/feedback` 和 `/human-interventions`。人工恢复必须配置进程环境 `FUTURECUP_APPROVAL_TOKEN` 并提交授权头、当前 attempt/产物版本，只能 retry/abort。默认不允许人工操作，令牌不得写入仓库。

## 后续边界

工作流、调度统计及验证/反馈审计持久化到 SQLite；Agent 注册表、Runtime 全记录和容量认领仍在进程内。仅支持单 Runtime/事件循环，禁止多 worker；重启不自动续跑。执行统计与质量证据分开。费用只做配置估算，无精确货币硬预算；安全地址检查不代表完整出口隔离。没有任意代码沙箱、完整认证或租户隔离，仅用于可信本地开发。真实模型效果、思考模式工具历史、流式/多模态、MCP/RAG、崩溃续跑及质量画像学习尚未实现。当前 npm audit 为 0 项漏洞；Python 依赖尚未锁定。不部署或 push，等待用户后续确认真实调用。
# 11-A 智能协作工作台

新增普通用户入口 `/#/workspace`，专业总览迁至 `/#/professional`，其余专业路径及只读比赛演示保留。默认 Mock：自然语言输入 → 受限计划 → 用户确认 → 原 CAAS/工作流/验证反馈引擎 → 可读成果与 Markdown/JSON 导出。三个场景为架构方案、原文审查、依赖与窗口排序；真实模型须单独启用和授权，本阶段未发送真实请求。

启动、预算边界及验收详见 [docs/user-workspace-11a.md](docs/user-workspace-11a.md)。隔离开发端口前端 15182、后端 18012，使用专用 `.data/user-workspace-11a.sqlite3`，不使用历史 DeepSeek 数据库。后端 613、前端 45 项测试通过，无新增依赖。
# 11-B 真实工作台联调准备

完整设计任务可显式选择“完整系统设计文档”，动态规划后由用户确认，最终 Agent 汇总两上游真实产物并生成中文 Markdown。规划/前置节点输出 2048、最终节点 4096 Tokens；共享六次调用、一次修复及可配置预算（默认 ¥0.50，本次建议用户手动选择 ¥2）。文档安全配置通过 `Start-DeepSeekSafe.ps1 -WorkspaceDocument -BudgetCny 2` 显式启用，使用独立 18013/15183 及新数据库，原 preparation 配置不变。前端还需选择同意本次任务预算，启动不等于任务授权。

执行诊断修复后无网络验收后端 655、前端 53 项通过，TypeScript/构建通过；未发送真实模型请求。完整文档规划复用原 SelfLoop/Verifier/FeedbackEngine，和执行阶段共享一次修复；明确 length 的隔离产物可以占用该次修复，未知原因/网络异常不自动重试。实际启动、价格预检和授权步骤见 [user-workspace-real-11b.md](docs/user-workspace-real-11b.md)，实际失败字段及证据见 [规划失败复盘](docs/workspace-planning-failure-11b.md) 和 [执行失败复盘](docs/workspace-execution-failure-11b.md)。结构/章节检查不能证明设计内容正确。
# 12-B 本地知识库

新增 TXT/MD/PDF/DOCX 导入、分块、SQLite FTS5/BM25 中文关键词检索、Agent 绑定、Runtime 来源注入、引用校验与原 Self-Loop 反馈集成。知识库在独立 `.data/knowledge-12b.sqlite3`，不是向量语义搜索。当前适用于可信本机单用户，无多租户鉴权或 OCR。Mock 页面 `http://127.0.0.1:15184/#/knowledge`；本阶段后端667、前端54项测试及类型/构建通过，未请求真实模型。说明与启动见 [knowledge-base-integration-12b.md](docs/knowledge-base-integration-12b.md)。
# 12-C 知识库误选与零命中保护

真实任务误选同名空库，绑定传递正常；原长查询在正确库可召回6块。现显示文档数、版本与短ID，禁选/拒绝空库；零命中不得作为知识增强成功交付。查询限定用户需求与节点职责，未改FTS OR/BM25。无网络后端670、前端55项及类型/构建通过，历史快照不变。复盘与安全重测步骤见 [knowledge-retrieval-failure-12c.md](docs/knowledge-retrieval-failure-12c.md)。
