# Track A8 独立代码审核（只读）

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/dev2/a8-calibration-v2-sample-honesty`
- 候选 tip SHA：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`
- 父 / 基线：`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`
- 关联：DAV-550
- brief：`work/issue-a8-calibration-v2-sample-honesty.md`

## 期望

- 合格 v2、`winner∈{bull,bear}`、`probability=NULL` 可进入校准样本（hold/outcome 纪律不变）
- **禁止** confidence→probability；buckets/Brier 仅用显式 probability
- 返回体可观测 `winner_only_admitted`（或等价）
- 旧 probability 路径不变；不开加权；不部署；无 schema

## 动作

detached checkout tip；diff 对照；`.venv310` 跑 `tests/test_calibration_service.py`（及 `test_decision_status` 若触及）。

## 禁止

改代码 / FF / 部署 / @调度助手催合入；PASS ≠ 准予合入。

## 交付

✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。
