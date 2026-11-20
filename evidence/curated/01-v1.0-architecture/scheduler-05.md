# 任务 05：Capability-Aware Adaptive Scheduling（CAAS）

日期：2026-11-11。范围：能力画像、在线执行统计、统一调度/认领、可解释记录、三种策略及可复现模拟实验。未调用付费 LLM，未实现可信验证或 Self-Loop。

## 1. 保护任务 04 与架构边界

开工检查确认工作区仅包含任务 04 的 24 个预期文件，无不相关修改；全部 132 项测试通过后建立本地提交：

`a5783cb23585ffe213fa591f3f6156d912a1716c feat: implement bounded multi-agent workflow engine`

任务 05 在原 WorkflowEngine 和 AgentRuntimeService 上增量集成，没有另建工作流或模型执行系统。任务 05 改动未提交，没有 push 或部署。原有 132 项测试文件未修改。

- WorkflowEngine：依赖、分支、循环、子流程、READY 判定和节点生命周期。
- SchedulingCoordinator：共同队列、任务排序、候选过滤、策略选择、解释和统计更新。
- AgentRuntimeService：所有入口共用一把准入锁；任务 ID 去重、容量检查和实际执行注册在同一临界区完成。
- SQLiteSchedulingRepository：在原工作流 SQLite 所有者上增加统计、观察和决策表，不破坏原表或 schema v1 快照。

新增 AgentSelectionPolicy 和 SchedulingRepository 端口；原 Scheduler.propose / StateStore 契约保留，未来可适配完整领域 Task。当前 SchedulingTask 是已 READY 节点的调度上下文，task_id 等于既有节点实例 ID，Runtime Execution.task_id 同步使用该 ID，不伪造验证配置。

## 2. 能力画像：配置、观察与实时状态分开

人工配置继续存于版本不可变的 AgentDefinition：

| 字段 | 说明 |
| --- | --- |
| skills / task_types | 技能与支持任务类型，旧 Agent 缺省 task_types=["*"] |
| tool_refs | 明确允许的工具及版本 |
| available / enabled | 临时可用状态、启用状态 |
| max_concurrency | 容量上限 |
| estimated_cost | 人工声明的相对成本单位，不是实际费用 |
| estimated_latency_ms | 可选人工耗时估计，没有估计则为 null |

这些字段通过原 Agent 创建/修改接口维护；修改仍发布新版本，不由一次模型成功自动“学会”技能或权限。

PerformanceStats 按 `(agent_id, agent_version, task_type, source)` 维护：successes、failures、cancellations、mean_duration_ms、revision、updated_at。sample_count=successes+failures；取消单独统计，不进入可靠性及正常耗时均值。

来源严格隔离：

- observed_mock：实际 Runtime 执行的 Mock 通道结果；不是真实模型质量。
- observed_real：非 Mock Runtime 通道的执行结果，也不等于模型已通过可信验证。
- simulation：压力实验的模拟服务时延/失败通道；默认生产调度不会读取这一通道。

没有该版本/类型/来源的已收集样本时，经验成功率为 **null**，不会默认为 100%。低样本标记与 confidence 一起返回。新配置版本不会自动继承旧版本的“高成功率”。

CapabilityProfile 在决策时组合人工配置、当前启用/可用状态、真实 Runtime active/capacity、全局容量、工具和 Provider 可用性、对应历史统计。候选画像中没有 Prompt、凭据或任务内容。profile_version 组合配置版本、统计来源/revision、容量 revision 和当前可用状态；决策另保存完整安全快照，能还原当时使用的数据。

工作流提交时固定候选配置版本，运行中重新读取可用状态与负载。缩小容量会阻止新增认领，不抢占已经运行的任务。候选版本已固定，运行中新增 Agent 不会无声加入已有运行。

## 3. 统一硬约束与任务排序

所有策略首先共用相同过滤：

1. 工作流依赖已满足，只接受 READY 节点。
2. Agent 已注册、启用且当前可用，Provider 可用。
3. task_type 精确支持或有完整通配符 `*`。
4. required_skills 是配置技能的子集。
5. required_tools 的名称和版本均在 Agent 白名单，实际工具版本可用。
6. Agent 当前并发小于有效容量，Runtime 全局容量也未满。

