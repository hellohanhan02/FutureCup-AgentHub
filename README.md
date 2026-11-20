# 智枢 AgentHub · 评委可复现源码

默认推荐 **stable（v1.0.0）+ 免费 Mock**。本目录是独立公开源码候选，不含比赛 PDF/PPT、学生证明、原 Git 历史、密钥或私有数据库。原有参赛材料继续保留在上级交付目录，请勿将上级整个仓库发布为公开源码。

## 三步快速体验

要求 Windows 11、Python 3.11（验收为 3.11.9）、Node.js 22.12+ 或 24（验收为 24.11.0）、npm（验收为 11.6.1）。进入下载解压后的本目录：

```powershell
powershell -NoProfile -File .\Install.ps1
powershell -NoProfile -File .\Start-Demo.ps1
# 使用完毕，只停止本包装拥有的进程：
powershell -NoProfile -File .\Stop-Demo.ps1
```

打开 **http://127.0.0.1:15320/#/workspace**。无需密钥。后端为 http://127.0.0.1:19320。启动器自动注册已有 Mock Agent、演示工作流及合成知识规范，不自动提交模型执行任务。

若本机 `py -3.11` 注册失效，先安装或定位有效的系统 Python 3.11，再明确指定：

```powershell
powershell -NoProfile -File .\Install.ps1 -PythonExecutable '你的Python3.11安装目录\python.exe'
```

不要指定原研发项目的 `.venv`。如 PowerShell 策略阻止脚本，请按本机正常授权流程处理，不绕过组织的安全审批。安装每步最多 300 秒、失败不自动重试；详细日志在 `.validation/`。已有 `.venv` 时安装器会要求先检查，而不会偷偷复用或重复安装。端口冲突时不停止其他项目，可改用 `Start-Demo.ps1 -BackendPort 19330 -FrontendPort 15330`。

完整逐页操作见 [评委快速体验指南](JUDGE-QUICKSTART.md)，真实配置见 [API-CONFIGURATION.md](API-CONFIGURATION.md)。

## 能力与版本

自然语言规划经用户确认后进入 WorkflowEngine；支持顺序、并行、条件、受控循环与子工作流；CAAS 进行能力/负载感知分配；Runtime 调用模型和白名单工具；独立验证与有界 Self-Loop 保存失败、反馈和修复证据；本地知识库支持文档解析、FTS5/BM25 检索、上下文注入和来源校验。

技术栈：Python 3.11、FastAPI、SQLite、Vue 3、TypeScript、Vite。应用与外部模型、工具、存储通过接口解耦。没有引入新的核心算法或大型部署框架。

- `source/stable/`：稳定参赛版，来源 `bb8c418baaa7e9dbf210d4aae6895f2e95bf9e3b`。
- `source/enhanced/`：来源 `dd862fa787fe705a56e3f87f8816428e060423fb`，含 Adaptive CAAS、覆盖检索、精准恢复及 Mock 预算/工具账本。属于研发增强，不宣称最新真实 RAG 完整闭环已通过。
- `deployment/`：仅交付安装、启动、停止、演示初始化及小型验收包装。
- `evidence/`：脱敏回放、模拟实验和原许可证；`acceptance/`：本次全新验收结果。

两版 Python 基础依赖相同，前端独立按锁文件安装。根安装脚本默认只安装 stable；首次就需要增强版时运行 `Install.ps1 -IncludeEnhanced`。只读回放使用 stable 前端依赖。

## 三种独立模式

```powershell
# 已安装增强前端后，独立 Mock 研发演示：
.\Start-Demo.ps1 -Mode enhanced
# http://127.0.0.1:15321/#/runs
# 真实历史只读回放，不调用模型、不使用原数据库：
.\Start-Demo.ps1 -Mode replay
# http://127.0.0.1:15322/#/demo
# 分别关闭：
.\Stop-Demo.ps1 -Mode enhanced
.\Stop-Demo.ps1 -Mode replay
```

现场 Mock 与历史真实回放必须区分。历史包仅提供两个可交互真实案例；其他真实案例使用脱敏摘要及成果文件，不伪造新的历史记录。用户自己的真实 API 模式另由 `Start-Real.ps1` 隐藏输入开启，启动不推理，必须在网页再次选择预算并授权任务。

## 本次验收与限制

全新独立 Python 环境依赖安装、两版 npm ci、pip check、12 项 Mock 流程检查、两版各 10 项本地 HTTP Provider 协议检查通过；增强 Mock 恢复及只读历史可复现；实际前端导出函数生成 Markdown，并验证了停止/重启后的任务读取。没有借用原项目环境或数据库。详细耗时、证据和范围见 acceptance。

浏览器访问组件不可用：本次未完成新的真实浏览器点击/下载验收。HTTP 与前端导出函数检查不能替代这项人工验收。没有重跑完整研发测试或大型实验，没有调用真实模型。

Mock 输出只证明执行链，不证明真实模型推理质量。来源存在、引用有效或结构验证通过不等于完整业务正确；旧校园真实成果曾遗漏十五分钟规则。FTS5/BM25 是关键词检索，不是向量语义检索。模拟逻辑毫秒和费用不能宣传为真实生产性能。10-A 经 Holm 校正无显著优势，增强实验也有代价和负向结果。

## 测试、部署与公开

需要复核本包小型演示时，在 Start-Demo 之后使用本地 `.venv` 运行 `deployment/verify-demo.py`；它会生成或复用明确标记的 Mock 记录。`deployment/check-provider-local.py --version stable` 或 enhanced 仅请求本机模拟 HTTP 服务，使用合成凭据。不要把这些测试当成真实 API 验收。

所有服务只绑定 127.0.0.1，不提供生产多租户认证，也不应直接开放公网。真实跨运行恢复保持关闭，真实持久化账本未开放。UNKNOWN 不视为免费或可再次消费。

公开前必须由团队确认项目自身许可。根 NOTICE 不是新的开源授权；不能由依赖的 MIT/Apache 推导本项目许可。此前 122 条缺少本地许可证原文的记录，本次从同名同版本新安装包补齐 4 条，剩余 118 条详见 acceptance/license-pending.csv。跨版本记录不等于独立库数量。

GitHub/Gitee 操作见 [PUBLISHING.md](PUBLISHING.md)。本次只建立独立本地仓库，不创建远程或 push。**本地 localhost 不是官方要求的公网部署链接；负责人仍需创建可供评委访问的源码仓库并授权上传。**