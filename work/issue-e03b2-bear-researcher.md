# E-03b-2：Bear researcher 命题引用边界

实现 owner：资深开发1。后续同 SHA 代码审查统一指派“代码审核员”。本卡按 2-2 严格接续 DAV-846，不能与 E-03c/E-03d 并行施工。

## 基线与工作树

- 从当前 trunk 精确基线 `4dc18b0bbb84dbdf170aa1e78b76eb9d3cda9c89`（DAV-846 合入后的远端 `codex/dav-4-p2a-trunk`）或其线性后代开工。
- 必须使用干净、隔离的 worktree；不得在脏宿主上 reset、clean、broad-stage 或施工。
- 交付一个候选 commit，报告完整 40 位 candidate SHA、第一父、远端 ref、`git status --short`、`git diff --name-only parent..candidate` 和 diffstat。

## 本卡唯一关注点

只收紧 Bear 研究员在命题引用上的边界，使 Bull/Bear 两侧对同一 claim ledger 遵守对称的 Opening 双盲和未知引用 fail-closed 契约。

1. v2 Opening 的 Bear Opening 发言仍是独立双盲立论：`responded_claim_ids=[]`，每条 `new_claims[].target_claim_ids=[]`，不得生成、提示或回退到占位的对手 claim ID；重试 prompt 也不得出现伪造 ID。
2. 任一命题引用字段若带有当前 claim ledger 不存在的 ID，必须在 accepted round/state ledger 之前 fail-closed；不得先静默过滤再接受，也不得把未知引用留在可回溯状态。该未知引用闸适用于 legacy 与 v2 的所有阶段；仅 Opening 双盲的额外字段约束属于 v2。至少覆盖 `responded_claim_ids`、`target_claim_ids`、challenge target、`resolved_claim_ids`、`unresolved_claim_ids`、`next_focus_claim_ids`。
3. 已存在的合法对手 Bull claim 在非 Opening 阶段仍可引用；不得改变既有辩论轮次、Opening/Challenge/Tiebreak 切换、3/1 设置、相似度/信息增量硬闸、风险辩论或 manager prompt。
4. 坏块重试不得推进 `count`、accepted `round_messages`、claims 或 challenges；第二次合法块只推进一次。
5. E-03a 的 `applicability` / `invalidation_conditions` 仍是纯契约，不得改变投票、权重、概率、确认闸、交易执行；不得发明平行字段、schema 或数据库字段。

## 允许修改

- `tradingagents/agents/researchers/bear_researcher.py`
- `tests/test_claim_review_contract.py`（在 DAV-846 已有测试基础上继续补 Bear 场景；不得删改既有 Bull 断言以逃避回归）
- `tests/test_debate_seven_reports_and_protocol_gate.py`（仅修正 Bear 的 legacy Opening 测试桩：空 claim ledger 不得硬编码 `INV-1` 引用；不得改动七报告断言或生产契约）

禁止修改 `bull_researcher.py`、`debate_utils.py`、`evidence_verifier.py`、`decision_status.py`、`research_manager.py`、prompts 模板、数据库/schema、provider、配置、部署脚本；禁止 FF、部署、重启、真实 LLM/网络调用和生产库写入。

## 最低测试

在 `tests/test_claim_review_contract.py` 增加并逐项覆盖：

- Bear v2 Opening 无对手引用、不出现占位 ID，合法 Opening 进入 state ledger；
- Opening 重试提示不出现占位 ID；
- unknown `responded_claim_ids` / `target_claim_ids` / challenge target / 状态引用被拒绝，且不进入 accepted round/state ledger；
- 当前账本中存在的合法 Bull claim 在 Challenge/Tiebreak 仍可被 Bear 引用；
- 坏块重试恢复和连续失败的状态推进语义不回归。

使用仓库已配置的 Python 3.10 解释器，交付报告写出真实 collected/passed/failed/skipped 数字及耗时，至少运行：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q tests/test_claim_review_contract.py tests/test_debate_opening_protocol.py tests/test_debate_challenge_protocol.py tests/test_debate_e2e_protocol_repair.py tests/test_debate_information_gain.py
git diff --check
```

并复跑当前已有的 9 个相关 debate 测试文件，报告与 DAV-846 的失败集合对照。交付状态置 `in_review`，不要自行合入；候选交付后由“代码审核员”对同一 SHA 只读审查。