每个不合格候选记录明确原因，如 skills_mismatch、tool_permission_mismatch、agent_disabled、agent_capacity。非法候选不参与评分，score=null。没有合格者时不能靠放宽权限或忽略依赖获得“成功”。

任务的共同有效优先级：

\[
P_i(t)=p_i+0.1\min(d_i,64)+a\max(0,t-r_i)
\]

p 为 0..100 的配置优先级，d 为直接后继数，r 为 READY 时间，a 默认 5 点/秒。三种策略使用同一优先级及 aging 机制，避免把队列规则差异混入 Agent 选择对照。先取 P 最大者，平局按 READY 时间及任务 ID。工作流层也按同一公式把本地 READY 节点送入有限执行容量。

aging 不封顶：允许长期等待任务超过新高优先级任务；测试中等待 22 秒的优先级 0 任务先于新优先级 100 任务获得释放后的容量。这里用预设 READY 时间测试逻辑，没有宣称真实等待了 22 秒。当前为非抢占调度，不保证过载下每个任务都能在 deadline 内完成；优先级全跨度补偿约需 20 秒，若配置更短等待上限则可能先超时。

## 4. CAAS 归一化评分与参数

合法候选的五项指标均在 [0,1]，值越大越优：

\[
S=0.30M+0.25R+0.20L+0.10C+0.15T
\]

| 项 | 公式与解释 |
| --- | --- |
| 能力 M | 0.5×任务类型匹配 + 0.5×preferred_skills 覆盖率；精确类型为 1，通配为 0.5；没有偏好技能时覆盖项用 0.5。required_skills 已先硬过滤 |
| 可靠性 R | `(successes + m/2)/(n+m)`，n=成功+失败，m 默认 5；冷启动 0.5，一次成功约 0.5833，不能直接判为高可靠 |
| 空闲度 L | `1-active/capacity`，真实实时计数；满载已被过滤 |
| 成本 C | `1/(1+estimated_cost/cost_scale)`，cost_scale 默认 1；未知成本为 0.5，不当成免费 |
| 延迟 T | `1/(1+estimated_ms/latency_scale_ms)`，scale 默认 1000ms；完全未知为 0.5 |

置信度 `c=n/(n+m)`。已有耗时样本时，预测延迟为 `c×观察均值+(1-c)×人工估计`，缺少人工估计时基准使用 1000ms；没有观察样本时保留人工估计或未知。它是可解释的收缩估计，不是训练出来的耗时模型。

权重范围均为 [0,1]、有限值、总和为 1；归一化尺度、先验样本强度和 aging 速率必须为有限正数。默认权重表达“能力/可靠性优先，同时考虑负载和资源成本”的设计取舍，未通过本次评估集调参，也没有证明为最优权重。

同一任务、同一候选快照和配置输入，CAAS 选择确定；平局使用 Agent ID/版本。决策保存 algorithm_revision=caas-v1 以及完整 configuration，不会用更新后的统计重算旧决策或事后更换执行者。

## 5. 三种策略与并发认领

| 策略 | 合法候选之间的选择 |
| --- | --- |
| fixed | 固定 Agent ID 优先顺序，选择当前合法候选中最小 ID；不把满载 Agent 当作唯一选择而闲置其他合法容量 |
| round_robin | 在当前合法候选 ID 上循环选择，维护上一次选择位置 |
| caas | 最大归一化得分，确定性平局规则 |

三个策略共用过滤、优先级/aging、准入、预算和审计。为统一解释，三者均计算并记录候选分数；基线忽略得分。因此实验中的调度开销是共同画像/审计管线的开销，不是三种 select 函数的纯 CPU 微基准。

选择后调用原 Runtime.submit，携带节点 task_id 和固定 Agent 版本。Runtime 在同一 admission_lock 内检查任务是否已认领、容量和当前可用状态，登记执行和容量并写入去重映射；直接 Agent API 也使用这把锁。过期容量快照不产生超配，认领失败会重新评估并留下解释。

相同调度任务重复请求返回同一个分配；同 ID 但不同输入/需求返回冲突。不同 Coordinator 共享一个 Runtime 时仍只能有一个认领者。执行完成释放容量；关闭 Runtime 会等待完成回调，避免终态报告已存在但 active 尚未清零。

