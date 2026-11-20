# 目录与操作指南

```text
backend/src/futurecup/
  domain/       Agent、工作流、调度、验证、模型与预算类型
  application/  Runtime、Planner、Workflow、Scheduling、Verifier、Feedback、SelfLoop及端口
  adapters/     SQLite、LLM、知识检索、解析、工具和证据适配
  api/          普通工作台、专业API及独立只读回放
backend/tests/  无网络自动化与明确标识的业务/协议夹具
frontend/src/   Vue工作台、编排器、知识库、监控与审计
config/         无密钥配置示例及模拟业务规范
scripts/        安全启动、离线预检和实验入口
results/        公平模拟实验原始数据/图表
docs/           各阶段设计、边界和复盘
competition-materials/ 本次技术素材与脱敏摘要
.data/          私有数据库、备份及完整证据（不进Git）
```

默认Mock启动，两个终端保持开启（端口已有服务不要重复启动）：

```powershell
cd D:\competition\FutureCup
$env:FUTURECUP_ENABLE_REAL_LLM='false'
$env:FUTURECUP_LLM_PROVIDERS_JSON='[]'
$env:FUTURECUP_DATABASE_URL='sqlite:///.data/workspace-12b.sqlite3'
$env:FUTURECUP_KNOWLEDGE_DATABASE='.data/knowledge-12b.sqlite3'
.\.venv\Scripts\python.exe -m uvicorn futurecup.api.app:app --host 127.0.0.1 --port 18014
```

```powershell
cd D:\competition\FutureCup\frontend
$env:FUTURECUP_API_TARGET='http://127.0.0.1:18014'
npm.cmd run dev -- --port 15184 --strictPort
```

知识库页 `/#/knowledge`：创建/选择有文档的库→导入模拟规范→手动检索来源。工作台 `/#/workspace`：选择库→自然语言输入→Mock动态规划或明确模板→修改/确认→执行→验证和来源→复制/导出。专业功能在 `/#/professional`、agents、models、workflows、runs。

已有真实任务只读查看使用指向18013的15185工作台，最新任务直达 `/#/workspace/ws-503317d980434ff084a64810e019c401`；不要把15184的另一套执行数据库当成真实运行不存在。比赛只读15180/18010用已有replay脚本，见guides中的demo-guide。历史回放不产生模型费用。

真实模式须负责人核实价格、显式预算预检、原SecureString启动脚本和两阶段授权，详见guides/user-workspace-real-11b.md。禁止为演示自动开启真实调用。没有Key也能运行默认Mock平台。
