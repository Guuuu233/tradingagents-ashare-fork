# DAV-846 同 SHA 只读代码审查：E-03b-1 Bull researcher

审查 owner：代码审核员。此卡只读；不得改代码、提交、推送、FF、部署、重启、真实 LLM/网络调用或生产库写入。

## 审查对象

- candidate：`4dc18b0bbb84dbdf170aa1e78b76eb9d3cda9c89`
- direct parent：`59713d3744bc4563e34a59173c4f12a16901bc9d`
- remote ref：`origin/agent/1/01a09529`，应回读到 candidate SHA
- 原施工序列：2-2 / E-03b-1
- 工作树：必须从 candidate SHA 新建干净 detached worktree；不得在脏宿主上审查

## 白名单

candidate 相对 parent 只能包含：

1. `tradingagents/agents/researchers/bull_researcher.py`
2. `tests/test_claim_review_contract.py`

禁止出现 `bear_researcher.py`、`evidence_verifier.py`、`decision_status.py`、`research_manager.py`、prompts、schema/database、provider、配置或部署脚本改动。

## 必查语义

1. v2 Opening 的 Bull 首轮保持双盲：初始 prompt、重试 prompt、机读块均不得生成或提示占位对手 claim ID；`responded_claim_ids=[]`，每条 `new_claims[].target_claim_ids=[]`，且不得通过额外引用字段绕过。
2. 当前 claim ledger 不存在的 ID 出现在 `responded_claim_ids`、`target_claim_ids`、challenge target 或其他 claim 状态引用字段时，必须在 accepted round/state ledger 之前 fail-closed；不能先静默过滤后接受，也不能把未知 ID留在可回溯状态。
3. 已存在的合法对手 claim 在非 Opening 阶段仍可引用；不得改变既有 Opening/Challenge/Tiebreak、3/1、重复观点/信息增量、风险辩论或 manager prompt 语义。
4. retry 的坏块不得增加 count、round_messages accepted 数、challenge ledger 或 claims；第二次合法块只能推进一次。
5. E-03a 的 applicability/invalidation_conditions 仍是纯契约，本卡不得引入投票、权重、概率、确认闸、交易执行或新的 schema/database 字段；不得提前实施 E-03c/E-03d。
6. 检查新增测试是否真实覆盖上述行为，且没有改写既有正确断言来制造通过。

## 复现与对照

使用 Python 3.10，`env -u PYTHONPATH`。至少复跑：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q \
  tests/test_claim_review_contract.py \
  tests/test_debate_opening_protocol.py \
  tests/test_debate_challenge_protocol.py \
  tests/test_debate_e2e_protocol_repair.py \
  tests/test_debate_information_gain.py
git diff --check
```

已知同口径证据：parent 为 65 passed；candidate 为 78 passed；新增 13 项。另 9 个既有相关 debate 测试在 candidate 为 102 passed、2 个既有 JWT 警告。请以实际复跑为准，并报告 collected/passed/failed/skipped/耗时。

## 交付格式

评论必须报告：精确 candidate/parent、实际白名单与 diffstat、测试命令和结果、逐项语义结论、是否发现 blocker。只读审查结论只能是 `PASS` 或 `FAIL/BLOCKED`；不要把审查结论当作合入、部署、启动或真实数据采集授权。