原子性作用域是**单 Runtime、单进程/事件循环**。没有实现跨进程租约或跨机器 exactly-once；不能使用多个 Uvicorn worker 共享调度池。SQLite 审计与内存认领不是分布式事务，中断运行继续沿用任务 04 的显式中断策略。

## 6. 工作流配置、等待与在线更新

根 WorkflowDefinition.scheduling_strategy 为 fixed / round_robin / caas（默认）；子流程和循环 body 继承根策略。旧 agent_id/agent_version 节点保持为单候选池。

动态 TASK config 支持：candidate_agents、task_type、required_skills、preferred_skills、required_tools、priority、no_candidate_policy、schedule_timeout_seconds。candidate_agents 省略时固定提交时已注册的候选版本；显式空列表表示无候选，不偷偷补充其他 Agent。

缺少结构上合格候选默认立即 FAILED / scheduling_no_candidate；wait 模式允许等待可用状态变化。单纯容量满始终有限等待。默认调度上限 10 秒，从 READY 时间计；节点和根工作流截止时间继续生效。超时为 scheduling_timeout，不无限挂起。

等待分配时节点保留 READY，真正认领后才进入 RUNNING；快照增加 ready_at、assigned_at、waiting_ms、scheduling_decision_id 和实际 agent_ref。Runtime execution_id 继续用于原结果查询。取消会移除未分配请求或取消已认领执行，不留下可在之后偷偷启动的请求。

完成后通过 `complete(task_id)` / `observe(terminal_report, task_type, succeeded=...)` 更新统计。observe 会读取 Runtime 中真实存在的终态记录，以 execution_id 事务去重；不能用任意构造的成功报告增加样本，也不能修改原观察结果。结构解析失败可以记为失败；这仍是执行/结构口径，非可信质量。

自动统计覆盖经调度的工作流任务；直接 Runtime 调用若需统计，应显式接入 observe。本次实验 warmup 正是这样收集，背景负载则不加入评估统计。

## 7. 存储、隐私与 API

新增表：capability_observations（execution_id 唯一）、capability_statistics（版本/类型/来源）、scheduling_decisions。统计更新和观察去重在同一短事务完成。决策独立保存，后续画像更新不会改变旧分数。列表按 SQLite 插入 sequence 倒序；Windows 墙钟可能同值，前端不应用时间戳代替因果顺序。

每条决策含任务/运行/节点 ID、候选和拒绝原因、各评分分项与总分、选中 Agent、优先级及有效优先级、等待及调度耗时、配置/画像版本、结果状态和 sequence。没有任务正文、Prompt 或凭据。Agent 管理响应也不再回显 system_prompt；创建/修改输入及实际模型请求仍保留该配置。

| 方法 | 路径（/api/v1 前缀） |
| --- | --- |
| GET | /scheduling/strategies：策略名、公式参数和执行级统计说明 |
| GET | /scheduling/agents?task_type=analysis：安全画像列表 |
| GET | /scheduling/agents/{id}?task_type=...&version=...：画像、冷启动/低样本及置信度 |
| GET | /scheduling/agents/{id}/history?version=...：按类型/来源分开的历史 |
| GET | /scheduling/decisions?task_id=...&run_id=...&limit=100：过滤决策，limit 为 1..1000 |

配置更新继续使用原 Agent PUT 创建新版本，策略切换通过发布工作流新版本。没有开放 HTTP 接口任意伪造执行观察或改写成功率。原 Workflow API 和健康端点兼容。

真实 Uvicorn/Vite 验收：三种策略都通过代理提交 202 并成功执行；画像/决策/历史/策略查询 200；CAAS 的技能偏好选择 agent-b，fixed/RR 本次选择 agent-a；禁用候选记录 agent_disabled。验证调度响应不含测试私有 Prompt 和任务正文，执行 task_id 与节点实例 ID 相等，结束后 active=0。

本地证据：`.data/scheduler-05-http.json`、`.data/scheduler-05-http.sqlite3` 和对应 backend/vite 日志；均不提交 Git。

## 8. 可复现模拟实验

命令：

```powershell
cd D:\competition\FutureCup
.\.venv\Scripts\python.exe benchmarks/run_scheduler_benchmark.py --seed 20261111 --tasks 24 --repeats 3
```

7 类场景 × 3 策略 × 3 重复 = 63 次工作流，1,512 个评估任务，每策略 504 个。相同场景/seed 的声明、任务和候选约束 SHA256 一致；对全部重复验证，而非仅第一组。

