# C-05 切片 7：`_fetch_all` 接入巨潮标题级公告 envelope

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `de0e4e013b1364ce55cd5e6d116c11fdd6b19d2d`（或其线性后代）。  
**一个关注点：** 在 `_fetch_all` 用 vendor 链**同一份** `cn_akshare` 调用已有 `get_cninfo_announcements`，把 `CninfoDisclosureEnvelope` 写入 `results["cninfo_announcements"]`，让现有 `cninfo_record_to_evidence` + `build_news_event_coverage` 循环真正吃到巨潮主源。  
**禁止：** 改 `get_cninfo_announcements` / PDF / `adjunctUrl` / 聚类算法；接线 `anns_d`；把 CNINFO 加进 `TOOLS_CATEGORIES` / `route_to_vendor`（本卡直连 registry，与 DAV-657 旁证同一模式）；本卡不要接 `get_cninfo_ir_surveys`（另开卡）；改 `_fetch_tushare_*`；C-09-3；社交 AUTH；打印 token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

主干 `de0e4e0` 已把 Tushare 旁证 `provider=` 传入 coverage，但 `_fetch_all` 任务表仍无巨潮结果，`cninfo_*` 循环对空 keys 空转，旁证只能挂 markdown 新闻或独立 `[结构化旁证]`。

## 允许改

- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector_cninfo_collect.py`（新建）

调用 `_registry.get("cn_akshare").get_cninfo_announcements(...)`。日期窗口与个股新闻一致：`start = trade_date - lookback`（现有 `LONG_DAYS`），`end`/`cutoff` = 规整后的 `trade_date`。`symbol` 用本次 `ticker`。失败 envelope 原样放入 `results["cninfo_announcements"]`，禁止把 KeyError/空表写成「确认无公告」。

## 契约

1. `_fetch_all` 结束后 `results["cninfo_announcements"]` 为 envelope（有 `status`/`records`），不是静默缺键。
2. `status=ok` 且 records 有 `canonical_event_id` 时，`event_coverage` 能见到该主源（cluster 或 evidences）；旁证不得改写该 `canonical_event_id`。
3. `provider_failure` envelope → coverage/`recall_status` 走失败/缺口，不得 `confirmed_empty`。
4. 空 records 的 ok/empty 路径文本不含「确认无公告」。
5. 单测 mock `get_cninfo_announcements`，禁止真打 `.cninfo.com.cn`。至少：正常一条、provider_failure、空 records。必须走 `_fetch_all`，禁止只 `patch(_fetch_all)`。
6. `env -u PYTHONPATH` + `.venv310/bin/python -m pytest tests/test_data_collector_cninfo_collect.py -q`

一个 commit，push 功能分支，评论 40 位 SHA。完成后不要自建审核卡、不要 @独立代码审核员。
