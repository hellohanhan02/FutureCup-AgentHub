# 12-B 本地知识库集成

12-C 更新：同名空库误选的实际复盘见 [knowledge-retrieval-failure-12c.md](knowledge-retrieval-failure-12c.md)。当前空库在规划前拒绝、节点零命中停止，空引用不再算知识增强验证通过；列表显示文档数/版本/短ID。历史12-B测试及下文无匹配允许空引用的口径已被此安全修正取代。最新后端670、前端55项及类型/构建检查通过，未调用真实模型。

## 实现

SQLite 独立文件 `.data/knowledge-12b.sqlite3`，延迟打开，不修改历史执行数据库。知识库 UUID、名称、描述及递增版本；文档每次导入独立 UUID、版本 1、原始字节 SHA-256、解析/索引/失败状态；分块 UUID、文本 SHA-256、原文段落或 PDF 页码及字符偏移。删除停止新检索并清除索引/分块，保留元数据墓碑；已有执行审计快照不随资料删除，隐私彻底擦除另需专门机制。

TXT/Markdown 使用 UTF-8，DOCX 使用受限 ZIP/XML 文本提取（不执行宏、不解压到任意路径），PDF 使用项目虚拟环境的 pypdf 7.1.0。无文本/扫描 PDF 明确报错，不伪造 OCR。每文件 2MB、每库 30 文档、最多 100 PDF 页、20 万提取字符、400 分块；每块 700 字符、步长 600，跨块重叠 100。ZIP 限成员数、单项和总展开大小，拒绝路径穿越、加密和实体声明。不是任意恶意 PDF 的完整安全沙箱；本阶段适用于可信本机资料，解析仍在服务进程同步执行，生产环境需隔离和 CPU/内存限制。

检索为中文相邻字符和英文词预处理 + SQLite FTS5/BM25，支持库范围、Top-K 1～8、无匹配、分数/来源返回。分数是排序依据，不是概率；不是向量/语义检索。重建文档采用新导入 ID，删除旧文档后不再召回旧块。无收费 Embedding 或外部网络。

## Agent、上下文和验证

AgentDefinition/Agent API 新增 knowledge_base_ids（最多五库），普通工作台用户选择库后，编译器把用户授权范围绑定给任务 Agent；LLM 不能自行扩大范围。Runtime 每次执行从绑定范围真实检索，保存 query、命中库/文档/版本、chunk_id、定位、原文、分数及上下文哈希。检索数据作为独立 user 数据块，系统明确声明其不可信，不变成 system 指令、不扩展工具权限。原用户需求及上游产物仍作为独立任务输入传递。

检索块与输入共同检查 max_input_chars；整个模型上下文继续走 Provider 字节限额、Runtime 共享调用和费用预估，不能绕过预算。执行每轮重新检索，引用绑定这一轮实际快照；原 SelfLoop 尝试审计保存 retrieval_records，详情页可查看。引用校验不依赖事后当前索引，保证文档变化时仍能核对当时来源。

使用知识库的工作流输出新增 citations: [{chunk_id,quote}]。既有 VerifierEngine 调用可信回调，要求命中存在时至少一个引用、ID 必须属于本轮命中、直接引文必须是对应文本的非空子串。伪造、跨库、引文不符均失败，原 FeedbackEngine/SelfLoop 在原一次修复、调用/费用上限内处理。未命中时允许空引用，不伪造来源。引用验证不证明观点、推理、全部文档事实正确，也不能证明 LLM 从未使用预训练知识。

**权限边界：可信本机单用户。** 用户可管理本机所有库，Agent 仅能检索显式绑定库。不是多租户 RBAC，不应直接暴露公网；不把知道库 UUID 当作多租户鉴权。提示注入以角色隔离和无权限扩张控制，不能保证模型语义上永不受恶意文本影响。

## API

- GET/POST `/api/v1/knowledge-bases`：列表/创建。
- PUT/DELETE `/knowledge-bases/{id}`：编辑/删除。
- GET/POST `/knowledge-bases/{id}/documents`：状态列表/导入。
- DELETE `/knowledge-bases/{id}/documents/{doc}`：删除文档。
- GET `/knowledge-bases/{id}/documents/{doc}/chunks`：定位与分块。
- POST `/knowledge-bases/{id}/search`：query/top_k，单库范围。

