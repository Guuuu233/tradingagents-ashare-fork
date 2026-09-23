# DAV-1227 样本供给漏斗只读诊断

- 库快照：`/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/dav-1227-6ef1ca12addc/workdir/tradingagents-ashare-fork/work/prep-b-supply-funnel/snapshot.db` sha256=`bda9e74cc7587ce98d614c6927da0b958abbfc0db17441bd5c792c86d3c9b18d` 回读时刻 2026-09-23T14:54:51+00:00
- 账户 `429163f7-50b6-4982-8bdf-96ae99506843` completed 报告；production=`3d9c414` / trunk=`9d03c89`

## production view 漏斗
```
raw 489 → v2 388 → D-009 eligible 43 → clean 15
D-009 排除: abstain=234, invalid_run=2, legacy_null=69, wait=40
隔离: price_basis_contaminated=14, price_basis_contract_incomplete=1, price_basis_pending_review=13
```

## trunk view 漏斗
```
raw 489 → v2 388 → D-009 eligible 43 → clean 12
D-009 排除: abstain=234, invalid_run=2, legacy_null=69, wait=40
隔离: hold_conflict=3, hold_defensive=1, price_basis_contaminated=14, price_basis_contract_incomplete=1, price_basis_pending_review=13
```

## 按周分解（raw→v2→eligible→prod_clean/trunk_clean）
| week | raw | v2 | eligible | prod_clean | trunk_clean |
|---|---|---|---|---|---|
| 2026-W31 | 23 | 0 | 0 | 0 | 0 |
| 2026-W32 | 29 | 0 | 0 | 0 | 0 |
| 2026-W33 | 13 | 0 | 0 | 0 | 0 |
| 2026-W34 | 50 | 14 | 0 | 0 | 0 |
| 2026-W35 | 55 | 55 | 0 | 0 | 0 |
| 2026-W36 | 58 | 58 | 7 | 7 | 6 |
| 2026-W37 | 4 | 4 | 2 | 1 | 1 |
| 2026-W38 | 218 | 218 | 19 | 4 | 3 |
| 2026-W39 | 39 | 39 | 15 | 3 | 2 |

## 按入口分解
| entry | raw | v2 | eligible | prod_clean | trunk_clean |
|---|---|---|---|---|---|
| api | 205 | 186 | 32 | 11 | 10 |
| unknown(no workflow_context) | 284 | 202 | 11 | 4 | 2 |

## clean cohort 分解
```json
{
  "production_clean_by_cohort": {
    "legacy_unversioned": 6,
    "decision_model.v1:evidence_contract.v1:price_basis.unspecified": 9
  },
  "trunk_clean_by_cohort": {
    "legacy_unversioned": 6,
    "decision_model.v1:evidence_contract.v1:price_basis.unspecified": 6
  },
  "v1_cohort_clean": {
    "production": 9,
    "trunk": 6
  },
  "h1b_threshold_single_cohort": 60
}
```

## reason_codes Top-N（按 D-009 排除类）
### abstain（234 份）
归类合计: {"guard:e04_priced_in_beat_miss": 200, "guard:manager_consistency_hard_gate": 179, "other": 149, "guard:fund_flow": 103, "evidence_verification": 65, "legacy_narrative(verifier)": 16, "guard:price_basis_gate": 1}
- 179× `manager_consistency_hard_gate`
- 109× `failed_checks:E-04 守卫拦截`
- 71× `risk_verdict:blocked`
- 69× `E-04 守卫拦截：缺乏可回溯证据，经理不得将“已定价/priced in”当作已确证事实引用`
- 55× `direction_evidence_blocked`
- 48× `fund_flow_consensus_guard`
- 32× `fund_flow_guard:data_conflict`
- 23× `upstream_non_executable`
- 15× `E-04 守卫拦截：基本面无有效旧基线，经理不得在正文或裁决理由中断言业绩“超预期”`
- 10× `fund_flow_guard:selected`
- 10× `fund_flow_guard:blocked`
- 9× `unadjudicated_material_claims_adopt:INV-4,INV-5`
  - 注 guard:e04_priced_in_beat_miss: 09-19 后已修：DAV-1110 条件作用域、DAV-1135(35c33de) 名词性存在否定豁免（仅 trunk）；DAV-1093 起拦截细节从 reason_codes 归位 failed_checks
  - 注 guard:fund_flow: 09-19 后已修：DAV-1138(9d03c89) moneyflow failure taxonomy（仅 trunk）
  - 注 guard:price_basis_gate: DAV-1199/1200/1207/1211 已在 3d9c414 上线（两视图同口径）
### wait（40 份）
归类合计: {"other": 86, "evidence_verification": 57}
- 40× `manager_terminal`
- 22× `upstream_non_executable`
- 22× `risk_verdict:blocked`
- 3× `fatal_contradicted_claims:INV-6`
- 3× `partial_core_claims:verified=INV-2;unverified=INV-1`
- 3× `audited_rejected_claims:INV-4`
- 3× `unverified_core_claims:INV-1`
- 3× `partially_adopted_claims:INV-2`
- 3× `unverified_core_claims:INV-10`
- 2× `fatal_core_claims:INV-1`
- 2× `fatal_contradicted_claims:INV-3`
- 2× `fatal_core_claims:INV-2`
### no_trade（0 份）
归类合计: {}
### invalid_run（2 份）
归类合计: {"data_gap": 16}
- 2× `analyst_upstream_7_of_7_failed`
- 2× `market:prefix:分析报告生成失败`
- 2× `social:prefix:分析报告生成失败`
- 2× `news:prefix:分析报告生成失败`
- 2× `fundamentals:prefix:分析报告生成失败`
- 2× `macro:prefix:分析报告生成失败`
- 2× `smart_money:prefix:分析报告生成失败`
- 2× `volume_price:prefix:分析报告生成失败`
### data_error（0 份）
归类合计: {}

## E-04 型 ABSTAIN 四类复放（trunk 9d03c89）
E-04 ABSTAIN 报告 102 份；报告级分类：
```json
{
  "1_bare_assertion(裸断言)": 101,
  "9_no_hit_reproduced(存储文本未复现命中)": 1
}
```
occurrence 级 outcome：```json
{
  "blocked:bare_assertion": 319,
  "exempt:negated_or_rejected": 20,
  "blocked:evidenced_pricing": 20,
  "exempt:conditional": 18,
  "exempt:quotation": 8,
  "exempt:absence_negation": 3,
  "exempt:downweight_with_basis": 3,
  "exempt:noun_phrase": 2,
  "exempt:unknown_annotation": 1
}
```

## 每 100 份估算
```json
{
  "production": {
    "clean_per_100_completed": 3.07,
    "reports_needed_for_60_clean": 1956
  },
  "trunk": {
    "clean_per_100_completed": 2.45,
    "reports_needed_for_60_clean": 2445
  },
  "cohort_note": {
    "production_clean_by_cohort": {
      "legacy_unversioned": 6,
      "decision_model.v1:evidence_contract.v1:price_basis.unspecified": 9
    },
    "trunk_clean_by_cohort": {
      "legacy_unversioned": 6,
      "decision_model.v1:evidence_contract.v1:price_basis.unspecified": 6
    },
    "v1_cohort_clean": {
      "production": 9,
      "trunk": 6
    },
    "h1b_threshold_single_cohort": 60
  }
}
```