# 157 条 manager_consistency_hard_gate ABSTAIN 离线复放（2026-09-20）

- 基线：trunk `3907c73`（代码面 = 部署版 `4297715`，docs-only diff 为空）
- 解释器：`.venv310` Python 3.10.20，`env -u PYTHONPATH`
- 方法：冻结 result_data 内全部输入（judge_decision 原始裁决文本、claims、七报告、
  market_data_context、expectation_revision、adjudication 列表），重跑当前代码的
  确定性后裁决链：`evaluate_claims` → `extract_and_validate_manager_verdict` →
  `validate_manager_expectation_revision_consumption` → `status_from_manager_verdict`。
  覆盖 DAV-1068（dcg 审计）、DAV-1071（E-04 豁免）、DAV-1088（matcher）、DAV-1091
  （is_fatal）、DAV-1093（reason_codes）全部修复层。
- 全程只读（`mode=ro` + `query_only`），零写库。

## 结果

| 复放后 | 条数 | 占比 |
|---|---|---|
| 仍 ABSTAIN + hard_gate | 148 | 94.3% |
| 翻转 VALID | 9 | 5.7% |

9 条翻转的动作面：VALID/SELL·CONFIRMED ×1（1d5a4c93）、VALID/HOLD·CONFIRMED ×1
（ef1a4a16）、VALID/WAIT ×7（D-009 §5 仍排除）。

148 条继续拦截按"旧因是否复现"拆：

- **STAY_同因 68**：存储 failed_checks 在现代码下逐字复现（E-04 真实断言为主——
  引述/条件句豁免不适用，属正确拦截）。
- **STAY_异因 80**：旧因消失，但被更深的闸接住——
  - 去重审计传播后 `unadjudicated` 名单收缩到真正未裁决残余（如 INV-6）；
  - 重算核验后发现 `裁决采纳证据覆盖率不足(50%<67%)` / `正文标注未核实 claim`
    / `verdict_consistency_rejected_adopt` 等**另一类实质违例**。

## 结论

1. 修复按设计生效：dcg 审计来源正常消化（155/157 存量即含 `double_count_guard_audit`），
   E-04 豁免按预期放行引述/条件句命中。
2. **这批存量主要是真实拦截，不是缺陷产物**：经理在 ~50% 证据覆盖下采纳 claim 是
   主流违例。修复不会把存量转绿，H1b 样本只能来自修复后的新跑批。
3. 9 条翻转仅作诊断证据；按审计红线历史报告不得改判、不得计入前向 cohort。

## 保真度声明（诚实边界）

- 复放测的是"同输入下当前代码是否仍拦"，**不回答**"重跑上游是否会产出不同输入"；
- `challenges`/`social_data_context` 存量为缺席，按 None/[] 传入；
- `analysis_baseline_date` 缺失时回落 `trade_date`；
- 复放脚本：`work/2026-09-20-hardgate157-replay.py`（主口径）与
  `work/2026-09-20-hardgate157-replay-detail.py`（同因/异因拆分），随本文档入库。
  复跑：`env -u PYTHONPATH .venv310/bin/python work/2026-09-20-hardgate157-replay.py`。
- 执行主体：本会话（非 agent 卡），用户放行后只读执行；属诊断证据非代码候选，
  不走 RED/同 SHA 复审——但脚本已入库，任何人可复跑复核。
