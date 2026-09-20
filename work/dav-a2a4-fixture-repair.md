# A2/A4 回归：研究总监夹具适配 fail-closed

## 背景（已核实）

主干 tip：`a25d404a63bde3c97217ffc1a945ff06b8f1bf9b`（已推 `origin/codex/dav-4-p2a-trunk`）。

A2/A4 已合入：
- A2：`market_data_context` 显式传入（含 `{}`）且无可用日线 → manager winner 强制 `tie`
- A4：冲突资金流 dispute → 行/裁决强制 `tie`；tie 且仓位 >30% → consistency 失败

`research_manager` 现始终把 `state["market_data_context"]`（缺省 `{}`）传入校验器，因此**夹具若不提供 available OHLCV provenance，方向性裁决会被打成 tie**。

## 已复现失败（本关注点必须绿）

```text
tests/test_debate_b3_protocol.py::TestB33DisputeMapAndChallengeConsistency::test_dispute_map_extracted_and_normalized
  → A4：dispute 含「净流入+净流出/吸筹」→ tie；position_pct=60 → consistency fail

tests/test_debate_e2e_protocol_repair.py::TestResearchManagerHardGateAndClaimsValidation::test_six_round_successful_fixture_passes_manager_gate
tests/test_research_manager_claim_evidence_coverage_gate.py::TestResearchManagerIntegrationWithEvidenceGate::test_research_manager_runs_and_passes_valid_adjudication
tests/test_research_manager_seven_reports_and_verdict_gate.py::TestResearchManagerIntegrationNode::test_research_manager_executes_and_persists_verdict_and_manifest
  → A2：state 缺可用 stock_data provenance → tie → 60% 仓位 fail
```

错误共性：`势均力敌/观望裁决下建议仓位(60.0%)过高，不得高于30%`

## 只做

1. **隔离分支**从当前主干 tip 开：`agent/cursor/a2a4-fixture-repair`（或等价命名），禁止改脏文件 `AGENTS.md` / `frontend/src/services/api.ts` / `work/h1b_gates_report.json`。
2. 修测试夹具，**不要削弱** A2/A4 生产闸门：
   - 需要方向性 bull/bear 且高仓位通过的集成测试：在 `market_data_context` 中提供 `source_provenance.stock_data = {status: available, as_of: "..."}`（或等价可用日线证据）。
   - `test_dispute_map_extracted_and_normalized`：要么改为断言 A4 行为（冲突→tie + 高仓位阻断），要么把 dispute 改成**无冲突**资金流表述并保留 bull；二选一，优先保留一个明确的 A4 正向夹具 + 一个无冲突规范化夹具。
3. 禁止改 `evidence_verifier.py` / `research_manager.py` 去“迁就旧夹具”，除非发现真正的产品 bug（须先在 issue 评论说明并停手等确认）。
4. 精确 pathspec 提交；一个 commit 一个关注点。

## 验收命令

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q --tb=short \
  tests/test_debate_b3_protocol.py::TestB33DisputeMapAndChallengeConsistency::test_dispute_map_extracted_and_normalized \
  tests/test_debate_e2e_protocol_repair.py::TestResearchManagerHardGateAndClaimsValidation::test_six_round_successful_fixture_passes_manager_gate \
  tests/test_research_manager_claim_evidence_coverage_gate.py::TestResearchManagerIntegrationWithEvidenceGate::test_research_manager_runs_and_passes_valid_adjudication \
  tests/test_research_manager_seven_reports_and_verdict_gate.py::TestResearchManagerIntegrationNode::test_research_manager_executes_and_persists_verdict_and_manifest \
  tests/test_ohlcv_fail_closed_verdict.py \
  tests/test_fund_flow_dispute_tie.py
```

全部通过后：推远端分支，在 issue 留下 **精确 SHA**、diff 文件列表、上述命令输出摘要。不要自行 FF 主干；等审核/运维合入。
