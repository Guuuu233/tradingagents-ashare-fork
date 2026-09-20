## 部署目标

等 `origin/codex/dav-4-p2a-trunk` 精确等于 `d50b0dc3a7b3721aba16ac4474530c1565be79de`（已 FF 核验通过）。若 healthz 仍是 `9321280…` → 需要重启对齐。

项目根 `/Users/davidliu/Documents/TradingAgents-AShare`。

## 执行动作

1. 宿主 checkout/FF 到 `d50b0dc3a7b3721aba16ac4474530c1565be79de`（勿 reset/clean 脏 work/）。
2. `DATABASE_URL=sqlite:///./data/tradingagents.db`，`.venv310`，保留代理 + 完整 no_proxy。
3. 重启 uvicorn。
4. `/healthz.commit_sha` 必须等于 `d50b0dc3a7b3721aba16ac4474530c1565be79de`。
5. 持久辩论轮次仍为 3/1；禁止改模型绑定 / Key。
6. `credit_weighting_enabled` 严格保持默认 `False`。

## 验证

- `.venv310/bin/pytest tests/test_evaluation_contracts.py -q`（契约测试）
- 贴 healthz 原文

不要 mention 项目调度助手。
