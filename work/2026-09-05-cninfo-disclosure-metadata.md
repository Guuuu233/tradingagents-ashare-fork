# C-05a：巨潮 AKShare 公告/IR 元数据结构化接入

**基线：** `origin/codex/dav-4-p2a-trunk` = `b2f7b77bca19a9b50f0556989f06553c5b15404f`  
**一个关注点：** 只把巨潮标题级元数据接进项目，并诚实分类查询结果。  
**本卡之后才拆：** PDF/正文资格、跨媒体 `canonical_event_id` 聚类、`source_manifest/query_manifest/recall_gap`。不要提前做。

## 数据源（必须复用现有 AKShare，禁止自写 CNINFO HTTP）

宿主 `.venv310` 的 akshare **1.18.30**：

- `ak.stock_zh_a_disclosure_report_cninfo(symbol, market="沪深京", keyword="", category="", start_date, end_date)`  
  公司公告。日期参数是 **YYYYMMDD**。
- `ak.stock_zh_a_disclosure_relation_cninfo(symbol, market="沪深京", start_date, end_date)`  
  投资者关系活动/调研记录。

返回列（按列名，禁止位置切片）：`代码`、`简称`、`公告标题`、`公告时间`、`公告链接`。

**实现者必须知道的 AKShare 1.18.30 行为（不要去 patch site-packages）：**

1. 原生 `announcementId` **被函数在 return 前丢掉**；链接里仍有 `announcementId=`。canonical id 只能从 **列名 `announcementId`（若将来版本留下）或规范化 URL 的 query `announcementId`** 取出。缺原生 ID → `canonical_event_id=None`。**禁止**用标题哈希、`source_hash`、orgId 冒充。
2. 某些**查询成功但无行**的分类会在拼列时抛 **`KeyError`**。这是 adapter 崩溃，**不是** confirmed empty。必须记 `provider_failure` / recall gap，**不得**映射成「确认无公告」。
3. 只有能证明「请求成功且结果确实为空」（例如返回带齐列名的空 DataFrame，或显式 `totalAnnouncement==0` 且未抛）才能记 `confirmed_empty`。
4. 超时、HTTP/JSON 失败、未知 `KeyError`、缺列、时间不可解析：一律 failure/gap，丢弃该行或整次查询，禁止填今天。

## 结构化记录（本卡产物）

每条记录至少：

| 字段 | 规则 |
|---|---|
| `symbol` | 证券代码，按列名 `代码` |
| `title` | `公告标题` |
| `announced_at` | `公告时间`，严格解析；失败丢弃该行并记日志 |
| `url` | `公告链接`，可规范化；缺则 None，不编造 |
| `source_type` | 公告接口=`cninfo_announcement`；调研接口=`cninfo_ir_survey` |
| `cutoff_eligible` | `announced_at <= cutoff`（cutoff 纯日期则含当日 23:59:59.999999）；解析失败则该行不合格，不得默认 True |
| `announcement_id` | 原生 ID 字符串；缺则 None |
| `canonical_event_id` | 有原生 ID 则为 `cninfo:{announcementId}`；否则 **null** |

查询信封（与行列表分开）必须能区分：`ok` / `confirmed_empty` / `provider_failure`。标题级元数据**只能证明事件存在**，不得据此下财务或经营结论，注释/返回说明里写明。

## 建议落点（原路径，禁止 `_v2`）

- 新增 `tradingagents/dataflows/cninfo_disclosure.py`（解析/分类/记录构造；函数拆短）
- `CnAkshareProvider` 增加调用上述两函数的方法（`AKSHARE_CALL_LOCK`、超时边界与现有 ak 调用一致、`env -u PYTHONPATH` + `.venv310`）
- `tests/test_cninfo_disclosure_metadata.py`

**不要**改：`get_news`、媒体新闻聚类、`build_news_event_coverage` 的 manifest、`NewsEvidence.canonical_event_id`、Tushare、PDF 下载/解析、`role_bindings`/`providers`、H1b、部署、加权。

未实现 `route_to_vendor("get_news")` 混入公告。本卡可以先不挂 graph；有可测的 provider/helper 即验收。

## RED（修复前失败）

1. Mock 调研 DF：标题含「2026年7月26日至8月21日投资者关系活动记录表」，`公告时间=2026-08-21 07:28:15`，链接含 `announcementId=1225488095`，cutoff=`2026-08-24` → 一条 `source_type=cninfo_ir_survey`，`canonical_event_id="cninfo:1225488095"`，`cutoff_eligible is True`。
2. Mock 公告 DF：工业富联 601138 含 2026-07-28 回购报告与 2026-08-12 半年报（用标题+时间+链接 ID 钉住，不要位置切片）。
3. Mock 公告 DF：海康 002415、分类权益分派、2026-08-12 权益分派实施公告。
4. Mock AKShare 在无结果分类抛 `KeyError` → **不是** `confirmed_empty`，而是 `provider_failure`。
5. 无 `announcementId` 且 URL 也解析不出 → `canonical_event_id is None`；不得用 `compute_source_hash` 填。
6. 缺 `公告时间` 或无法解析 → 丢弃该行，不填 wall-clock。

允许用 mock，不必把 live HTTP 写进默认 CI。完成评论可附一次带 `no_proxy` 含 `.cninfo.com.cn` 的 live 探针摘要（只写标题/时间/ID/分类，禁止全文）。

## 施工

- 隔离 worktree：`git fetch origin && git worktree add -b agent/<you>/<short> <dir> b2f7b77bca19a9b50f0556989f06553c5b15404f`
- `ln -sfn` 宿主 `.venv310`；`env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_cninfo_disclosure_metadata.py -q`（先 RED 再 GREEN）
- 一个 commit，中文说明**为什么**；`git push -u origin HEAD`
- 评论必须含完整 40 位 SHA、changed files、pytest 退出码与数量、`git diff --check`
- **禁止**自行 FF 主干、禁止部署、禁止改 `.env`/token

独立审核员对 **exact SHA** 只读复审。Cursor 隔离复测写「准予合入」后才线性 FF。
