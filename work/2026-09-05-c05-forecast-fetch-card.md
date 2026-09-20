# C-05 旁证切片 2：只接线 `forecast`（不接线 repurchase/disclosure_date，不改巨潮主源）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须已含 C-09-2 `e8130b69eb153fc4d0a3079f3ec7986bd9f58dd5`（若尚未 FF 则等 Cursor 合入）。  
**一个关注点：** 用现有 `CnAkshareProvider._tushare_post` 拉 `forecast` 结构化旁证行，PIT 用 `ann_date <= as_of`，`canonical_event_id` 恒为 `None`。  
**禁止：** `anns_d`；`repurchase`/`disclosure_date`（另开卡）；改聚类/PDF；把空表写成确认无公告；改巨潮 `get_cninfo_*`；C-09-3；打印 token；FF/部署。

依据 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md`。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（扩展 `_TUSHARE_REQUEST_FIELDS` + `_fetch_tushare_forecast`；复用既有 post，禁止新 HTTP 客户端）
- `tests/test_tushare_forecast_collateral.py`（新建）

不要改 `news_event_evidence.py` / `data_collector.py`（挂载到巨潮 evidence 是下一刀）。

## 契约

1. `ann_date > as_of` 的行丢弃；不得用 `end_date` 截断。
2. 按列名取数；缺 `ann_date` → `schema_drift`/`missing_field`。
3. 403/`token_missing` → `provider_failure`；0 行 → `collateral_empty`（测试断言文本不含「确认无公告」）。
4. 返回结构里 `canonical_event_id` 必须是 `None`。
5. 单测全部 mock HTTP：正常一行、越界行被丢、空表、缺列、token 缺失。禁止真网关。

一个 commit，push 功能分支，40 位 SHA。禁止 push 主干。
