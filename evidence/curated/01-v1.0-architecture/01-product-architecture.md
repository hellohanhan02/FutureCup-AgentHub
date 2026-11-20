# 项目定位、系统架构与技术栈

项目面向复杂任务的可控多Agent协作：让普通用户输入需求、审查计划并取得可追溯交付；让专业用户配置角色、模型、工具、知识库和可执行工作流。目标赛道为用户指定的2026第五届未来精英创新大赛Agent编排创新应用。没有已验证商业客户、用户规模或落地机构，不填写此类指标。

技术栈：Windows本地环境、Python3.11、FastAPI、SQLite、Vue3/TypeScript/Vite、VueFlow、ECharts、jsonschema、httpx、pypdf。模型通过可替换Provider接入，Mock默认安全模式。知识库无需付费Embedding。

```mermaid
flowchart TD
 U[普通工作台 / 专业编排 / 只读比赛回放] --> API[FastAPI版本化API]
 API --> P[TaskPlanner: 受限动态计划与用户确认]
 API --> W[WorkflowEngine: 依赖和执行语义]
 P --> W
 W --> S[CAAS / Fixed / Round Robin]
 S --> R[Agent Runtime]
 K[独立SQLite知识库: 解析 分块 FTS5] --> R
 R --> L[Mock / DeepSeek / 兼容Provider]
 R --> V[独立VerifierEngine]
 V --> F[FeedbackEngine]
 F --> B[有界SelfLoop与共享预算]
 B --> S
 W --> A[SQLite快照 事件 尝试 验证 反馈]
```

分层方向：domain类型/状态 → application端口及流程 → adapters数据库/模型/工具 → api组合根。bootstrap选择具体实现。工作流管理执行顺序和跳过，调度器只处理合格候选和容量，Runtime只负责单Agent推理和受限工具执行；验证不由生成Agent自报成功决定。

只读replay为独立应用，加载已有脱敏证据包，无写入接口、无模型执行入口；普通/专业工作台共用真实引擎。SQLite知识库与执行记录分文件，历史真实证据不写入版本仓库。
