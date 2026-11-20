# 14-D 平台工程化与证据交付

本阶段只在FutureCup-v1.1完成，基于账本增强提交cd106e3。原RAG、四策略调度、Verifier/Feedback/Self-Loop核心算法保留。没有调用真实模型、读取真实密钥、连接原历史数据库、push或建立正式标签。

## 页面与组件

- ExecutionMap：实际WorkflowDefinition依赖、节点状态、真实时间、分支、复用/重执行标识；无模拟执行动画。
- SchedulingExplanation：候选资格、排除原因、需求快照、分项、动态权重、负载、有效样本、冷却、选择依据，历史缺字段显示未记录。
- KnowledgeCoverage：主题来源、COVERED/PARTIAL/UNCOVERED、初检/补检、注入片段、引用定位、规则遗漏和部分数值冲突证据；不把这些等同业务正确率。
- LedgerPanel：实际共享账户额度、预留、确认、UNKNOWN、剩余、工具状态、幂等摘要、重放次数和人工对账原因。
- RunDetail：运行/节点请求代次隔离和合并、受限Mock恢复、源与恢复关联、当前ID与版本校验后的脱敏报告导出。
- Workspace与Workflows：防重提交、迟到响应保护；沿用已实现的计划确认、选择器、模板、四策略和知识库入口。

工具逐次参数/结果来自实际Runtime记录，保存在尝试/节点快照，并可在“输入与最终结果→完整审计摘要”及导出中查看。精简账本不显示结果正文。未重新实施不必要的框架、聊天或流程引擎。

## API

| 接口 | 行为 |
|---|---|
| GET /api/v1/workflow-runs/{id}/observability | 实际图、审计、决策、账本、关联及恢复预检 |
| GET /api/v1/workflow-runs/{id}/report-export?expected_revision=N | 当前版本的脱敏因果证据，包含原始/子运行引用与记录；变化时409 |
| POST /api/v1/workflow-runs/{id}/recover | 复用原接口，显式授权、新Run ID、原预算/期限/工具安全最终校验；真实模式关闭 |
| GET /api/v1/platform/demo-capabilities | 仅独立Mock服务提供 |
| POST /api/v1/platform/mock-demo | 仅独立Mock服务，显式authorize_mock和持久请求认领，不重复执行同一请求 |

恢复预检不是授权；子运行要求在根级处理。预算快照不符不允许前端清零或更换账户。此界面不发送authorize_effects=true。普通服务不自动新增Mock演示入口或启用账本。

## 已实际执行的综合Mock案例

- 原运行：`4e318bf941fc4be5a307b0d91c6bc8c0`，FAILED、verified=false。
- 恢复运行：`0b56770332c84df2ac4c6614da978236`，SUCCEEDED、verified=true。
- 两个独立角色analyse→review，均经实际本地RAG和Adaptive CAAS；analyse真实调用安全计算器，产物映射到review。
- 首次review的模拟输出故意注入“遗漏关键规则”故障，Schema和数值检查独立执行，RAG规则体现验证失败；不是供应商或真实模型推理。
- 原运行安全终止。随后通过既有显式恢复API创建新运行，只重执行review，复用已验证analyse。新请求携带实际旧产物和验证失败证据，规则保持不变。
- 累计4次Mock模型调用、1次工具调用、3个节点尝试、1次恢复反馈额度。账本占用与总调用5一致。
- 模型未报告Token用量：reported_tokens=0、unknown_usage_calls=4。不能把0解释为真实供应商零Token或免费请求；该案例本身没有真实供应商调用。

证据：results/platform-14d/http-acceptance/下summary、原/恢复观测、导出报告及SHA-256清单。所有源代码与runner哈希由实际运行记录，不是反推过去构建。记录明确MOCK_ONLY_ACTUAL_ENGINE。

## 启动及操作

当前独立服务已在18031/15191启动，直接打开：

- 普通工作台：http://127.0.0.1:15191/#/workspace
- 运行监控：http://127.0.0.1:15191/#/runs
- 已完成恢复：http://127.0.0.1:15191/#/runs/0b56770332c84df2ac4c6614da978236
- 后端健康：http://127.0.0.1:18031/api/v1/health

服务关闭后，使用两个独立终端：

```powershell
# 终端A：保持开启。端口已占用会拒绝启动，不停止既有服务。
Set-Location D:\competition\FutureCup-v1.1
.\scripts\Start-Platform14D.ps1 -Port 18031
```

```powershell
# 终端B：保持开启；Vite只代理到独立Mock服务。
Set-Location D:\competition\FutureCup-v1.1\frontend
$env:FUTURECUP_API_TARGET='http://127.0.0.1:18031'
npm.cmd run dev -- --port 15191 --strictPort
```

启动器固定关闭真实模型、使用专属.data/platform-14d-live，并拒绝其他目录/未知数据归属。没有读取API Key。当前由后台服务运行，PID元数据在.data/platform-14d-checks/service-pids.json；不要凭陈旧PID停止其他进程。

人工验收建议：在运行监控点击“综合Mock验收案例”，等原运行失败；分别查看调度、知识覆盖和账本，再在原300秒期限内申请Mock局部恢复。恢复完成后确认analyse标为复用、review是新执行，并导出报告检查Run ID。旧记录期限耗尽时按钮禁用是安全行为，应显式创建新Mock案例，而非重写旧记录。

独立HTTP重放（新结果目录，禁止覆盖）：

```powershell
.\.venv\Scripts\python.exe scripts/run_platform_14d_demo.py --output results/platform-14d/manual-new
```

## 验收与截图状态

自动化：全量后端826项、前端68项通过。之后仅补充关联运行证据，6项API定向回归通过。TypeScript与Vite生产构建分别通过；Python编译、pip check、npm依赖树和离线缓存审计通过。原测试未删除、跳过或削弱。详见platform-checks-14d.md。

**浏览器验收未完成，无截图。** 浏览器宿主不可用，原生Edge尝试被安全机制阻断，原因是无法可靠确认Windows当前浏览器URL。已停止UI操作，没有换工具绕过，也没有生成替代截图。1366×768及1920×1080布局仅有响应式实现和DOM测试，仍需人工检查，不能声称浏览器双分辨率通过。

## 风险与后续真实验收

本地可信单用户环境，无完整RBAC或多租户隔离；请勿对外开放。账本UNKNOWN需对账，费用估算不是账单，真实模型跨运行恢复继续禁用。模拟故障修复不证明真实模型能力；真实模型验收仍须用户另行明确授权、价格和预算预检，不能从此Mock启动器打开。剩余首要工作是人工浏览器截图和交互复核，其后才评审真实联调，不自动进入付费阶段。
