# 评委快速体验指南

先按 README 安装并执行 Start-Demo。默认网页 http://127.0.0.1:15320/#/workspace，全部为免费 Mock，新数据库自动初始化；不需要原项目进程、私有数据库或 API Key。

1. **Agent**：左侧进入“Agent 管理”，查看 fc-researcher、fc-writer、fc-reviewer 三个不同角色，展开技能、模型和工具白名单。judge-calculator 只允许内置计算器。配置真实数据来自当前 API。
2. **多 Agent**：进入“工作流编排”，从已保存列表加载 `llm08-requirements`，查看 plan→design→review 的连线与 `nodes.plan.output`、`nodes.design.output` 映射。执行时填写 JSON：`{"requirement":"Design a campus marketplace","constraints":["local permissions","bounded recovery"]}`。预期三个节点顺序完成且配置的独立 Schema 通过。
3. **并行/条件**：加载 `fc-demo-parallel`，输入 `{"topic":"MOCK parallel review"}`；两个上游执行后汇合。加载 `fc-demo-branch-10b` 输入 `{"flag":true}`，另一分支应 SKIPPED，汇合不会永久等待。这两例未配置完整结果验证，执行成功时不应冒称全部结果已验证。
4. **知识库**：进入“知识库”，选择 `[MOCK] Judge campus business rules`，查看已导入的合成 Markdown 和分块。查询“订单取消与锁单”，点击“检索 Top 4”，预期出现十五分钟取消及释放锁定的来源片段。它不代表真实学校政策。
5. **普通用户协作**：回到工作台，保留 Mock，选择“软件需求与架构方案”，选刚才的知识库，输入“请根据业务规范设计校园二手交易平台，明确订单取消与锁单、信用评价和隐私保护”。可明确使用模板模式完成三个角色协作；动态 Mock 规划使用测试替身，不声称真正自然语言推理。点击“生成可审查的计划”，检查角色与依赖，再“确认计划并开始执行”。
6. **执行证据**：进入新生成的运行，查看节点输入输出、实际检索及引用、CAAS 调度决策。不要手工打开另一台机器验收记录里的 UUID；本机新运行 ID 由引擎生成。
7. **反馈修复**：加载 `fc-demo-selfloop`，输入 `{"request":"MOCK bounded repair"}`。预期首次 Schema FAILED，有具体反馈，第二次 PASSED；最多两次尝试、一次修复。此模型明确为脚本化 Mock，不是真实模型自发错误或修复。
8. **工具**：Agent 页面选择 judge-calculator，输入 `{"operation":"add","a":2,"b":3}` 执行；预期一次安全计算器调用，工具结果回到 Mock 模型。无任意 Shell 或 Python 执行。
9. **交付**：回到已完成且验证通过的工作台任务，查看“本次检索来源”，点击“导出 Markdown”或“导出 JSON”；应属于当前任务。源码的实际 deliveryMarkdown 函数已在新环境验证，浏览器下载按钮仍需人工点击验收。
10. **历史真实**：另开 replay 模式并访问 15322/#/demo，查看真实三 Agent 3次/966 Tokens 与 Self-Loop 2次/2119 Tokens；这些是脱敏只读历史，不是现场重新推理。不可通过该服务修改或重复执行。

## 增强研发演示（可选）

首次 Install 使用 -IncludeEnhanced，再 Start-Demo -Mode enhanced。复用已有综合 Mock 驱动：

```powershell
$env:PYTHONPATH=(Resolve-Path .\source\enhanced\backend\src).Path
.\.venv\Scripts\python.exe -B .\source\enhanced\scripts\run_platform_14d_demo.py --url http://127.0.0.1:19321 --output results/platform-14d/my-judge-demo
```

输出目录须从未存在，不重复跑同一案例。脚本返回原运行与恢复运行 ID；在 15321/#/runs 中查看 Adaptive CAAS、RAG、review 规则遗漏、analyse 复用及 review 重执行、工具/预算账本。4 次 Mock 模型和1次工具调用占用5个配额；不是人民币账单，也不是新的真实 RAG 闭环验收。

## 刷新、停止和冲突

页面刷新后从“运行监控/历史任务”进入当前记录。服务使用本目录独立 SQLite，重启后可以读取已保存任务。停止使用对应 `Stop-Demo.ps1 -Mode ...`，包装会检查 PID 与创建时间，不停止其他项目。若端口占用，换 BackendPort/FrontendPort，不杀其他进程。

本指南以已有组件/API、实际 HTTP 及导出函数验证为依据。浏览器组件不可用，本次没有伪造截图或声称完成点击验收；请负责人按上述顺序人工核对一次。