场景为能力相近、能力不同、混合优先级、动态背景负载、执行失败、并发、无合格候选。三 Agent 容量均为 2，工作流并发上限 8。策略按预先规定的轮换顺序运行，平衡先后顺序影响。每次试验重建状态，使用同一权重，不根据结果调参。

任务/Agent 组合的潜在失败与时延扰动由 seed+task_id+agent_id 的 SHA256 确定，只存在 Mock Provider 中；调度器看不到潜在失败率或未来结果。部分场景先对所有 Agent/类型各执行两条相同 warmup，ID 与评估任务分离；所有策略得到相同 warmup 任务。warmup、配置发布和背景任务不进入评估样本。

统计口径：

- 成功/失败率：所有评估节点为分母，包含无候选和失败，不筛选成功样本。
- 延迟：READY 到节点终态，包含工作流排队、调度、执行和状态处理；P95 使用全部重复合并后的 nearest-rank。
- 吞吐：终态任务总数/试验 makespan 总和；另报成功吞吐，避免把快速失败当作有效产能。
- 等待：READY 到分配；未分配者使用其最终调度决策的等待时间。
- 负载分布：实际分配次数，包含被分配后失败的任务；运行中断言各 Agent 峰值并发不超过 2。
- 调度开销：记录决策的 elapsed_ms 不含日志写入；另累计所有规划轮次（含未新增日志的等待轮询）的计算/审计时间，汇总表使用后者的每轮均值。它不是单纯算法 select 耗时。
- makespan 覆盖 engine.run 的预检与运行，排除 warmup；延迟单独从 READY 计，因此二者不应相互替代。

固定种子使工作负载及潜在 Agent/任务结果矩阵可复现。真实 asyncio/Windows 时序、实测耗时和在线画像会改变后续选择，墙钟数值及最终分配不保证逐位重现。模型时延仅几毫秒，SQLite 快照、事件和 Python 调度开销会显著影响结果。

### 最终代码实际运行汇总

以下为最终版本重跑结果，包含每策略 72 个预期无候选失败任务；不是 LLM 基准。

| 策略 | 成功率 | 失败率 | 平均延迟 ms | P95 ms | 终态吞吐 /s | 成功吞吐 /s | 平均等待 ms | 每规划轮 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 79.96% | 20.04% | 339.25 | 619.16 | 38.22 | 30.56 | 238.43 | 0.394 |
| round_robin | 79.96% | 20.04% | 342.59 | 626.19 | 37.86 | 30.27 | 240.18 | 0.402 |
| caas | 79.56% | 20.44% | 340.91 | 622.31 | 38.08 | 30.30 | 238.28 | 0.405 |

| 场景（每格 72 任务） | fixed 成功率 | RR 成功率 | CAAS 成功率 |
| --- | ---: | ---: | ---: |
| 能力相近 | 97.22% | 100.00% | 97.22% |
| 能力不同 | 100.00% | 100.00% | 97.22% |
| 混合优先级 | 97.22% | 98.61% | 94.44% |
| 负载变化 | 97.22% | 95.83% | 97.22% |
| 失败任务 | 68.06% | 69.44% | 72.22% |
| 并发任务 | 100.00% | 95.83% | 98.61% |
| 无合格 Agent | 0.00% | 0.00% | 0.00% |

| 策略 | agent-a 分配数 | agent-b 分配数 | agent-c 分配数 | 累计规划/审计 ms |
| --- | ---: | ---: | ---: | ---: |
| fixed | 168 | 139 | 125 | 301.14 |
| round_robin | 145 | 146 | 141 | 303.85 |
| caas | 136 | 144 | 152 | 304.70 |

CAAS 在本次失败场景表现较好，在其他部分场景及总体成功率上低于基线。三次重复和模拟数据不足以形成普适优胜或显著性结论；没有为了 CAAS 获胜改变基线、删去失败组或挑选较好的早期运行。

原始 `.data/scheduler-05-results.json` 包含全部 63 次试验、逐任务结果、所有指标、画像快照、决策、固定数据、潜在模拟参数（仅供复现实验）、源码哈希、Git 基点与依赖版本。Python 3.11.9、SQLite 3.45.1，沿用现有依赖，没有新增框架或安装全局软件。

