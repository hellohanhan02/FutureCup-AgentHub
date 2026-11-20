# 14-C：现有 Self-Loop 审计

审计基线：14-B `14f801946a01773b71e78b5c2533103bc705e975`。开发分支 `feature/v1.1-selfloop`，仅修改独立 v1.1 副本。未打开原项目数据库、读取真实密钥或发出真实模型请求。

## 中断恢复核对

恢复时分支和 HEAD 正确，已有源代码、测试、前端展示、实验脚本及首轮数据均保留。此前额度拒绝的复杂 PowerShell 命令所包含的 HTTP 证据传递、强化评测与对应新测试并未出现在磁盘；没有发现该批次局部写入。后续逐文件小补丁完成这些部分，未重试原命令。已保存的 745 项基线和 777 项中间 JUnit 记录仍位于忽略目录 `.data/`；最终为 790 项通过。

## 旧版真实能力与缺口

| 模块 | 14-B 已有能力 | 14-C 必要扩展 |
|---|---|---|
| WorkflowEngine | DAG 就绪推进、并发、条件/汇合、受限循环/子工作流、失败传播、取消、SQLite 快照/事件 | 显式创建新运行的局部恢复入口、验证证明绑定的复用、传递失效、来源审计 |
| SelfLoopEngine | 已在失败节点内重试，保留成功上游；不是全量重跑；新 attempt_id、隔离失败产物 | 可选精准归因、按来源限制恢复动作、副作用前置检查 |
| FeedbackEngine | 格式/断言/工具/超时等粗分类，真实产物和验证证据进入修复上下文 | 区分 Schema、业务、知识缺失、引用、冲突、权限/预算、服务与验证器故障 |
| 恢复策略 | Prompt 重试、兼容 Agent 切换、批准的工具子集切换、显式备用分支、人工票据 | 动作必须同时属于配置和诊断允许集；不盲目重试不可修复错误 |
| Runtime/调度 | 调用/工具/长度/超时限制、容量原子认领释放、取消传播、共享 ResourceBudget | 传递工具重放域；恢复预算强制 simulation_only，防止切换到真实 Provider |
| SQLite | 尝试、验证、反馈、工作流修订和事件 | 源运行唯一恢复认领，与新运行创建同事务；换 request_id 或服务实例不能重置预算 |
| 工具 | 白名单、Schema、异步执行、异常/超时 | 重试安全元数据、同域幂等 single-flight、不确定状态人工处理 |

旧接口默认 `precise=false`；无显式恢复请求时不改变旧版恢复策略。CAAS/Adaptive CAAS 的权重和决策内核、14-A 检索/覆盖规则没有修改。调度接口仅增加可选的重放域透传。

## 安全 HTTP 归因

复用现有 Provider 的脱敏整数状态码与 `HTTPErrorCategory`，不复制供应商正文。仅在精准模式下，从本次 ModelCallRecord 提取并再次白名单校验后交给 VerifierEngine 的 execution evidence。每轮清空临时字段，避免上一轮 HTTP 状态污染下一轮。

| 安全类别 | 归因 | 默认允许行为 |
|---|---|---|
| authentication_failed / 本地凭据拒绝 | PERMISSION_DENIED | 终止，不盲重试 |
| insufficient_balance | BUDGET_EXHAUSTED | 终止 |
| rate_limited / server_error | SERVICE_UNAVAILABLE | 在原修复预算内按配置重试、备用路径或人工 |
| model_unavailable | RESOURCE_UNAVAILABLE | 终止，等待配置修正 |
| invalid_request / unknown_http_error | 无充分依据时 AGENT_RUNTIME_ERROR | 人工或终止 |

不保存 Authorization、Cookie、密钥、原始响应正文、完整敏感 URL。此扩展不调整 Provider 自动重试次数、模型参数、价格或真实调用上限。Mock HTTP 测试覆盖安全状态归因，已有 Provider HTTP 回归全部保留。

## 错误责任与验证边界

- artifact 来源：输出格式、Schema、业务约束、引用缺失与规则冲突；修复建议依据真实验证记录。
- execution 来源：工具故障、临时服务、超时、权限等；不能直接解释为 Agent 能力不足。
- verifier 来源：ERROR / INCONCLUSIVE 进入人工或终止，不能当成业务验证通过。
- control 来源：共享预算耗尽、取消、整体期限、人工等待截止独立记录。
- 诊断包含证据 ID、产物摘要、规则/字段位置、可修复性、允许动作与策略版本。它解释故障类别，不是模型或业务正确性的完整证明。

不新增质量评分自动扣减逻辑，不把知识缺失、验证器错误、工具环境故障自动算作 Agent 能力下降。旧版调度执行统计与验证质量证据保持分开。

详细机制见 [精准恢复设计](precise-recovery-14c.md)，实验与限制见 [公平评估](selfloop-benchmark-14c.md)。
