# DAV-610：新闻去重纳入 URL（禁止假装 canonical_event_id）

**父 tip：** `4f6e45ec4a9a87da32c24ebfaea92f9ecf66dd9d`  
**依据：** DAV-598 只读审计（url 完全未参与去重；`source_hash` 含 source 导致跨源无法哈希对齐）。DAV-608 已合入召回诚实化。本卡**不是**交易所公告文号，**不是** CNINFO。

## 一个关注点

`NewsEvidence` 已有 `url` 字段，但 `build_news_event_coverage` 构造 evidence 时不传入 url；`compute_source_hash` 与 `cluster_news_evidences` 也不看 url。同一篇文章被两家媒体转载、标题略有差异时会裂成两个 cluster。

本卡只让**已有 URL** 进入身份与聚类。禁止发明 `canonical_event_id`、禁止编造公告备案号、不接新 provider、不改召回清单语义、不补 H1b、不部署、不开加权。

## 语义

1. 从 raw item 按列名取 url（`url` / `链接` / `link` 等已有键；缺则 `None`，禁止填默认 URL）。
2. 规范化：trim、去 fragment。解析失败则当作无 URL，不得用今天或空串冒充。
3. 两条都有非空规范化 URL 且相等 → **必须**同一 `EventCluster`，即使 `source` 不同、标题不完全相同。
4. `compute_source_hash`：有 URL 时把规范化 URL 纳入哈希；无 URL 时保持现有 title/source/published_at/summary 行为（不要为了跨源对齐而去掉 source，那是下一刀）。
5. 不得新增或填写 `canonical_event_id`。不得声称「同一底层公告」。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`
- `tests/test_news_event_coverage.py`

禁止改 `data_collector.py` / `news_analyst.py` / providers / `evidence_verifier.py`。原路径改，禁止 `_v2`。

## RED（修复前必须失败）

同一规范化 URL、不同 source、标题不完全相同的两条 cutoff 前新闻 → `hit_count==1`（一个 cluster）。无 URL 的既有标题聚类测试不得回归。

## 禁止

CNINFO/巨潮/IR 新接口；`canonical_event_id`；改 DAV-608 的 `recall_status` 合同；自动 FF；部署；补样本；`credit_weighting_enabled`。

## 验收

一个 commit；隔离 worktree 从父 tip 开分支；pytest 命令/退出码/数量；40 字符 SHA；changed files；`git status`；`git diff --check`。**必须把分支 push 到 origin**（上一张卡漏推，审核无法 fetch）。然后独立审核 exact SHA。
