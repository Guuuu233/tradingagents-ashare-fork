Cursor 独立复审 DAV-479 / P0-4b 返修。

候选 SHA（完整 40 位）：`18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`
父链：`18e73bd` → `cef3bcf` → `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`（线性，无 merge）
分支：`agent/dev2/p0-4b-claim-cluster`

隔离 worktree `/tmp/ta-p04b-18e73bd` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_claim_cluster.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

结果：**26 passed in 0.36s**。与交付数字一致。不采信口头；本次为 Cursor 独立复跑。

相对主干 6 文件、+1048/−2，白名单内。未改资金流 P0-4a、财报 Q2、社交、受保护脏文件。

返修核对：
1. 异步集成测已改 `asyncio.run`，无 `pytest-asyncio` 依赖 → 绿。
2. `claims_verification` 已接入；`verified` 计入、`contradicted`/`unsupported`/`source_unavailable` 不计入；与真实 `evaluate_claims` 扁平 `{raw,claim_id,status}` 结构对齐（Cursor 探针核过）。
3. `format_claim_cluster_summary_for_prompt` 注入 `claims_text`；prompt 含三项计数；DAV-336 七分析师仍绿。
4. inline import 已注释循环依赖原因。

工业富联钉子：3 份 price-derived → 1 cluster；相对 1 个空头 cluster 权重 50%≠60%。P0-1 资金闸 ABSTAIN/`DIRECTION_NA` 回归仍绿。

残留（不挡合入）：聚类仍是关键词启发式；无核验时 `verified_evidence_count` fallback 为证据条数。后续可收紧，不挡本刀。

**准予合入** SHA `18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。不要 FF `cef3bcf`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
