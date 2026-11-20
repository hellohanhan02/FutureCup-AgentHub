# 14-D 验收与命令记录

## 已确认结果

| 项目 | 实际结果 | 证据 |
|---|---|---|
| 全量后端 | 826通过，0失败/跳过，保留一条Starlette/httpx弃用提示 | .data/platform-14d-tests.xml及backend日志 |
| 后续关联源/子运行导出改动 | 6项定向API回归通过，约5秒 | focused-api-final.log/json |
| 全量前端 | 11文件、68项通过 | frontend日志 |
| TypeScript | 已有可靠退出码0，约4秒，无残留vue-tsc | typecheck日志；未因界面等待反复重跑 |
| Vite生产构建 | 独立执行成功，约12.6秒，90秒上限 | vite-build-independent.json/log |
| Python编译 | 成功，30秒上限 | compile-independent.json/log |
| pip check | 成功，30秒上限 | pip-check-independent.json/log |
| npm依赖树 | 成功，30秒上限，无新增依赖 | npm-ls-independent.json/log |
| npm audit | 离线缓存0项，不能代表最新漏洞库 | npm-audit-offline-independent.json/log |
| loopback HTTP综合案例 | 成功，约1.8秒，40秒上限 | results/platform-14d/http-acceptance |
| 后端/Vite代理/HTML | 18031及15191 API HTTP200，前端HTML HTTP200 | proxy-health.json |
| 浏览器与截图 | 未验收，0截图 | browser-acceptance.json，安全工具无法可靠判断Windows URL |

日志均在.data/platform-14d-checks，包含退出码、时间上限和实际输出。没有把DOM测试称作浏览器测试。全量后端之后只补关联证据导出读取，按用户要求仅重测覆盖该改动的6项，不无意义地重复全套。

## 新增测试

后端6项：综合RAG/Adaptive CAAS/独立验证失败/精准恢复/账本/导出；重复提交；重启后的真实工具记录及运行关联；UNKNOWN持续占用；版本和Run ID错误；脱敏；普通工作台共享持久预算闭环。

前端9项：实际依赖布局、导出身份和版本拒绝、UNKNOWN展示、真实权重与缺失数据、知识来源和恶意文本转义、A→B和A→B→A竞态、导出去重及跨任务迟到响应、真实恢复禁用、旧确认框不能操作新运行。全部使用明确模拟HTTP或DOM夹具。

## 等待调查

旧综合辅助脚本没有用PIPE承接类型检查，类型检查直接运行Node/vue-tsc，日志记录已经成功。不能将Codex显示等待据为TypeScript未完成的证据。本阶段后续停止使用该综合脚本；检查拆成独立命令，分别写日志和退出码、设置上限。没有发现需终止的残留类型检查进程，没有停止其他服务。

若命令通道再次不返回，不重复尝试；在外部PowerShell逐项执行下述命令。已经可靠通过且相关代码未改动的项目不必重跑。

```powershell
Set-Location D:\competition\FutureCup-v1.1
# 每次只执行一条；这里是手动复核命令，不是自动综合脚本。
.\.venv\Scripts\python.exe -m pytest backend/tests/test_platform_14d.py -q --basetemp=.data/pytest-platform-manual-new
.\.venv\Scripts\python.exe -m compileall -q backend/src backend/tests
.\.venv\Scripts\python.exe -m pip check
```

```powershell
Set-Location D:\competition\FutureCup-v1.1\frontend
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit
node node_modules/vite/bin/vite.js build
node node_modules/vitest/vitest.mjs run
npm.cmd ls --depth=0
npm.cmd audit --offline
```

如需外部PowerShell硬超时，可针对其中**单条命令**用Start-Process -PassThru -WindowStyle Hidden，输出重定向到本项目新日志；调用WaitForExit(90000)。若超时，仅清理该次启动且已核实的PID树，不使用“终止全部node/python”。本阶段已执行的独立验收使用subprocess单项timeout=30/45/90并保存实际结果；不需要再次启动旧综合脚本。

## 交付文件分组

- API：api/platform.py、platform_demo.py及app挂载/专用知识库注入。
- 持久数据：工具调用快照、调度需求快照、账本public_view；原数据缺字段按默认兼容。
- 应用接入：原规划/工作流在显式账本模式绑定和恢复同一账户，无新增调度算法。
- 前端：ExecutionMap、SchedulingExplanation、KnowledgeCoverage、LedgerPanel、platform.ts；RunDetail、Workspace、Workflows、Runs的竞态/提交保护；api/types/composables和局部样式。
- 验证与操作：backend/tests/test_platform_14d.py、frontend/tests/platform14d.test.ts、Start-Platform14D.ps1、run_platform_14d_demo.py；check_platform_14d.py保留作历史追溯，剩余验收不再使用。
- 文档：platform-audit-14d.md、platform-14d.md、本记录及机器可读验收JSON。README与architecture追加本阶段说明。
- 实际Mock证据：results/platform-14d/http-acceptance/；验收摘要和浏览器缺口JSON。没有截图文件或截图替代品。
