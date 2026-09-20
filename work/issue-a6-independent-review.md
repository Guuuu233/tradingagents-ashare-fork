# Track A6 独立代码审核（只读）— rebase tip

## 角色

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)

## 候选（已填实）

- 分支：`origin/agent/cursor/a6-h1b-gates-v2-only`
- 候选 tip SHA：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`
- 父 / 基线：`9d5d53dd89bed6dcbfa7dd3aa991771993dddfea`
- 关联：DAV-453
- brief：`work/issue-a6-rebase-resume.md`

## 期望

单 commit：门槛脚本只计 v2 completed 样本；无 v2 winner 排除出分母；industry 缺失不硬编；**不开** `credit_weighting_enabled`。

白名单仅：
- `scripts/verify_h1b_gates.py`
- `tradingagents/agents/utils/shadow_credit.py`
- `tests/test_h1b_gates.py`

（不应含 `work/h1b_gates_report.json`）

## 动作

detached checkout tip；diff --stat 对照白名单；`.venv310` 跑 `tests/test_h1b_gates.py`（及相关若触及）。

## 禁止

改代码 / FF / 部署 / @调度助手催合入；PASS ≠ 准予合入。旧 tip `f2905da` 的审核作废。

## 交付

✅/⚠️/❌ + 完整 tip SHA + pytest + 路径:行号。
