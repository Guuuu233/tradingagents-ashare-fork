# DAV-614：钉住东财新闻「新闻链接」写入 markdown Link，并接到 coverage url

**父 tip：** `9b3de9b0153e4727896692e42d22223a9b0f4efe`  
**依据：** DAV-610/612 已合入 URL 聚类与跨源 hash，但 `parse_news_markdown` 只认正文 `Link:`。`cn_akshare_provider.get_news` 已从列名 `新闻链接`/`链接` 取值；现有 vendor 测试**没有**断言写出 `Link:`。本卡只钉这条已有路径，**不接新接口、不发明 canonical_event_id。**

## 一个关注点

生产 `get_news` 若丢掉 `Link:` 行，610/612 在主路径上等于没接线。

## 契约

1. Mock `ak.stock_news_em` 返回含列名 `新闻链接`（非空、非 nan）的 DataFrame，且发布时间在窗口内：输出 markdown **必须**含对应 `Link: <url>`（按列名取值，禁止位置切片）。
2. 该 markdown 经 `parse_news_markdown_to_evidences` 后 evidence.url 为规范化 URL。
3. `新闻链接` 缺失 / 空 / nan：不得编造 URL，不得写假 `Link:`。
4. 不改 DAV-608 `recall_status`，不改 clustering 规则本身。

## 允许改

- `tests/test_vendor_chain_semantics.py`（或同目录仅因本卡新增的测试；优先改原测试文件）
- 仅当 RED 证明生产路径没写 `Link:` 时，才改 `tradingagents/dataflows/providers/cn_akshare_provider.py` 原 `get_news` 路径

禁止改其它 provider、禁止 CNINFO、禁止补样本、禁止部署。一个 commit。必须 push origin。报告 40 位 SHA。
