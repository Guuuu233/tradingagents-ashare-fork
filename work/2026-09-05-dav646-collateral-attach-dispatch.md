# C-05 旁证切片 5：把已接线的 forecast/repurchase/disclosure_date 软对齐挂到巨潮主源

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须已含 `b9e7238376e2ba74f1e3510f90f3b5a27dd8c08c`。若尚未在远端主干，等 Cursor FF。  
**一个关注点：** 在 `news_event_evidence.py` 把三张旁证表的结构化行软对齐挂到巨潮 `NewsEvidence` / envelope，**不替代** CNINFO 主源。  
**禁止：** 改 `_fetch_tushare_*` 实现；改聚类/PDF/`adjunctUrl`；把旁证写成 `cninfo:` ID；空旁证写成「确认无公告」；C-09-3；打印 token；FF/部署；push 主干。`data_collector.py` **本卡不要改**（采集入口另开卡）。

依据 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md` §3.3。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`
- `tests/test_news_event_collateral_attach.py`（新建）

可调用已有 `CnAkshareProvider._fetch_tushare_forecast` / `_fetch_tushare_repurchase` / `_fetch_tushare_disclosure_date`，禁止新 HTTP 客户端。

## 契约

1. `canonical_event_id` 仅来自巨潮主源；旁证记录恒为 `None`，`collateral_id` 用 `tushare:` 命名空间。
2. 软对齐三要素：同标的；`|primary.announced_at.date - collateral.ann_date| <= 1 日`；标题主题分别对应 forecast / repurchase / disclosure_date（见评估稿 §3.3.2）。
3. 主源未命中时旁证可独立留存，标签必须是结构化旁证，不得冒充全量公告。
4. `provider_failure` 进 gap，不是空；`collateral_empty` 文本不含「确认无公告」。
5. 单测 mock 旁证与主源：命中挂载、日期越界不挂、异标的不挂、`actual_date` 未来仍可挂（PIT 已在 fetch）、canonical_event_id 不被旁证改写。禁止真网关。

一个 commit，push 功能分支，40 位 SHA。禁止 push 主干。
