# Track A5 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/dev2/a5-tplus5-shadow-backfill`
- 候选 tip SHA：`d2f8aa05579d0520abe942972889a82060ea65e7`
- 父 / 基线：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`
- 关联：DAV-540
- brief：`work/issue-a5-tplus5-shadow-backfill.md`

## 期望

- v2 completed 报告：严格交易日历 T+5 后按 `manager_verdict.winner` 写 `t_plus_5_direction_hit`
- `pending_due` / `suspension` / `data_missing` 语义正确；禁止缩短 hold
- dry-run；写回幂等；**不开** `credit_weighting_enabled`
- 无 Gate4 / 无删 legacy

## 动作

detached checkout tip；`git diff --stat` 对照交付范围；`.venv310` 跑 `tests/test_tplus5_shadow_backfill.py`（及 `test_shadow_credit` / `test_h1b_gates` 若触及）。

## 禁止

改代码 / FF / 部署 / @调度助手催合入；PASS ≠ 准予合入。

## 交付

✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。
