# DAV-612：有规范化 URL 时 source_hash 跨源对齐（仍禁止 canonical_event_id）

**父 tip：** `158ccb7e9df49ba0d0510d1a50b4cc82c6c3ed55`  
**依据：** DAV-610 brief 写明「无 URL 时不要为了跨源对齐而去掉 source，那是下一刀」。610 已按 URL 相等聚类，但 `compute_source_hash` 在有 URL 时仍把 `source` 编进哈希，同一 URL 跨源哈希仍不同。

## 一个关注点

有非空规范化 URL 时，`source_hash` 必须由该 URL（及现有 title/published_at/summary 中**不含 source** 的部分，或仅 URL）决定，使同一规范化 URL、不同 source 的两条得到**相同** `source_hash`。无 URL 时保持 610 行为：哈希仍含 source。

禁止发明 `canonical_event_id`、不接 CNINFO、不改聚类规则（URL 相等已合并）、不改 `recall_status`、不补样本、不部署、不开加权。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`（`compute_source_hash` / `NewsEvidence.__post_init__` 调用处）
- `tests/test_news_event_coverage.py`（610 里「有 URL 时跨源哈希不相同」的断言必须改为相同；无 URL 跨源仍不同）

禁止改 providers / collector / analyst。原路径改。禁止 `_v2`。

## RED（修复前必须失败）

同一规范化 URL、不同 source → `compute_source_hash` 结果相等。无 URL、不同 source → 哈希仍不相等。

## 禁止

CNINFO；`canonical_event_id`；改 DAV-608 召回合同；自动 FF；部署；补样本。必须 `git push origin` 分支并报告 40 位 SHA。
