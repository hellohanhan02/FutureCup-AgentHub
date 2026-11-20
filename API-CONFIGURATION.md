# 评委自带 API 的安全配置

默认 Mock 无需密钥。真实模式仅使用 stable；不会开启增强版真实跨运行恢复或未经验证的真实持久化账本。启动服务器不等于授权执行付费任务。

## 实际支持范围

| 类型 | 已有兼容路径 | 默认示例模型 | 边界 |
|---|---|---|---|
| mock | 本地测试替身 | mock-echo / mock-scenario08 等 | 明确模拟，不消耗模型费用 |
| deepseek | `https://api.deepseek.com` 或官方 `/v1` | deepseek-flash | 既有真实历史证明了当时配置；当前模型可用性/价格由使用者核实 |
| qwen | 已有官方 DashScope `/compatible-mode/v1` 主机白名单 | qwen-plus | 支持 OpenAI Chat Completions 兼容协议配置；本次没有真实 Qwen 验收 |
| openai_compatible | 管理员显式批准的 HTTPS 主机与 Base URL | 使用者填具体模型 ID | 只支持现有 Chat Completions 通路，不代表所有厂商专有 API 都兼容 |

Base URL、Provider 类型、模型标识、超时、工具能力、格式能力、上下文/输出上限与估算单价都由服务端配置。URL 禁止认证信息、query、fragment、非允许主机、非公网地址及不安全重定向。通用模式必须同时显式传入 AllowedHost；不会把网页任意 URL 转发为请求。

Start-Real 默认只声明 text/prompt_json，不声明通用原生结构化输出或 Function Calling 已被所有模型支持。底层已有工具调用协议接口；本次用本地 HTTP 替身验证，实际厂商能力须单独确认，不能盲目开启。

## DeepSeek 启动步骤（由评委手动执行）

先按 README 完成安装。核实官方当期模型与人民币每百万输入/输出 Token 价格，然后运行以下交互命令：

```powershell
$inputPrice=[double](Read-Host '已核实的每百万输入 Token 人民币单价')
$outputPrice=[double](Read-Host '已核实的每百万输出 Token 人民币单价')
.\Start-Real.ps1 -EnableRealCalls -Provider deepseek -BaseUrl 'https://api.deepseek.com' -Model 'deepseek-flash' -InputPricePerMillionCny $inputPrice -OutputPricePerMillionCny $outputPrice -BudgetCny 2
```

脚本要求输入 `ENABLE`，然后通过 Read-Host -AsSecureString 隐藏输入**评委自己的新密钥**。不要发到聊天窗口，不在网页或文件中保存。脚本不读取已有的 DEEPSEEK_API_KEY 或其他已保存真实密钥；新输入通过子进程 stdin 传入，仅保留在服务进程的环境 `JUDGE_LLM_KEY`。不会写磁盘、注册表、CLI 参数、前端或 Git。清理临时变量/BSTR只能减少暴露时间，不能保证彻底清除不可变字符串或进程内存。

启动使用独立 `.runtime/real/` 数据库，后端 19323、前端 15323。网页：**http://127.0.0.1:15323/#/workspace**。

网页再选择真实模式及刚启用的 Provider/模型；默认用户预算仍为0.50元。需要2元时必须由评委选择并授权，预算预检通过后才能生成真实计划；查看计划后再次确认执行。不要选择未启用的其他模型，也不自动回退 Mock 来伪装成功。

上面2元只是显式选择示范，不是必要收费额或账单保证。单价未知/不合法必须停止。输出默认最多4096 Tokens，可通过 MaxOutputTokens 进一步收紧；上下文最多131072字节、请求默认120秒、并发1、无 Provider 自动重试、任务统一最多6次模型调用与一次共享修复。规划、执行和修复共同计数；RAG上下文仍接受长度和预算检查。

估算采用配置单价与保守输入/输出预留，概念公式为 `Σ(预留输入Tokens×输入单价+预留输出Tokens×输出单价)/1,000,000`，实际准入以现有 budget-preview 为准。估算费用、API 已报告 Token 和供应商最终账单不同，不能宣称精确费用硬上限。

关闭：`Stop-Demo.ps1 -Mode real`，只停止本次拥有的进程。

## Qwen 和通用兼容服务

使用相同脚本，更换显式参数即可。例如 Qwen 采用 Provider qwen、BaseUrl `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型及单价必须由评委核实。其他已有官方地域主机以 backend/src/futurecup/adapters/provider_config.py 中白名单为准。

通用服务示例中的域名与模型必须替换为评委批准的真实服务：

```powershell
$baseUrl=Read-Host '管理员批准的 HTTPS Base URL（不含密钥或query）'
$approvedHost=Read-Host '同一服务的批准主机名'
$modelId=Read-Host '服务端支持的模型ID'
.\Start-Real.ps1 -EnableRealCalls -Provider openai_compatible -BaseUrl $baseUrl -AllowedHost $approvedHost -Model $modelId -InputPricePerMillionCny $inputPrice -OutputPricePerMillionCny $outputPrice -BudgetCny 0.5
```

不能将本地测试的 allow_loopback 配置直接用于任意公网代理。生产启动包装不开放该选项，核心 DNS/地址限制保持不变。前端暂不提供密钥保存；这是有意保留的安全终端配置入口。

## 本次验证结论

全新环境使用生产适配器请求本机模拟 HTTP 服务，两版各10项协议检查通过：路径、模型、max_tokens、授权头精确匹配（仅记录布尔值）、usage解析、Function Calling解析、401/429错误、异常JSON、超时及不安全URL拒绝。只用合成凭据，不输出 Authorization 值，不产生真实推理费用。

真实启动脚本仅做静态/语法检查，没有输入真实 Key 或进行真实厂商调用。不能以这些协议替身结果宣称当前DeepSeek/Qwen/任意兼容服务已真实联通。