# 持久预算、工具重放与崩溃恢复

开发基线：14-C `bda37c96ee81b8db3155aa733a90571ccdfba13e`。仅在v1.1独立副本开发，无新依赖、无真实模型请求、无历史数据库改写。默认Runtime、专业/普通工作台与只读演示没有自动启用新账本。

## A. Budget Reservation Ledger

独立SQLite中的ledger_accounts以account_id和owner_id双重唯一绑定预算。重复创建必须匹配原限制和单位；不能给同一owner换account_id或提高上限。每次实际调用生成稳定reservation_id，绑定run/node/execution、调用种类、输入摘要和预留整数单位。

- reserve：BEGIN IMMEDIATE内检查容量并插入，重复相同ID和参数只读取原记录；参数变化拒绝。
- dispatch：在外部调用前持久标记，不能重复派发同一预留。
- commit：按证据ID和实际整数单位结算；同一终态证据即使换进程重试也不重复扣减。金额/证据变化拒绝。
- release：仅允许未派发的RESERVED；已派发、UNKNOWN或COMMITTED不可自动退回。RELEASED重复确认是无写入的幂等返回。
- recover：确认旧会话已停止后将其fence。未派发预留释放；已派发未结算转UNKNOWN并保持全额占用。

占用调用数 = 所有非RELEASED预留数。占用额度 = COMMITTED的actual之和 + RESERVED/UNKNOWN的reserved之和；未知调用不按零计算。派发前的预留与执行进程仍有间隙，因此dispatched只代表“可能已经发送”，不能宣称请求必然到达供应商。

支持resource_units和estimated_micro_cny两种账户单位。后者是经调用方明确配置的人民币估算整数微元，不是供应商账单，也不会自动获取价格。Runtime当前只绑定resource_units，沿用原调用次数/输入长度/输出Token/费用保护；不将资源单位冒充费用。已有真实CNY预留机制未被替换。

## B. Tool Replay Ledger

保存tool_call_id、run_id、node_id、tool_name/version、input_hash、side_effect_class、STARTED/SUCCEEDED/FAILED/UNKNOWN、result_digest、限长结构化结果及worker/session归属。另存每次调用到原始工具操作的引用。

不可变身份由账户、重放scope、工具名称和版本约束，输入变化拒绝；无副作用工具按独立调用记录。同一非纯工具scope只允许一个逻辑操作，不能将两次本来不同的外部写入自动视为同一业务操作。

工具成功时，结果/摘要和预算COMMITTED在同一SQLite事务完成；结果持久化失败会回滚预算提交。之后才写原Runtime执行报告。已提交结果可以在新会话读取，不假装执行了新副作用。相同结果的完成确认可重试，其他结果拒绝。只保存输入哈希，不保存模型请求正文或工具参数；工具结果是私有数据，沿用Runtime敏感数据检查且限制64KB，不作为公开证据默认导出。

## C. Crash Recovery

worker_id是唯一进程会话标识。重启不会偷取仍可能活跃的会话。调用recover(dead_worker, confirmed_stopped=True)表示操作员/受控启动器已确认旧执行者停止；这是外部事实前提，不是靠PID猜测。fence阻止旧会话继续通过账本提交，但不能撤销外部系统已发生的副作用。

- 进程在工具执行中硬退出：原STARTED转UNKNOWN，原预算继续占用。
- unknown/irreversible工具：claim始终拒绝自动重放，需要外部对账、补偿或人工处置；新模块未提供直接强制放行开关。
- none/idempotent工具：必须显式authorize_idempotent，权限、工具版本、安全类别和参数哈希仍须匹配。BEGIN IMMEDIATE原子认领；并发请求只有一个会真正调用工具。
- 每个逻辑工具最多一次崩溃恢复派发。恢复时新建一笔物理调用预留，旧UNKNOWN不退还；不足额度直接拒绝。不能以“幂等”推定没有再次调用开销。
- 显式工具级恢复默认30秒超时、最多可配置300秒；专门短超时仅用于故障测试。超时保留UNKNOWN，依赖工具合作取消。该接口用于受控对账，不代表原工作流自动续跑；工作流恢复继续检查原deadline。
- 恢复再次崩溃或失败：保持未知占用，不能再换request_id无限尝试。
- 结果已在账本提交、但执行报告尚未保存：不重放外部操作，返回保存结果供显式核对；不会自动将原工作流标为成功或已验证。

