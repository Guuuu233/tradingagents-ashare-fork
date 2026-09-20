# C-05 旁证切片 4：只接线 `disclosure_date`（不改巨潮主源，不改 forecast/repurchase）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须已含 `d82b0da8c6e32c0e85371cd9abb9d85ebe920387`（C-05 repurchase）。若尚未在远端主干，等 Cursor FF。  
**一个关注点：** 用现有 `CnAkshareProvider._tushare_post` 拉 `disclosure_date` 结构化旁证行，PIT 用 `ann_date <= as_of`，`canonical_event_id` 恒为 `None`。  
**禁止：** `anns_d`；改 `forecast`/`repurchase` 行为；改聚类/PDF；把空表写成确认无公告；改巨潮；改 `news_event_evidence.py` / `data_collector.py`；C-09-3；打印 token；FF/部署；push 主干。

依据 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md` §2.3。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（扩展 `_TUSHARE_REQUEST_FIELDS` + `_fetch_tushare_disclosure_date`；复用既有 post）
- `tests/test_tushare_disclosure_date_collateral.py`（新建）

## 契约

1. `ann_date > as_of` 的行丢弃。不得用 `end_date`、`pre_date`、`actual_date`、`modify_date` 截断。
2. `actual_date` 只能作为旁证字段原样保留（若列存在），禁止用它决定历史可见性。
3. 按列名取数；缺 `ann_date` → `schema_drift`/`missing_field`（空表判定不得盖住缺列）。
4. 403/`token_missing` → `provider_failure`；0 行 → `collateral_empty`（文本不含「确认无公告」）。
5. `canonical_event_id` 必须是 `None`。
6. 字段至少按列名取：`ts_code,ann_date,end_date,pre_date,actual_date,modify_date`（列缺失则 typed missing，禁止 invent）。
7. 单测全部 mock HTTP：正常一行、越界行被丢、空表、缺列、token 缺失；另测 `actual_date` 在未来但 `ann_date <= as_of` 的行仍保留（防把实际披露日当 PIT）。禁止真网关。

一个 commit，push 功能分支，40 位 SHA。禁止 push 主干。
