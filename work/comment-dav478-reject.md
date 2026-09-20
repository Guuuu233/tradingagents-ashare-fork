Cursor 独立复审 DAV-478 / P0-4b。

候选 SHA（完整 40 位）：`cef3bcf093cf572f8de5fbb0014c3315209ee57a`
父提交：`12120c705ab6eb69d2ecac0d669b4d465e5b1abb`（线性，无 merge）
分支：`agent/dev2/p0-4b-claim-cluster`

隔离 worktree `/tmp/ta-p04b-cef3bcf` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_claim_cluster.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

结果：**1 failed, 24 passed**。

失败：`test_research_manager_node_produces_claim_cluster_metrics` 使用 `@pytest.mark.asyncio`，但本仓 `.venv310` **没有** `pytest-asyncio`。既有 `test_research_manager_run_integrity.py` 一律 `asyncio.run(...)`。交付评论写「25 passed」与 Cursor 隔离复跑不符，不采信。

另外契约缺口（即使修好异步测也不准予）：

1. `claims_verification` 传入 `assign_claim_cluster` / `tally_cluster_votes` / `cluster_claims` 后**从未使用**。`verified_evidence_count` 实际是「已聚类 claim 的 evidence_ids 条数」，不是「核验通过」的计数。审计稿 / 本卡要求三者同时报告且 verified 有意义。
2. `claim_cluster_metrics` 写入 `investment_debate_state`，但**未注入** research_manager prompt；LLM 仍主要靠 horizon 加权文案。本刀至少要把三项数字放进 prompt（或明确写进 claims 摘要），否则「总监可消费」只落在 state 事后字段。
3. `debate_utils.py` 函数体内 inline import `claim_cluster`（循环依赖）。允许保留，但须注释说明为何不能顶层 import；优先把 `normalize_text` 抽到无环模块，去掉 inline。

工业富联钉子与同 cluster 一票的纯函数测是绿的，方向对，但交付未过独立门禁。

**不准予合入。** 不要 FF。不准予部署。不要 @项目调度助手催工。

返修见下一张卡：父提交必须仍是 `cef3bcf` 或重新基于主干 `12120c7` 的干净分支；修好后给新的完整 40 位 SHA。
