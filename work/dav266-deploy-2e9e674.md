# DAV-266 部署 2e9e674（用户自有 URL 拉模型列表）

主干已核验：`target/codex/dav-4-p2a-trunk@2e9e674ceff062b7b35ae010a954b61f7575c47e`
父提交：`0b10041`。DAV-262 PASS。当前宿主 healthz 仍为 `0b10041`，PID 约 86680，active reports=0。

## 保护

禁止 `reset --hard` / `clean -fd`；禁止改 `.env`、providers、role_bindings、用户 Key、持久轮次。WIP 先保护再 FF。

## 部署

1. 再确认远端主干仍是 `2e9e674`。
2. active reports=0。
3. 宿主 fast-forward 到该 SHA。
4. kill 旧 PID，`lsof -iTCP:8000` 直到空。
5. 完整 `no_proxy` + `DATABASE_URL=sqlite:///./data/tradingagents.db` + `.venv310` 重启 uvicorn :8000。
6. 验收：新 PID、cwd=项目根、打开 `data/tradingagents.db`、healthz `commit_sha=2e9e674ceff062b7b35ae010a954b61f7575c47e`。

## 冒烟（只读，不改配置）

对 `POST /v1/models/fetch` 用正确账户：`base_url=http://100.65.130.33:8317/v1` 不得因 allowlist 被拦（连通/401 可记，但错误文案不能再是白名单拒绝）。云元数据仍须拒绝。不打印 Key。

不得 @项目调度助手。立即执行。
