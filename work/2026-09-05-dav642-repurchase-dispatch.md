# C-05 旁证切片 3：只接线 `repurchase`（不接线 disclosure_date，不改巨潮主源）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须已含 `e20c6acbbd4dca9f25b6df733ec6216a7e84bab6`（C-05 forecast）。  
**一个关注点：** 用现有 `CnAkshareProvider._tushare_post` 拉 `repurchase` 结构化旁证行，PIT 用 `ann_date <= as_of`，`canonical_event_id` 恒为 `None`。  
**禁止：** `anns_d`；`forecast` 再改；`disclosure_date`（另开卡）；改聚类/PDF；把空表写成确认无公告；改巨潮 `get_cninfo_*`；改 `news_event_evidence.py` / `data_collector.py`；C-09-3；打印 token；FF/部署；push 主干。

依据 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md` §2.2。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（扩展 `_TUSHARE_REQUEST_FIELDS` + `_fetch_tushare_repurchase`；复用既有 post，禁止新 HTTP 客户端）
- `tests/test_tushare_repurchase_collateral.py`（新建）

## 契约

1. `ann_date > as_of` 的行丢弃；不得用 `end_date` 或 `exp_date` 截断。
2. 按列名取数；缺 `ann_date` → `schema_drift`/`missing_field`。
3. 403/`token_missing` → `provider_failure`；0 行 → `collateral_empty`（测试断言文本不含「确认无公告」）。
4. 返回结构里 `canonical_event_id` 必须是 `None`。
5. 字段至少按列名取：`ts_code,ann_date,end_date,proc,exp_date,vol,amount,high_limit,low_limit`。
6. 单测全部 mock HTTP：正常一行、越界行被丢、空表、缺列、token 缺失。禁止真网关。
7. 必须覆盖：`proc=完成` 且 `ann_date > as_of` 的累计金额行被丢弃（防完成态穿越）。

一个 commit，push 功能分支，40 位 SHA。禁止 push 主干。
