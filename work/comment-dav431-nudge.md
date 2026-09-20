编排侧核验（请直接在宿主路径操作，勿在 Multica worktree 里重启）：

- 宿主 `/Users/davidliu/Documents/TradingAgents-AShare` 已在 `codex/dav-4-p2a-trunk` @ `11309037de9334820603eec6dd801f291172f6ed`（与 `target` 一致）
- 当前 uvicorn PID 仍服务旧 build：`/healthz.commit_sha=50679db31a7fa1908f5ba91d54aa7c39711605a0`
- 因此本卡剩余动作：**仅重启宿主 uvicorn**，使 healthz=`11309037de9334820603eec6dd801f291172f6ed`
- `DATABASE_URL=sqlite:///./data/tradingagents.db`，`.venv310`，保留代理+no_proxy
- **禁止** kill `work/run_v2_sample_fill.py`（样本补齐进行中）
- 禁止改 3/1、模型、Key；完成后贴 `/healthz` 原文

[@代码运维测试员](mention://agent/f179edb8-9a81-4dbd-8787-afbfd307eda4)
