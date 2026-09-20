H2.0 已 FF + 部署完成，可开工。

- 主干 tip / healthz：`d50b0dc3a7b3721aba16ac4474530c1565be79de`
- 契约模块：`tradingagents/agents/utils/evaluation_schemas.py`
- Mock 样本：`tests/mock_evaluations/`、`tests/test_evaluation_contracts.py`（13 passed）
- 历史案例参考：`tradingagents/knowledge/historical_cases.py`（黑名单排重勿污染已有基准标的）
- 门槛参考：`scripts/verify_h1b_gates.py`（bull/bear_samples 统计口径）

交付按卡面：`scripts/generate_weekly_target_pool.py` + pytest；隔离分支；业务代码 0 侵入；不改 3/1 / 模型绑定 / `credit_weighting_enabled` 默认 False。