## 9. 测试和工程检查

最终全部 pytest：**183 passed，0 failed，0 skipped**。原有 132 项源文件未改，新增 51 项：

- test_scheduling_algorithm.py：18 项，技能/类型/工具/可用性/容量过滤、冷启动、低样本、归一化、等待补偿、确定性和参数范围。
- test_scheduling_coordinator.py：14 项，统一策略、12 个重复请求仅一次执行、两个 Coordinator 竞争同任务、40 个请求竞争容量 2、等待/无候选、负载变化、观察去重、取消、12 次并发统计无丢失。
- test_scheduling_api.py：15 项，动态工作流、策略切换、画像与决策查询、隐私、参数、无候选和人工配置更新。
- test_scheduling_store.py：2 项，SQLite 历史与决策重启可查、样本不重复、结构失败不伪装成功。
- test_scheduling_benchmark.py：2 项，固定数据与抽样可重现、指标分母包含失败和未分配任务。

容量竞争测试实际只接受 2/40，peak_active=2，关闭后 active=0；同任务 12 次请求得到同一 execution_id，跨 Coordinator 竞争只有一个成功认领。原子性没有从此扩展宣称到多进程。

compileall、pip check 和原架构依赖边界检查通过。未配置 mypy/Ruff，不把编译说成完整类型检查。已有一条 Starlette/HTTPX 弃用警告保留。证据 `.data/pytest-05.xml`。

## 10. 文件清单

新增 12 个：

- backend/src/futurecup/domain/scheduling.py
- backend/src/futurecup/application/scheduling.py
- backend/src/futurecup/adapters/scheduling_store.py
- backend/src/futurecup/api/scheduling.py
- backend/tests/scheduling_support.py
- backend/tests/test_scheduling_algorithm.py
- backend/tests/test_scheduling_coordinator.py
- backend/tests/test_scheduling_api.py
- backend/tests/test_scheduling_store.py
- backend/tests/test_scheduling_benchmark.py
- benchmarks/run_scheduler_benchmark.py
- docs/scheduler-05.md

修改 13 个：

- backend/src/futurecup/domain/models.py
- backend/src/futurecup/application/ports.py
- backend/src/futurecup/application/runtime.py
- backend/src/futurecup/application/workflow.py
- backend/src/futurecup/application/workflow_validation.py
- backend/src/futurecup/adapters/memory.py
- backend/src/futurecup/adapters/workflow_store.py
- backend/src/futurecup/api/agents.py
- backend/src/futurecup/api/workflows.py
- backend/src/futurecup/api/app.py
- backend/src/futurecup/bootstrap.py
- README.md
- docs/architecture.md

原有示例、测试、前端和依赖声明未改；`.data` 的数据/日志继续被忽略，不提交运行结果或凭据。

## 11. 限制及下一阶段接口建议

- 单进程/事件循环内存容量与任务认领；SQLite 保存决策和统计，但不提供分布式租约、崩溃后 exactly-once 或自动恢复。
- Agent 定义和 Runtime 详细记录仍在内存，重启后需重新注册配置；旧决策和统计可保留。尚无归档、长期画像衰减或跨版本统计迁移。
- CAAS 为可解释启发式，不是学习了最优权重的模型；没有显式探索策略、全局最优保证或实时 SLO 证明，非抢占及短 deadline 仍可能使低优先级任务超时。
- 成功统计目前为 Runtime/已有结构口径；不代表业务答案正确、可信验证或真实费用。成本为人工相对单位；没有付费模型性能证据。
- API 仍限可信本地开发，无认证/租户隔离；没有高风险工具隔离或前端调度可视化。

下一阶段建议保持现有执行观察不可改写，增加独立 VerificationObservation（execution_id、verification_id、verifier_version、verdict、evidence_refs），以单独质量通道更新画像，避免把执行成功率与验证通过率混合。

失败重调度应创建新 attempt/task-instance ID，关联原节点、失败原因和 Verification ID；不得复用已认领 ID 重新执行。由 FeedbackController 在父工作流预算、权限和截止时间内提出动作，再调用本阶段统一队列/认领接口，保留旧决策、旧产物和因果链。当前仅预留 observe、SchedulingRepository、AgentSelectionPolicy 和稳定错误码，没有实现上述验证、重试或 Self-Loop。

本阶段完成后停止。
