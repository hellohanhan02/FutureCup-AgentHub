# 10-A：CAAS 公平离线调度基准

**SIMULATION ONLY。** 基点 `97f43b0709b0edf094424cd85cb176af75936cd1`。本实验没有读取 API Key、调用模型、访问历史工作流数据库或调整 CAAS 权重。全部策略、失败和种子结果均保留在 [results/scheduler-10a](../results/scheduler-10a/)。

## 1. 复现与样本

```powershell
Set-Location D:\competition\FutureCup
.\.venv\Scripts\python.exe -B scripts\bench_scheduler_10a.py --seed 2026111201 --seeds 20 --tasks 64 --output results\scheduler-10a-reproduction
```

输出目录必须在 FutureCup 内且尚不存在；原交付目录 `results/scheduler-10a` 已存在，不覆盖它。仅需项目 Python、Node.js 和已安装的前端 ECharts，无需模型凭据、服务启动或新增依赖。

20 个种子 `2026111201..2026111220`，四类场景、三策略，每次 64 个评价任务：**240 次主试验、15,360 个任务**。每次另行重复验证，合计 480 次执行；重复运行不作为新增独立统计样本。每次三策略共享完全相同的任务、初始声明、预热结果，哈希逐项一致。每个种子的策略执行顺序循环轮换，没有按结果选择更好一轮。

配置在试验前写入 configuration.json；全部任务、潜在模拟参数保存在 workloads.json。未根据观察结果换种子、调权重或筛场景。

## 2. 真实策略复用与公平性

直接导入生产 `assess`、`effective_priority`、`FixedSelection`、`RoundRobinSelection`、`CAASSelection`。源码 SHA-256 保存在 raw-trials.json，生产调度应用/领域文件已与冻结提交逐字核对一致；测试检查函数对象身份和真实评分重算。

- fixed：生产实现为固定候选 ID 次序，选择当前最低 ID 的合格 Agent，忙或不可用时允许其他合格 Agent。没有弱化成只使用一个 Agent 或强制串行。
- round_robin：生产实现按上次选择 ID，在当前合格集合中前进并回绕。
- caas：最高生产评分，按 ID/版本确定性打破平局。
- 三策略使用相同的过滤、容量、可用性、优先级和等待补偿规则，基线也看得到同样画像。

原权重为匹配 0.30、可靠性 0.25、负载 0.20、成本 0.10、延迟 0.15，先验 5 个样本、成功率 0.5，aging 每秒 5 分，均未修改。成本为相同的人工相对单位，不是人民币。

**范围是生产决策内核，不是完整端到端服务**：使用确定性离散事件资源驱动器，未运行 SchedulingCoordinator 异步 pump、Runtime、WorkflowEngine、SQLite 或网络。虚拟资源模型不证明实际多线程/多进程认领原子性；完整引擎模拟的 05 结果另行保留。

## 3. 负载模型

三 Agent 都声明支持 analysis/design/review，技能、成本、声明延迟相同。各 Agent/类型有相同的 4 个独立预热任务，共 36 个；评价时只在实际完成后更新自己的观测历史，不提前载入未来或反事实结果。生产评分看不到私有潜在参数。

| 场景 | 能力与故障 | 到达、容量 |
| --- | --- | --- |
| A 均匀 | 各 Agent/类型成功概率 0.94，基准时长 50ms | 每 10ms 到达；Agent 各 2，全局 6 |
| B 异构 | 每 Agent 一个随机置换的专长，专长 0.96 / 35ms，其他 0.65 / 70ms；不公开专长标签和概率 | 同 A |
| C 动态故障 | 正常同 A；随机一 Agent 在 [180,420)ms 降为 0.10、时长翻倍，[260,360)ms 不可用，420ms 恢复 | 同 A；仅当前不可用状态公开 |
| D 并发波动 | 成功概率、时长同 A，没有 CAAS 专属质量优势 | 每 150ms 突发 16 任务，优先级 20/50/80；容量随机置换 1/2/2，全局 4 |

所有时长乘 `0.8+0.4×U`。U 与成功抽样由 SHA-256(seed, task_id, agent_id, draw_kind) 构造，与策略调用顺序无关。相同任务—Agent 对潜在随机数相同，实际结果在选择之后查询；故障按实际开始时刻生效。预热使用不同 task_id。

