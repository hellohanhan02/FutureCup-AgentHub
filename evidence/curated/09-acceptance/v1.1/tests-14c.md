# 14-C：最终验收与文件清单

日期：2026-11-18。分支 feature/v1.1-selfloop，基线14-B `14f801946a01773b71e78b5c2533103bc705e975`。只在v1.1目录修改及测试，未操作原参赛项目、历史私有数据库、真实密钥或DeepSeek。

## 实际验收

| 检查 | 结果 |
|---|---|
| 完整后端 pytest | 790通过，0失败/错误/跳过，68.95秒；一条原有Starlette/httpx弃用提示 |
| 保留14-A RAG测试 | 25通过（包含于全量） |
| 保留14-B Adaptive CAAS测试 | 49通过（包含于全量） |
| 新增14-C精准恢复测试 | 45通过（包含于全量） |
| 并发/取消/容量/认领/覆盖/重放专项 | 22通过、97未选中；这是独立的关键字专项，全量没有跳过 |
| 前端测试 | 10个测试文件、59项通过；新增复用来源/无假执行卡片测试 |
| TypeScript vue-tsc | 通过 |
| 前端生产构建 | 通过，Vite 6.4.6 |
| Python compileall | backend/src、backend/tests、两个新评测脚本通过 |
| pip check | No broken requirements found |
| npm ls --depth=0 | 通过，无新增依赖 |
| npm audit --offline | 缓存返回0项；未验证最新漏洞库 |
| 最终离线对照 | 640条；64条独立重跑的规格/指标/引擎哈希全部一致 |
| 跨运行操作证据 | 六类全部验证通过、源记录不变、容量归零 |

Python3.11.9、Node24.11.0、npm11.6.1。没有配置mypy/Ruff，不把compileall当作静态类型检查。没有再次进行浏览器人工验收；本阶段前端为组件回归及构建验证。机器名等本地JUnit字段不进入公开摘要。

## 新增测试覆盖

- 真实格式/Schema/业务证据归因，RAG缺失/引用/冲突、验证器ERROR不可伪装通过。
- 401/402/404/429/500的Mock HTTP类别与动作；元数据白名单丢弃认证、原正文和URL等测试字段。
- 原Prompt/Agent重选策略兼容，修复成功和失败，调用与反馈耗尽后停止。
- 正确复用已验证上游，显式/知识版本变化的传递失效，不复用失败产物或过期反馈。
- 并行单支失败、条件跳过/汇合、循环/子工作流新子ID，取消与多运行隔离。
- 幂等single-flight、参数冲突、取消后不确定状态、参数及结果可变对象隔离；未知/不可逆副作用人工处理。
- Mock源切换真实Provider拒绝，simulation_only通过预算父链在Provider.validate/infer之前生效。
- 同请求幂等、源唯一认领跨服务实例/新SQLite连接保留、预算与原deadline继承。
- API显式授权、修订冲突、历史接口兼容；评测终止参考表同时接受真实旧/新停止证据。

全部保留原745项后端测试；没有删除、跳过或弱化断言。14-C自身新增测试中的人工等待改为充足时间并等待真实票据后拒绝；普通工具超时与专门短超时故障分离，避免机器调度抖动伪装业务故障。

## 文件分组

- 领域/端口：domain/models.py、domain/verification.py、application/ports.py，增加默认兼容字段和可选预算/工具协议。
- 新服务/API：application/diagnosis.py、application/local_recovery.py、api/recovery.py；api/app.py挂载。
- 集成：application/feedback.py、selfloop.py、verification.py、runtime.py、workflow.py、scheduling.py；没有修改adaptive.py或原CAAS权重。
- 适配器：adapters/tools.py、workflow_store.py、knowledge.py，分别处理重放、持久认领和知识快照一致性。
- 前端：frontend/src/pages/RunDetail.vue、src/types.ts、tests/components.test.ts，复用来源/诊断/审计展示。
- 后端新测试：backend/tests/test_precise_recovery_14c.py。
- 免费工具：scripts/bench_selfloop_14c.py、scripts/check_local_recovery_14c.py。
- 文档：README.md、docs/architecture.md、docs/v1.1下本阶段审计、设计、评测、验收和JSON摘要。
- 数据：results/selfloop-14c下首轮、各修正阶段、最终640条、64条重复核验、六类跨运行原始记录、来源哈希和协议脚本存档。全部为合成数据，不含历史真实运行或私有库。

具体完整路径见本次Git提交。机器可读验收见 [acceptance-14c.json](acceptance-14c.json)；最终原始数据与代码哈希见 [评测报告](selfloop-benchmark-14c.md)。

## 复核命令

```powershell
Set-Location D:\competition\FutureCup-v1.1
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe -m compileall -q backend/src backend/tests scripts/bench_selfloop_14c.py scripts/check_local_recovery_14c.py
.\.venv\Scripts\python.exe -m pip check
Set-Location frontend
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
npm.cmd ls --depth=0
npm.cmd audit --offline
```

上述命令无需真实模型。离线实验不自动重跑，重放时请使用设计文档中的新输出目录参数。跨运行真实预算仍不可证明，接口继续拒绝该模式。下一阶段优先补持久对账与预算来源，再独立评审；本阶段不自动进入14-D。
