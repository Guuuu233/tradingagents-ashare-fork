**只读审查。** 禁止改功能代码、禁止 commit、禁止 push、禁止部署、禁止 FF。

## 候选

- **exact SHA：** `d82b0da8c6e32c0e85371cd9abb9d85ebe920387`
- **分支：** `origin/agent/1/d7196be18b79`（远端：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`）
- **父 tip：** `e20c6acbbd4dca9f25b6df733ec6216a7e84bab6`（必须是直接第一父；主干 `origin/codex/dav-4-p2a-trunk`）
- **关联开发卡：** DAV-642
- **Commit 标题：** `feat(tushare): 实现 repurchase 股票回购结构化旁证接线与 PIT 契约 (C-05 Slice 3, DAV-642)`

## 审核范围（白名单 2 文件）

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_tushare_repurchase_collateral.py`
- 严禁改 `news_event_evidence.py`、`data_collector.py`、巨潮、`forecast` 行为、`disclosure_date`、`api/`、`frontend/`、`.env`。

## 契约

1. 直接第一父 = `e20c6acbbd4dca9f25b6df733ec6216a7e84bab6`；`git diff --name-only` 仅上述 2 文件。
2. 复用 `_tushare_post`；按列名取数；禁止 `iloc`；禁止真 Token。
3. PIT 仅 `ann_date <= as_of`；禁止用 `end_date`/`exp_date` 截断。
4. 缺 `ann_date` → `schema_drift`/`missing_field`（须在空表判定之前或同等严格）。
5. 403/`token_missing` → `provider_failure`；0 行 → `collateral_empty`，文本不含「确认无公告」。
6. `canonical_event_id` 恒 `None`。
7. `proc=完成` 且 `ann_date > as_of` 的累计金额行必须丢弃。
8. 单测 mock HTTP。定向：`tests/test_tushare_repurchase_collateral.py`；回归至少含 `tests/test_tushare_forecast_collateral.py`。

书面给出 ✅通过 / ⚠️有条件通过 / ❌打回，含路径与行号。PASS ≠ 准予合入。