同刻先完成、再到达、再认领；按生产 effective_priority / ready_at / task_id 排序。无候选时等待，到达后 2000ms 仍不能分配则失败。没有重试或 Self-Loop，失败及未分配任务都进入统计分母。独立种子使用独立 RNG/哈希流设计，不宣称从有限样本数学证明随机流独立性。

## 4. 实际数据

每格 20 种子、1280 任务。延迟为**逻辑毫秒**；P95 是每种子的最近秩 P95 再取均值，并非全部样本合并的 P95。主机调度开销单独实测，不加入逻辑时间。

| 场景 | 策略 | 成功率 | 平均延迟 ms | P95 ms | 调度轮开销 ms | 容量超限 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| A | fixed | 94.22% | 49.966 | 58.765 | 0.0352 | 0 |
| A | round_robin | 93.91% | 49.974 | 58.815 | 0.0370 | 0 |
| A | caas | 93.67% | 50.015 | 58.809 | 0.0362 | 0 |
| B | fixed | 74.92% | 65.729 | 91.757 | 0.0293 | 0 |
| B | round_robin | 75.70% | 64.798 | 91.138 | 0.0292 | 0 |
| B | caas | 77.03% | 60.679 | 88.158 | 0.0311 | 0 |
| C | fixed | 89.22% | 58.490 | 95.348 | 0.0305 | 0 |
| C | round_robin | 88.75% | 58.340 | 93.515 | 0.0316 | 0 |
| C | caas | 89.14% | 59.061 | 97.019 | 0.0301 | 0 |
| D | fixed | 93.75% | 201.055 | 517.515 | 0.0248 | 0 |
| D | round_robin | 93.67% | 200.134 | 516.343 | 0.0243 | 0 |
| D | caas | 95.16% | 199.506 | 511.550 | 0.0244 | 0 |

各指标跨种子的均值、样本 SD、95% CI 见 [summary.json](../results/scheduler-10a/summary.json)；逐种子指标见 [trial-metrics.csv](../results/scheduler-10a/trial-metrics.csv)，包含失败率、等待时间、吞吐量及故障恢复，没有只输出较好的指标。

### 负载分配与故障恢复

Agent-0/1/2 累计分配数（每行每策略共 1280）：

| 场景 | fixed | round_robin | caas |
| --- | --- | --- | --- |
| A | 473 / 432 / 375 | 440 / 420 / 420 | 428 / 427 / 425 |
| B | 449 / 432 / 399 | 428 / 431 / 421 | 433 / 420 / 427 |
| C | 450 / 438 / 392 | 427 / 432 / 421 | 421 / 433 / 426 |
| D | 530 / 557 / 193 | 427 / 441 / 412 | 444 / 423 / 413 |

Agent 峰值不超过配置容量；全局峰值 A/B/C 为 6，D 为 4。raw-trials.json 保留逐 Agent busy_ms、峰值、分配时 active/capacity。D 容量不均，分配更均匀不等于资源利用必然最优。

只对 C：恢复后到达任务（arrival≥420ms）平均成功率 fixed **94.55%**、RR **94.09%**、CAAS **95.00%**。原故障 Agent 在恢复后开始的首个成功任务相对 420ms 的完成延迟分别 **82.452 / 81.001 / 83.185ms**，各 20 种子均有观测、无删失；CAAS 没有恢复更快。不是已失败任务重试或 Self-Loop 修复率。

A/B/D 恢复字段为空、不适用；summary 的 recovery_censored_seeds 空值计数只在 C 解释为删失，不把非故障场景计作恢复失败。

## 5. 统计推断

以种子为独立分析单位，不能把同种子任务或确定性重复当作独立样本。固定统计 RNG、2000 次 percentile bootstrap 给出均值 95% CI；配对差先在同种子相减，再重采样。

预定主指标 success_rate / mean_latency_ms，4 场景 × 2 基线 × 2 指标 = 16 项。双侧精确配对符号检验去掉平局，Holm 校正全部 16 项，α=0.05。符号检验针对胜负方向，不与“均值差”检验等同；未校正 CI 不控制整体家族错误率。

B 中 CAAS 对 fixed 延迟差 **−5.050ms**，配对 SD 10.971，95% CI [−9.882,−0.740]；对 RR 差 **−4.119ms**，SD 6.875，CI [−7.029,−1.300]。符号检验原始 p 均 0.04139，Holm 后均 **0.66223**，不显著。B 成功率差 +2.11 / +1.33 个百分点，CI 均跨零。

