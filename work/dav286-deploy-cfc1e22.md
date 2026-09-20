# DAV-286 部署 cfc1e22（+历史案例学习闭环）

主干已核验：`target/codex/dav-4-p2a-trunk@cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`
父链：`dcc871`（RAG）→ `cfc1e22`（历史案例闭环，DAV-283）
DAV-284 独立终审已归档 PASS；当前宿主 healthz=`dcc871`。

## 保护

禁止 `reset --hard` / `clean -fd`；禁止改 `.env`、providers、role_bindings、用户 Key、持久轮次。WIP 先保护再 FF。

## 部署

1. 再确认远端主干仍是 `cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。
2. active reports（pending/running）= 0。
3. 宿主 fast-forward 到该 SHA。
4. kill 旧 PID，`lsof -iTCP:8000` 直到空。
5. 完整 `no_proxy` + `DATABASE_URL=sqlite:///./data/tradingagents.db` + `.venv310` 重启 uvicorn :8000。
6. 验收：新 PID、cwd=项目根、healthz `commit_sha=cfc1e22…`。

## 进程冒烟（不发完整分析）

- `historical_cases` 表迁移幂等：连续两次启动不报错、表结构一致；空表时检索输出 `【历史案例未命中】`。
- `retrieve_industry_knowledge` / `retrieve_macro_event_knowledge` 冒烟仍通过；`000725.SZ` 映射消费电子。
- `POST /v1/models/fetch` 正确账户 + `http://100.65.130.33:8317/v1` 仍可拉列表。

禁止改配置。不得 @项目调度助手。立即执行。