上述简写均位于 `/api/v1`。导入 JSON `{name,content_base64}`，前端使用 File API 编码，服务端校验解码体积，不接受任意磁盘路径/URL。解析失败的文档记录保留 failed 与安全错误码。

## 使用与启动

当前独立 Mock 后端 18014、前端 15184；保留所有原服务。访问 http://127.0.0.1:15184/#/knowledge ，创建知识库，导入 `config/campus-business-rules-12b.md`，资料明确标注模拟，包含认证、禁售、锁单取消、评价申诉和隐私规则。可先检索“订单取消锁定”，查看来源。然后打开 `/#/workspace`，选择库、选择 Mock，输入三个协作任务，审查并确认计划。成果区查看来源片段，执行详情展开每轮实际检索/引用验证与反馈。专业 Agent 页面也可绑定库 ID。

终端一（仅新库和新执行数据库）：

```powershell
cd D:\competition\FutureCup
$env:FUTURECUP_ENABLE_REAL_LLM='false'
$env:FUTURECUP_LLM_PROVIDERS_JSON='[]'
$env:FUTURECUP_DATABASE_URL='sqlite:///.data/workspace-12b.sqlite3'
$env:FUTURECUP_KNOWLEDGE_DATABASE='.data/knowledge-12b.sqlite3'
Remove-Item Env:DEEPSEEK_API_KEY,Env:DASHSCOPE_API_KEY,Env:FUTURECUP_LLM_API_KEY -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn futurecup.api.app:app --host 127.0.0.1 --port 18014
```

终端二：

```powershell
cd D:\competition\FutureCup\frontend
$env:FUTURECUP_API_TARGET='http://127.0.0.1:18014'
npm.cmd run dev -- --port 15184 --strictPort
```

两个终端需保持开启；端口已运行则不重复启动。没有浏览器人工视觉验收声明。

后续真实测试仍由用户按 11-B 安全启动/已核实价格/预算预检流程手动授权，先停止并升级专用 18013，保留原 18008 和只读演示。服务端配置相同知识库路径，在 15183 选择实际库；先授权真实规划，再确认执行。检索本身免费，但注入内容增加模型输入费用，仍须预算预检。Key 只走原隐藏输入脚本，不输入浏览器。不得把当前 Mock 结果当作真实知识增强推理证据。

## 文件与验收

新增 adapters/knowledge.py、api/knowledge.py、Knowledge.vue、test_knowledge_12b.py、knowledge.test.ts、模拟规范及本文。修改 Agent 配置/API、Runtime、尝试审计、工作台编译/引用回调、路由导航、Agent 表单和成果/执行来源显示；CAAS 评分不变。

新增依赖仅 pypdf（安装在项目 .venv，pyproject 声明）。后端全部 **667 项通过**（原655+12），含四种格式、扫描PDF拒绝、持久化/删除、固定中文检索集、库隔离、引用伪造拒绝、三Agent实际索引与上游传递、反馈修复、提示角色隔离和上下文超限拒绝。前端全部 **54 项通过**（原53+1），TypeScript、生产构建、Python 编译、pip check、diff 检查通过；npm 离线审计0项仅代表缓存范围。原测试均保留；存在原 TestClient 弃用警告。

完整文件范围：新增 backend/src/futurecup/adapters/knowledge.py、api/knowledge.py、backend/tests/test_knowledge_12b.py、frontend/src/pages/Knowledge.vue、frontend/tests/knowledge.test.ts、config/campus-business-rules-12b.md 和本文；修改 domain/models.py、domain/verification.py、application/ports.py、runtime.py、planner.py、selfloop.py、adapters/llm.py、api/app.py、agents.py、workspace.py、backend/pyproject.toml、前端路由导航、Agent/Workspace/RunDetail 页面、workspace.ts、types.ts 及既有工作台测试的新请求替身。普通 Agent 独立执行只提供检索审计，不把执行成功自动标成引用验证通过；要独立验证需采用带引用策略的工作流，工作台绑定库时自动配置该策略。

实际 Vite→18014 HTTP Mock 流程成功，run_id `37d08b8e788e4ceb8fc9c13447a12fbd`，simulated=true、verified=true，手工检索命中2条，最终4条引用；这是真实索引/执行流程，模型响应明确模拟，不是付费推理。没有读取密钥、请求 DeepSeek、改写历史运行或 push。
