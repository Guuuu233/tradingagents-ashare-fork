# C-05 旁证切片 6：`data_collector` 把已有 Tushare 旁证拉进 `event_coverage`

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `45f52868c78e2239a126595fd60bdf10db4f4de4`（或其线性后代）。若主干尚未在该 SHA，停工等 Cursor FF。  
**一个关注点：** `_fetch_all` 在调用 `build_news_event_coverage` 时传入与 vendor 链**同一份** `cn_akshare` provider，让已合入的 `fetch_tushare_collaterals` / 软对齐在采集入口真正跑起来。  
**禁止：** 改 `_fetch_tushare_*` 实现；改聚类/PDF/`adjunctUrl`；把旁证写成 `cninfo:` ID；空旁证写成「确认无公告」；把 CNINFO 接进 `route_to_vendor` / `TOOLS_CATEGORIES` / `_fetch_all` 并行任务表（巨潮主源采集另开卡）；C-09-3；社交 AUTH / `TA_SOCIAL_MODE`；打印 token；打官方 `api.tushare.pro`；FF/部署；push 主干。独立审核走**单独审核卡**，本卡评论**禁止** @独立代码审核员。

依据 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md` §3.3 与已合入 `news_event_evidence.build_news_event_coverage(..., provider=)`。

## 现状（主干已核实）

`tradingagents/graph/data_collector.py` 已把 `cninfo_*` envelope 转成 `NewsEvidence` 并调用 `build_news_event_coverage`，但**未传 `provider=`**。`build_news_event_coverage` 仅在 `provider is not None` 时调用 `fetch_tushare_collaterals`。因此采集路径上三张旁证表不会被拉、不会进 gap、也不会软对齐。

`_fetch_all` 的并行任务表当前也没有 `get_cninfo_*`。本卡**不要**补巨潮抓取。测试 mock `fetch_tushare_collaterals` 或 provider 的 `_fetch_tushare_*` 即可。最低验收：`provider=` 被传入，且 mock 旁证进入 `event_coverage` / gap。

## 允许改

- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector_collateral_collect.py`（新建；不要把无关断言塞进旧 `test_data_collector.py` 除非必须共用 fixture）

Provider 必须来自 `tradingagents.dataflows.interface` 里已有的 `_registry.get("cn_akshare")`（与 `route_to_vendor` 同一实例、同一并发限额）。禁止 `CnAkshareProvider()` 另造一份。禁止新 HTTP 客户端。

## 契约

1. `_fetch_all(..., trade_date)` 调用 `build_news_event_coverage` 时 `provider` 为上述 `cn_akshare` 实例，`default_entity` 为本次 `ticker`，`cutoff` 仍为分析日。
2. 复用已有 `_fetch_tushare_forecast` / `_fetch_tushare_repurchase` / `_fetch_tushare_disclosure_date`；本卡不改它们。
3. `provider_failure` 进 `event_coverage` gap（或现有 coverage 的 failure 列表），不得当成空表成功。
4. `collateral_empty` 任何落盘/coverage 文本不含「确认无公告」。
5. 旁证 `canonical_event_id` 恒为 `None`；不得发明 `cninfo:` ID。
6. 单测全部 mock：禁止真网关、禁止读 `.env` token。至少覆盖：
   - `_fetch_all` 路径（不要只测 `collect()` 再 `patch(_fetch_all)`，那会绕过本卡代码）
   - mock 三表之一返回可对齐行时，`event_coverage` / `market_data_context["event_coverage"]` 出现旁证挂载或 `[结构化旁证]` 独立项
   - mock `provider_failure` / 403 类 → gap，不是成功空
   - mock 空表 → 不出现「确认无公告」
7. 解释器：`env -u PYTHONPATH` + 仓库 `.venv310/bin/python -m pytest <新测试文件> -q`

一个 commit，push 功能分支，评论写出完整 40 位 SHA 与 `origin/<branch>`。禁止 push 主干。完成后不要自建审核卡、不要 @独立代码审核员；Cursor 另开审核卡。