依赖工具真实满足幂等声明或在外部系统使用稳定幂等键；本地日志不能保证外部非幂等操作exactly-once。没有新增危险工具、Shell或任意代码执行器。

## D. Reconciliation

reconcile返回受控code、record_id和manual_review动作，基于一致SQLite事务读取，绝不修改预算、工具结果或工作流。支持：未知结算、执行记录缺少预留/预算缺少执行记录、输入/用量不符、重复执行证据、预算不变量损坏/超额、工具结果缺失或摘要错误、工具预算和调用状态不符。

observations中的state是对应模型/工具调用的状态，不是工作流总状态。某工具成功后整体工作流因其他节点失败并不构成错误。只有明确声明complete_inventory=True，才把缺失的执行记录判为缺口；部分查询不等于不存在记录。诊断不会静默补造结果、修改验证状态或猜测退款。

新预留和恢复认领检测到损坏预算记录即拒绝继续分配。LocalRecoveryService只能恢复同一budget_account_id，并要求原预算快照的总调用、模型/工具分项、资源使用和限制一致；缺少账户或存在偏差时返回明确诊断，不能换新账户清零。

## 现有引擎接入与操作

显式程序配置示例（Mock、全新独立数据库；目录需由调用方选择在v1.1内）：

```python
from futurecup.adapters.recovery_ledger import SQLiteRecoveryLedger
from futurecup.application.durability import DurableExecution
from futurecup.domain.verification import ResourceBudget

ledger = SQLiteRecoveryLedger('.data/private-ledgers.sqlite3')
durability = DurableExecution(ledger)
runtime.durability = durability  # runtime为项目原AgentRuntimeService实例
budget = ResourceBudget(max_calls=6, max_resource_units=100000,
                        max_feedback_loops=1)
durability.bind(budget, 'unique-task-account', 'unique-task-owner')
# 在原WorkflowEngine.submit(..., shared_budget=budget)中使用；仍由用户确认计划后执行。
# 关闭时先await runtime.close()，再ledger.close()。
```

Self-Loop的attempt预算携带真实run/node实例ID，Runtime记录reservation_id；工作流报告持久化budget_account_id。普通未验证节点也有独立归属。重开SQLite后可凭持久证明复用已成功的幂等工具，不依赖旧内存记录；结果缺失或损坏则拒绝恢复。原14-C跨运行恢复可继承账本，但要求快照一致。已使用账户不能重新bind一个零消耗预算；restore只核对既有快照，不猜测重写。

本次没有增加服务端环境开关、HTTP账本管理接口或新UI；不要求重启现有服务。要启用必须在独立Mock服务构造时显式注入。当前所有非模拟Provider在此模式下、在凭据校验之前即被拒绝。真实/未知Provider跨运行恢复继续禁止，没有因此开放付费恢复。

## E. 无网络测试

```powershell
Set-Location D:\competition\FutureCup-v1.1
.\.venv\Scripts\python.exe -m pytest backend/tests/test_durable_ledgers.py -q --basetemp=.data/pytest-ledger-manual-new
```

30项新增测试包括：reserve后硬退出、dispatch后commit前硬退出、外部替身完成但工具状态未保存、真实Workflow/Runtime工具提交后执行报告保存前os._exit、结果缺失/损坏、事务部分失败回滚、重复恢复、并发双连接认领及实际handler一次执行、幂等重放额外预算、取消/超时、丢失确认后的跨会话幂等、账户不重置、旧worker fencing、错误单位/历史库拒绝、原Self-Loop局部恢复继承账户。

故障进程只继承必要系统变量及本地PYTHONPATH/TEMP，使用独立合成SQLite和模拟外部文件，不读取真实密钥或调用模型服务。完整回归和源文件摘要见durable-ledgers-acceptance.json。首次全量发现领域哈希实现越过原白名单，已改为适配器实现/端口调用；保留原架构测试和失败报告，没有放宽断言。

## 边界

账本是可信本机SQLite实现，不是多主分布式事务、外部支付系统或防管理员篡改存储。恢复依赖确认旧会话停止；不会自动续跑整个进程，也不自动恢复内存中的Agent注册或Runtime执行记录。UNKNOWN需要独立对账，价格未知仍不得真实调用。预算与工作流快照不符时必须人工处理，不能通过新额度“修复”。私有账本文件按现有.gitignore排除，测试结果不构成真实LLM恢复成功证明。
