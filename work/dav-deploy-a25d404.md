# 主干部署到 a25d404 + 健康核验 + dry_run smoke

## 前置（门禁）

仅当以下都满足后再动手：
1. A2/A4 夹具修复已合入主干（或经主管确认可先部署、夹具并行）。
2. DB 无 running 分析任务（`reports` 仅 completed/failed 时可部署）。
3. 当前服务仍停在旧 SHA：healthz 曾报 `aa41f449...`。

目标 tip（或其后含夹具修复的 tip）：先 `git fetch` 再确认 `origin/codex/dav-4-p2a-trunk`。

## 只做

1. 确认无运行中分析后，按运维手册重启 uvicorn（`DATABASE_URL=sqlite:///./data/tradingagents.db` + 完整 `no_proxy`）。
2. 核验：`/healthz` 的 `commit_sha` == 目标 tip；`lsof` 新 PID；解释器为 `.venv310`；打开的是 `data/tradingagents.db`。
3. 真实业务 smoke：`dry_run=true` 的 analyze（或等价），确认 completed；**不得改写旧报告**。
4. 结果回帖：新旧 SHA、PID、healthz JSON、smoke job_id/status。

禁止：改用户模型/role_bindings/providers；`git add -A`；混提脏文件。
