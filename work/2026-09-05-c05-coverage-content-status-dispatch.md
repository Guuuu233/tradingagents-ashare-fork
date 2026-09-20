# C-05 切片 10：event_coverage 诚实报告巨潮 `content_status`（不解析 PDF）

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `705b4a684b0e09c417c94b427c1ceb500a906722`（或其线性后代）。  
**一个关注点：** 切片 9 已把 `content_status`/`content_sha256` 写进 record。`build_news_event_coverage` 与 `format_event_coverage_summary` 仍不计数、不注入，模型会把标题级公告当成已核验正文。本卡只把**已有字段**变成 coverage 计数 + 紧凑摘要句。  
**禁止：** 解析 PDF/抽取财报数字；改 `qualify_cninfo_content` 资格规则；改聚类；接线 `anns_d`；编造 URL；把 `unavailable`/`not_attempted` 写成「确认无公告」或「无此公告」；把 `hashed` 写成「已读年报/已验证净利润」；C-09-3；社交 AUTH；打印 token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`
- `tests/test_news_event_coverage.py`（追加本卡用例；不要重写无关旧用例）

`data_collector.py` **不要改**，除非现有 `_fetch_all` 已传入的 envelope/evidence 读不到 `content_status`（应能从 record/`raw_item` 读到）。不要新写下载器。

## 契约

1. `build_news_event_coverage` 返回值增加（或等价具名字段）：`cninfo_content_hashed_count` / `cninfo_content_unavailable_count` / `cninfo_content_not_attempted_count`。只统计巨潮主源 record（公告/IR），不要把媒体 markdown 算进去。缺字段按 `not_attempted`。
2. `hashed` 计入命中事件（已有 C-05d：未 hashed 也仍是事件，不得从 `query_manifest` 抹掉）。
3. `format_event_coverage_summary` 必须出现正文资格计数，并写明：hashed 只证明 cutoff 前取得 PDF 字节，不是已抽取财务数字；unavailable/not_attempted 不是确认无公告。
4. 摘要文本不得出现「确认无公告」「已验证净利润」「已读年报」。
5. 单测 mock 记录即可，禁止真网。至少：hashed+unavailable 混合计数；摘要含禁止误读的说明；unavailable 不改变 `provider_failure` 语义。
6. `env -u PYTHONPATH` + `.venv310/bin/python -m pytest tests/test_news_event_coverage.py -q`

一个 commit，push 功能分支，评论 40 位 SHA。不要自建审核卡、不要 @独立代码审核员。
