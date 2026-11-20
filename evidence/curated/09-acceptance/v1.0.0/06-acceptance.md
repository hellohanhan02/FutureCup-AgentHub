# 13-A 最终自动化技术验收

本次真实执行：后端pytest **671 passed**，前端Vitest **57 passed**；TypeScript noEmit及Vite生产构建通过。后端存在已知Starlette TestClient弃用警告，无跳过或删除测试。Python编译和pip check结果记录在acceptance.json，前端顶层依赖树完整，npm offline audit返回0，仅代表本地缓存范围。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp=.data/13a-final-tests
.\.venv\Scripts\python.exe -m compileall -q backend/src/futurecup
.\.venv\Scripts\python.exe -m pip check
cd frontend
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
npm.cmd ls --depth=0
npm.cmd audit --offline
```

冻结验收未发送真实DeepSeek请求；Provider协议用Mock HTTP，知识检索为实际本地索引。真实质量证据来自已有历史运行，不能把本次测试通过写成再次真实推理成功。浏览器完整人工截图/录屏不在本次自动化验收内，既有阶段人工验收与自动DOM测试需区分。
