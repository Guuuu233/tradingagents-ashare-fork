# C-05c：canonical_event_id 跨源聚类（不写 manifest、不解析 PDF）

**基线：** `origin/codex/dav-4-p2a-trunk` tip 必须含 `effa20f6bbeed6e74716db71189380ef7f743245`。  
**一个关注点：** 媒体新闻证据与巨潮元数据若已有**同一** `canonical_event_id`（`cninfo:{announcementId}`），则进入同一 `EventCluster`。  
**不要做：** `source_manifest`/`query_manifest`/`recall_gap`、PDF 正文抽取、Tushare `anns_d`、部署、改 `get_news` 东财 markdown。

## 契约

1. `NewsEvidence` 增加可选 `canonical_event_id: str | None = None`。缺省 None。DAV-610 测试「parse_news_markdown 不得发明 id」仍成立：解析媒体 markdown **不得**用标题/`source_hash` 填 id。
2. 只有调用方把巨潮 `CninfoDisclosureRecord.canonical_event_id` **原样**拷到 evidence 时才有值。禁止 `cninfo:` + 标题哈希。
3. `cluster_news_evidences`：两边都有非空且相等的 `canonical_event_id` 时**必须**归入同一簇，即使标题/URL/来源不同。不等的 id 不得因标题模糊匹配并成一簇。
4. 仅一侧有 id：不得为另一侧编造 id；仍可走现有 URL/`source_hash`/标题规则。
5. `EventCluster` 可回显该 id（有则带上，无则字段缺省/None）。不要改 recall_status。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`
- `tradingagents/graph/data_collector.py`（仅当需要把已取到的 cninfo records 的 id 拷到对应 evidence；不要为此改东财 get_news）
- `tests/test_news_event_coverage.py`

禁止改 `cninfo_disclosure.py` 的查询/hash 契约，除非测试 import 需要。

## RED

1. 两条 NewsEvidence：标题不同、URL 不同，但 `canonical_event_id` 同为 `cninfo:1225488095` → 一个 cluster。
2. 两条标题极相似、时间接近，id 分别为 `cninfo:1` 与 `cninfo:2` → 两个 cluster。
3. markdown 解析路径仍无 `canonical_event_id` 键或值为 None。

一个 commit，push，40 位 SHA。禁止 FF/部署。独立审核 exact SHA。
