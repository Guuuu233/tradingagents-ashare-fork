# DAV-281 部署 dcc871（27 行业 + 本地 RAG）

主干已核验：`target/codex/dav-4-p2a-trunk@dcc871dff13878803881bdbb9aed55f7cc10dbeb`
父链：`8866494`（两阶段拓扑）→ `2873f30`（27 行业）→ `dcc871`（RAG）
DAV-276 / DAV-279 独立终审均 PASS。

当前宿主 healthz 仍为 `8866494`，PID 约 35631。active reports 须为 0。持久配置必须保持 3/1。

## 保护

禁止 `reset --hard` / `clean -fd`；禁止改 `.env`、providers、role_bindings、用户 Key、持久轮次。WIP 先保护再 FF。

## 部署

1. 再确认远端主干仍是 `dcc871dff13878803881bdbb9aed55f7cc10dbeb`。
2. `SELECT COUNT(*) FROM reports WHERE status IN ('pending','running')` = 0。
3. 宿主 fast-forward 到该 SHA。
4. kill 旧 PID，`lsof -iTCP:8000` 直到空。
5. 完整 `no_proxy` + `DATABASE_URL=sqlite:///./data/tradingagents.db` + `.venv310` 重启 uvicorn :8000。
6. 验收：新 PID、cwd=项目根、打开 `data/tradingagents.db`、healthz `commit_sha=dcc871dff13878803881bdbb9aed55f7cc10dbeb`。

## 进程冒烟（不发完整分析）

`.venv310`：
- `INDUSTRY_LINKAGE_MAP` 覆盖知识库 27 个 `industry_name`；`000725.SZ` 仍映射到消费电子（别名可解析）。
- `retrieve_industry_knowledge` / `retrieve_macro_event_knowledge` 对京东方/利率相关查询有命中；空查询输出 `【知识库未命中】`。
- `POST /v1/models/fetch` 正确账户 + `http://100.65.130.33:8317/v1` 仍可拉列表。

禁止改配置。不得 @项目调度助手。立即执行。
