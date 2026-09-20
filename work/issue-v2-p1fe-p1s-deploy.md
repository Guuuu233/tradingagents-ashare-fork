# P1-FE + P1-S 部署（等主干同时包含两候选 SHA）

## 前置（不满足则 BLOCK，不重启）

独立核验合入卡完成后，`git ls-remote` 主干必须同时是：

- `d9c7014684c844f0aa0042bb822986f41bb0c7ca` 的后代
- `059f0810674b107b1ee1f56cfe44a5cd704b74f5` 的后代

记下那时的**精确 trunk SHA**，下文称 `NEW`。当前若 trunk 仍是 `0554216` → 本卡不得动手。

项目根：`/Users/davidliu/Documents/TradingAgents-AShare`  
DB：`sqlite:///./data/tradingagents.db`（`data/tradingagents.db`）  
解释器：`.venv310`

## 允许动作

1. 在途报告为 0，否则 BLOCK。
2. 记录旧 PID、旧 health SHA。
3. 宿主大量 untracked，禁止 `reset --hard` / `clean -fd`。阻塞 checkout 的 untracked 只许移动备份，不许删。
4. `https_proxy=http://127.0.0.1:7897` fetch target 主干，本地 `merge --ff-only` 到 `NEW`。
5. TERM 旧 PID，`lsof -nP -iTCP:8000 -sTCP:LISTEN` 为空后再启：
   - `env -u PYTHONPATH`
   - `DATABASE_URL=sqlite:///./data/tradingagents.db`
   - 代理 + 完整国内 `no_proxy`
   - `.venv310/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000`
6. `/healthz.commit_sha` 必须等于 `NEW`。
7. 库内用户仍辩论 3 / 风险 1。

禁止改代码、`.env`、模型/绑定/Key。前端静态资源若由该 API 托管则随重启生效；不要另起无关服务。

评论交付：旧/新 PID、trunk SHA、healthz JSON 摘要、3/1。不要 mention 项目调度助手。
