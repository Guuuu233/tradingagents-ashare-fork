上一轮部署 run 已僵死（agent idle、healthz 仍为 `50679db`）。请重新执行：

宿主路径 `/Users/davidliu/Documents/TradingAgents-AShare` 已在 tip `11309037de9334820603eec6dd801f291172f6ed`。
**只重启 uvicorn**（`.venv310`，`DATABASE_URL=sqlite:///./data/tradingagents.db`，保留代理/no_proxy），使 `/healthz.commit_sha` = `11309037de9334820603eec6dd801f291172f6ed`。
勿 kill `work/run_v2_sample_fill.py`。勿改 3/1、模型、Key。完成后贴 healthz 原文。

[@代码运维测试员](mention://agent/f179edb8-9a81-4dbd-8787-afbfd307eda4)
