# C-05b：巨潮公告 PDF/原文内容资格与 hash（不聚类、不写 manifest）

**基线：** 先 `git fetch origin`，父 tip 必须是当前 `origin/codex/dav-4-p2a-trunk`（合入后含 DAV-617 `fba70674bcaa3df5e271d2148ee40dceb6b83a18`）。  
**一个关注点：** 在已有标题级元数据上，为**单条**公告/IR 补内容资格：能否在 cutoff 前取得原文/PDF 字节，以及内容 hash。  
**不要做：** 跨媒体 `canonical_event_id` 聚类、`source_manifest`/`query_manifest`/`recall_gap`、解析财报数字、Tushare `anns_d`、部署。

## 数据约束

- 主源仍是巨潮；**禁止** `anns_d`（403）。
- AKShare 1.18.30 的 disclosure 函数**丢掉** `adjunctUrl`/`announcementId` 列。本卡允许在 `cninfo_disclosure` / `CnAkshareProvider` **原路径**上，对与 `stock_zh_a_disclosure_*_cninfo` **同一** `hisAnnouncement/query` 响应按**列名/字段名**保留 `adjunctUrl`（或官方 PDF 字段）。禁止编造 `static.cninfo.com.cn/finalpage/...` 公式；URL 必须来自响应字段或已有 `公告链接` 里可验证的附件。
- 缺原生 `announcementId`：`canonical_event_id` 仍为 null，且不得进入“正文已核验”。
- 超时、HTTP 非 2xx、非 PDF/非可读正文、KeyError：`content_status=unavailable` 或 `provider_failure`，**不是** confirmed empty、也不是“无此公告”。
- `content_observed_at` 不得冒充 `announced_at`。资格只看公告时间 ≤ cutoff **且** 字节在分析日可取得的证明（测试用 mock 响应即可）。

## 契约字段（加在现有 record 上，禁止平行 record 类型 `_v2`）

- `content_status`: `hashed` | `unavailable` | `not_attempted`
- `content_sha256`: 64 hex 或 null（仅 hashed）
- `content_bytes` 不得写入日志/prompt/测试黄金文件全文；测试只断言 hash 与 magic `%PDF` 或明确失败类。

标题仍不能支撑财务结论。有 hash ≠ 已抽取净利润。

## 允许改

- `tradingagents/dataflows/cninfo_disclosure.py`
- `tradingagents/dataflows/providers/cn_akshare_provider.py`（仅 cninfo 方法）
- `tests/test_cninfo_disclosure_metadata.py`（或同目录仅本卡测试）

## RED

1. Mock 附件 URL + PDF 字节 → `content_status=hashed`，sha256 钉死，`canonical_event_id` 仍 `cninfo:{id}`。
2. Mock 仅有标题无附件字段 → `unavailable`，不编 URL。
3. Mock 下载 403/超时 → `unavailable`/`provider_failure`，不是 confirmed_empty。
4. 无 announcementId → 不 hashed。

一个 commit，push origin，40 位 SHA。禁止 FF/部署。独立审核 exact SHA。