**16 项校正后全部不显著。** 部分未校正 CI 不跨零不等于全局显著；未显著也不证明等价。完整差值、CI、非平局种子数与 p 值见 [paired-comparisons.csv](../results/scheduler-10a/paired-comparisons.csv)。不对主机开销作显著优胜宣称。

## 6. 05 对照与局限

原 05 JSON 只读提取全部 21 组汇总及文件哈希到 [reference-05.json](../results/scheduler-10a/reference-05.json)，原件不改。7 类场景、3 种子、63 次试验、1512 个评价任务，含无候选任务：

| 05 总体 | fixed | RR | CAAS |
| --- | ---: | ---: | ---: |
| 成功率 | 79.96% | 79.96% | 79.56% |
| 平均墙钟延迟 ms | 339.250 | 342.589 | 340.911 |

旧 CAAS 总体成功率更低；旧失败场景较好、其他部分场景不如基线，没有隐瞒，详见 [scheduler-05.md](scheduler-05.md)。05 经完整 WorkflowEngine/Runtime/asyncio/SQLite 测墙钟，10-A 是逻辑时钟内核基准，场景与资源亦不同，**禁止相除宣称跨阶段提速**。

局限：仅四个合成分布、有限种子、少量预热；无真实 LLM 质量、Token/费用或可信验证质量测量。虚拟容量不证明生产并发原子性；故障模型不等于系统崩溃恢复。A/D 中成功率差主要来自有限样本分配到不同随机结果，不说明 Agent 真实质量不同。主机开销受系统负载、Python/OS 热状态影响，不要求逐位复现。

本设计中技能匹配和声明成本相同，主要考察历史可靠性、延迟与负载适应，不覆盖稀缺工具/技能匹配或成本差异的全部作用。这里的成功为模拟模型的二元结果，不等于 VerifierEngine 对真实业务产物的质量验收。一次调度轮指一个待调度任务的候选评估（包括等待重查），排序时间摊入总开销。

## 7. 文件、图表和验收

- 入口：scripts/bench_scheduler_10a.py；测试：backend/tests/test_scheduler_benchmark_10a.py。
- 图表：benchmarks/render_scheduler_10a.mjs，复用既有 ECharts 离线 SVG，无额外依赖。
- 原始评价任务：[raw-tasks.csv](../results/scheduler-10a/raw-tasks.csv)；完整试验及每次候选评分/拒绝原因/画像版本：[raw-trials.json](../results/scheduler-10a/raw-trials.json)。
- 配置/输入：configuration.json、workloads.json；汇总：summary.json、report.md；文件校验：manifest.json。
- 报告图：[成功率](../results/scheduler-10a/success-rate.svg)、[平均延迟](../results/scheduler-10a/mean-latency.svg)、[P95](../results/scheduler-10a/p95-latency.svg)。柱为均值，须线为 95% CI，点为种子；图上标记模拟，未做浏览器级视觉验收。

主机开销使用 perf_counter_ns，包括优先级排序、构造画像、过滤和选择，不包括 JSON/CSV 落盘、SQLite、网络或 Runtime 认领。两次执行开销都保留于 raw-trials 的 wall_clock/repeat_check；没有按开销选择更好一轮。确定性 SHA-256 排除墙钟开销，包含全部逻辑结果和分配解释。

初始基线 557 通过、1 失败：既有本地验证器修改移除了 Schema rule/schema_path。经用户明确授权仅补回两字段，其他原改动保留；没有删除/跳过/弱化测试。最终后端 **572 passed**（原 558 + 新 14），0 跳过；前端 **26 passed**；TypeScript、Vite 生产构建、compileall、pip check 均通过。保留原 Starlette/HTTPX 弃用警告，报告 `.data/10a-pytest.xml`。历史六个 SQLite 文件 SHA-256 未变。

可用于比赛报告的结论：**“在固定四类合成负载、20 个种子中，生产 CAAS 决策内核在异构能力场景观察到较低平均延迟，在均匀和动态故障场景没有一致优势。容量模型未超限；预定多重比较校正后无显著优胜证据，不能外推到真实模型或生产性能。”**
