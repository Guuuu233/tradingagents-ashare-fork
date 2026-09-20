# P0：真实E2E辩论协议失败仍继续裁决返修

**基线：主干/线上 `e0036e537707fd1ca656c85fe384a9acdc4abf4b`。独立分支，不合主干。**

## 三行业真实复现

报告：京东方 `317bf7c9`、宁德时代 `53b4a996`、招商银行 `c0dab2d7`。
共同现象：
- investment count=6、round_messages=6；
- message1 Bull valid，生成INV-1/2；
- message2-6全部 parse_status=invalid_protocol；
- claims最终只有首轮Bull 2条；
- 状态无blocked/block_reason；
- Research Manager继续执行并全部判bull winner，manager_consistency_passed=True。

这是伪三轮辩论与单边裁决，阻断理想目标。

## 根因要求查证

1. Prompt的DEBATE_STATE `new_claims`示例/契约是否漏展示 `target_claim_ids`，导致模型稳定不输出；必须修中英文镜像。
2. update_debate_state/conditional routing为何 invalid_protocol 仍count+1并继续；blocked字段是否未进InvestDebateState/未被conditional_logic读取。
3. 研究总监为何不检查6条round_messages全valid、双方至少各有claim、后续轮回应对方。

## 契约

1. Prompt机器块示例每个后续new_claim必须含 `target_claim_ids:["INV-x"]`；第一轮Bull可空。正文明确第2-6次输出规则。
2. 每次研究员调用：首次协议无效时，在**同一发言序号**有限重试1次，重试Prompt附精确错误与合法focus IDs；第一次无效不得count，不得进入history作为有效发言，可在attempt trace留痕。
3. 重试仍失败：抛 typed `DebateProtocolError`，当前horizon失败；ReportDB/JobStore必须failed或双周期partial，禁止走Research Manager/Trader。
4. conditional_logic：只按有效消息数推进；`round_messages`可记录attempts，但需区分 accepted=false；最终成功报告必须恰好6条accepted valid消息。
5. Research Manager前置硬闸：count==6、accepted valid==6、Bull/Bear各3次、后5次responded非空且target对方、双方均有claims；不满足直接fail-closed，禁止裁决。
6. manager不得采纳不存在/未验证claim ID；adopted/rejected必须是当前claim账本子集且至少覆盖双方焦点。
7. result_data持久化 attempts/error，便于审计。
8. 不改轮数3/1、模型、provider、用户配置。

## 白名单
- bull_researcher.py / bear_researcher.py
- debate_utils.py / agent_states.py
- conditional_logic.py / setup.py（仅路由硬闸）
- research_manager.py
- zh.py/en.py
- graph/API生命周期必要透传
- 对应tests

## 验收
- Mock LLM：首次坏块、第二次合法 → count只增1、accepted一条、attempt两条。
- 连续两次坏块 → horizon failed、manager未调用。
- 6轮成功fixture：Bull/Bear各3，后5轮回应+target，claims双方均存在。
- manager前置缺Bear/invalid消息必须红。
- 真实风格机器块中英文Prompt测试。
- `.venv310`定向+全量、compileall、diff-check；推精确SHA。
- 禁止@项目调度助手。
