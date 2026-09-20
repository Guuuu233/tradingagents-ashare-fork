# C-05d：cninfo 进入 query_manifest / recall_gap（不编默认五主题）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须含 `d6f75dddb396d35e5102c66b67a5e13f8d0650bb`（若尚未 FF 则等 Cursor 合入后再动）。  
**一个关注点：** 把**已经取到的**巨潮元数据变成诚实的应查清单与缺口，而不是再编主题。  
**不要做：** PDF 解析、改聚类规则、Tushare `anns_d`、部署、H1b、把失败写成「确认无公告」。

## 契约（延续 DAV-608）

| 条件 | recall_status | query_manifest | 禁止说法 |
|---|---|---|---|
| 未调用巨潮 / 调用方未给清单 | `unknown` | `[]` | 编五主题；「无明显主题缺失」 |
| 巨潮 envelope `ok` 且有 records | `partial_vs_manifest` | 原样列入 `canonical_event_id` 或标题（有 id 优先 id） | 把未 hashed 当成无事件 |
| 巨潮 `provider_failure` / KeyError | 不得当 confirmed empty | 记 `recall_gap`/`provider_failure` | 「确认无公告」 |
| 巨潮 `confirmed_empty`（带齐列空表） | 可记该次查询空 | 仍不得推广成全市场无新闻 | 媒体新闻缺失 ≠ 无公告 |

`source_manifest` 只回显实际查过的源（如 `cninfo_announcement` / `cninfo_ir_survey`），未查则不要假装查过。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`（`build_news_event_coverage` / format summary）
- `tradingagents/graph/data_collector.py`（把已有 cninfo envelope 传入 coverage，不要为此改东财 get_news）
- `tests/test_news_event_coverage.py`

## RED

1. 无 cninfo、无 requested_themes → 仍 `unknown`，manifest `[]`。
2. 传入两条 `cninfo:1225488095` 等 records → manifest 含这些 id；coverage 不得写「无明显主题缺失」。
3. 传入 `provider_failure` envelope → 不是 confirmed empty，有 gap。

一个 commit，push，40 位 SHA。禁止 FF/部署。
