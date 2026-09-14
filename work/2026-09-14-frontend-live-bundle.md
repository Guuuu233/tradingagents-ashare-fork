# 前端 live bundle 验收证据

日期：2026-09-14（Australia/Perth）

## 验证对象

- 前端源码来自发布版本 `026349614a3f1b92a95dc06c0515f10ebec193bc`。
- 构建目录为 `/private/tmp/ta-release-p1e-0263496-20260914/frontend`；构建结果由当前
  发布副本启动的 API 进程从 `frontend/dist` 提供。
- 本项只验证前端构建和 HTTP 入口，不触发真实分析、不写生产数据库。

## 构建结果

- `npm test -- --run`：15 个测试文件、144 个测试全部通过。
- `npm run build`：TypeScript 检查通过，Vite 成功构建，转换 2784 个模块。
- 构建产物共 10 个文件：
  - `frontend/dist/index.html`：SHA-256
    `71cf0760ac37b77b8952e476d034a93a4d603e8f1911a3b83489430f3e6f5ec4`
  - `frontend/dist/assets/index-BC0YYieV.js`：SHA-256
    `9696168a4a37ee0101158aa13f71eb01e17afbd6ff1cdf186d086b102cee645f`
  - `frontend/dist/assets/index-wyh7fy6w.css`：SHA-256
    `b92200a2edaa00770ed2894255935e39991e75bd0335346243b5d4ac6c978500`

## live HTTP 验收

- 候选先在 8001 启动：`/healthz` 精确返回发布 SHA；`/` 返回 HTTP 200，页面标题为
  `TradingAgents Dashboard`；新 JS 资源返回 HTTP 200。
- 同一发布副本重启到 8000 后：`/healthz` 仍精确匹配发布 SHA；`/` 返回 HTTP 200，页面标题
  正确；`/assets/index-BC0YYieV.js` 返回 HTTP 200。
- `/v1/does-not-exist` 仍返回 HTTP 404，说明 API 路径没有被 SPA fallback 吞掉。
- 8001 临时进程已关闭，8000 保持单一服务监听。

## 结论边界

- DAV-887 的 live bundle 缺口已完成当前发布副本的 HTTP 层验收。
- `frontend/dist` 是构建时运行产物，未纳入 Git 追踪；后续任何发布版本都必须重新构建并
  重复本验收，不能沿用本次资源名或 hash。
- 本项不等于浏览器交互、真实用户登录或真实分析业务验收；这些仍需单独的受控测试范围。
