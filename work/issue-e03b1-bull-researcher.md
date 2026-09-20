# E-03b-1：Bull researcher 命题引用边界

实现 owner：资深开发1。后续同 SHA 代码审查统一指派“代码审核员”。

## 基线与工作树

- 从 `origin/codex/dav-4-p2a-trunk` 当前精确基线
  `59713d3744bc4563e34a59173c4f12a16901bc9d`（或其线性后代）开工。
- 必须使用干净、隔离的 worktree；不得在脏宿主上 reset、clean、broad-stage 或施工。
- 交付一个候选 commit，报告完整 40 位 candidate SHA、第一父、远端 ref、`git status --short`、`git diff --name-only parent..candidate` 和 diffstat。

## 本卡唯一关注点

按整合施工计划 v1.1 的 E-03b-1，只收紧 Bull 研究员在命题引用上的边界；不提前实施 E-03c / E-03d。

必须保持现有辩论轮次、阶段和状态语义：

1. v2 Opening 的 Bull 首次发言没有对手命题可引用。其 prompt、重试 prompt 和机读块必须保持双盲约束：`responded_claim_ids=[]`，每条 `new_claims[].target_claim_ids=[]`，不得生成或提示占位的对手 claim ID。
2. 任一命题引用字段若带有当前 claim ledger 不存在的 ID，必须 fail-closed；不得先静默过滤、再把未知引用留在可回溯状态中。现有合法对手 claim 的引用仍按当前阶段和阵营规则处理。
3. 不能借本卡改变辩论次数、Opening/Challenge/Tiebreak 切换、3/1 现有设置、相似度/重复观点闸、风险辩论或 manager prompt。
4. E-03a 的 `applicability` / `invalidation_conditions` 仍是纯契约；本卡不得让它们改变投票、权重、概率、确认闸或交易执行。不得发明平行字段名、schema、数据库字段或默认 probability/confidence。

## 允许修改

- `tradingagents/agents/researchers/bull_researcher.py`
- 仅当上述 fail-closed 约束无法在研究员入口完成且有测试证明必要时，才允许最小修改 `tradingagents/agents/utils/debate_utils.py`；不得顺手重构共享协议。
- `tests/test_claim_review_contract.py`（新建）及为本卡必要的 Bull 现有协议测试；不得改写既有正确断言来逃避回归。

禁止修改 `bear_researcher.py`、`evidence_verifier.py`、`decision_status.py`、`research_manager.py`、prompts、数据库/schema、provider、配置、部署脚本；禁止 FF、部署、重启、真实 LLM/网络调用和生产库写入。

## 最低测试

新增 `tests/test_claim_review_contract.py`，至少覆盖：

- Bull v2 Opening 无对手 claim 时不出现占位对手 ID，合法 Opening 仍通过；
- unknown `responded_claim_ids` / `target_claim_ids` / challenge target 被拒绝，且不会进入 accepted round/state ledger；
- 已存在的合法对手 claim 在非 Opening 阶段仍可引用；
- 既有 Opening、Challenge、information-gain 和 manager pre-gate 测试不回归。

使用仓库已配置的 Python 3.10 解释器，交付报告写出完整可复制命令和真实 collected/passed/failed/skipped 数字及耗时。至少运行：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q \
  tests/test_claim_review_contract.py \
  tests/test_debate_opening_protocol.py \
  tests/test_debate_challenge_protocol.py \
  tests/test_debate_e2e_protocol_repair.py \
  tests/test_debate_information_gain.py
git diff --check
```

交付状态置 `in_review`，不要自行合入。评论必须逐项说明白名单、失败集合/新增回归、未做的 E-03c/d 边界；候选交付后由“代码审核员”只读审查同一 SHA。
