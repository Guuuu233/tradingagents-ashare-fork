# DAV-601：H1b cohort 隔离（CLI + 评价入口）

**父 tip：** `e10b106df9d3173258b0a3fefc90ba7f3559f109`  
**规格：** `work/2026-09-04-decision-model-version-isolation-design.md`（Cursor 冻结版，含 DAV-600 修正）

## 一个关注点

让 H1b gate **不能再无参数全库混算**。不写 PIT、不写新闻召回、不改 `evidence_verifier.py`（595 占用）、不补样本、不部署、不开 `credit_weighting_enabled`。

## 允许改

- `scripts/verify_h1b_gates.py`
- `tradingagents/agents/utils/shadow_credit.py`（评价入口的 cohort 过滤 / 同质性断言 / 空 cohort FAIL）
- 对应测试：`tests/test_h1b_gates.py`、`tests/test_shadow_credit.py`（只加本关注点用例）

## 必须做到

1. 未传 `--cohort`：非零退出，不写 PASS 报告。
2. `--cohort=legacy_unversioned`：只纳入缺版本字段的旧样本；不得把它们标成 v1。
3. 指定三元版本时：只纳入三字段全等的样本；SHA 只进 JSON 摘要，不当过滤主键。
4. 空结果：`passed=false`，`due_count==0` 不得 PASS。
5. 混世代同一次评价：拒绝（非零或明确 FAIL），不得静默合并。
6. 线上加权解析遇未标记样本：保持/降级权重 1.0，不抛崩主链路。
7. RED 测试先失败后修复。一个 commit。隔离 worktree。

## 禁止

- 改 `decision_status.py`、`evidence_verifier.py`
- 回填旧 121 为 v1
- 删样本、缩短 T+5、编造 probability
- 自动 FF / 部署 / 改 DB 生产数据

## 验收

完整 pytest 命令、退出码、数量；40 字符 SHA；changed files；`git status`；`git diff --check`。然后独立审核 exact SHA。
