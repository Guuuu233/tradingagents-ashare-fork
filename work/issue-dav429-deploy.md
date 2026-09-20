# P2-10.4 部署 data_gaps 结构性分类

等 `target/codex/dav-4-p2a-trunk` 精确等于 `11309037de9334820603eec6dd801f291172f6ed`。若仍是 `50679db31a7f…` → BLOCK（那是合入前旧主干）。

项目根 `/Users/davidliu/Documents/TradingAgents-AShare`。在途报告可有 v2 样本补齐任务，**不要 kill 样本脚本**；可在样本 job 间隙或并行重启 API（确认 executor 策略后安全重启）。禁止 hard reset / clean 脏工作树无关文件。

动作：
1. 宿主 checkout/FF 到 `11309037de9334820603eec6dd801f291172f6ed`（仅主干相关 refs；勿 reset 脏 work/）
2. `DATABASE_URL=sqlite:///./data/tradingagents.db`，`.venv310`，保留代理 + 完整 no_proxy
3. 重启 uvicorn
4. `/healthz.commit_sha` 必须等于 `11309037de9334820603eec6dd801f291172f6ed`
5. 持久辩论轮次仍为 3/1；禁止改模型绑定 / Key

完成后贴 healthz 原文。不要 mention 项目调度助手。
