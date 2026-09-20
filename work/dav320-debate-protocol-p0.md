# P0：多空辩论七报告完整输入与逐轮协议硬闸

**基线：`1eb280b35f436c2ff1ece00a448ad7483c86eff9`。独立分支，不合主干。与 DAV-316 provider 文件完全不冲突。**

## 已复现缺陷

1. `bull_researcher.py` / `bear_researcher.py` 只读取 market/sentiment/news/fundamentals/volume_price，遗漏 `macro_report` 与 `smart_money_report`。
2. 真实报告 `cb95eee4`、`c626ace1` 虽有6次发言和新claims，但所有新 claim 的 `target_claim_ids=[]`；最终状态没有逐轮 `responded_claim_ids` 轨迹，机器无法证明第2/3轮直接回答对方。
3. 非法/缺失 DEBATE_STATE 当前静默降级、count照增；同一阵营可把对方 claim 自行 resolved。

## 契约

1. Bull/Bear Prompt 输入补齐完整七报告：macro、market、sentiment、news、fundamentals、smart_money、volume_price；报告输入 manifest 记录每份长度与 passed=true。
2. `investment_debate_state` 新增并持久化 `round_messages[]`，每次发言记录：message_index、debate_round、speaker、cleaned_prose、parse_status、responded_claim_ids、new_claim_ids、target_claim_ids、resolved/unresolved、round_summary/goal；可选 model_name，但不得影响确定性测试。
3. 第1次发言可无 responded；从 Bear第1次发言（message_index>=2）起，合法机读块必须至少回应一个**对方已存在且尚未resolved的 claim**。否则 parse_status=invalid_protocol，不能把旧 claim 改状态；必须触发一次有限重试或 fail-closed 明确 blocked，不得静默计数通过。
4. `new_claims[].target_claim_ids`：后续轮至少一条新 claim 必须指向对方 claim；只响应ID但新claim全无target不合格。
5. 阵营权限：一方可 `responded` 对方 claim；不得自行 `resolved_claim_ids` 对方 claim。claim 只有：原作者撤回/认输，或 Research Manager裁决时才能 resolved。当前若没有总监结构化裁决，辩论阶段只标 addressed/unresolved。
6. 解析失败/坏JSON：保留正文但 parse_status=missing|invalid；不得伪装结构化成功，不得悄悄清空焦点；有限重试失败后明确失败/阻断。
7. 不修改用户配置、轮数3/1、provider、主干、风险辩论。

## 白名单

- `tradingagents/agents/researchers/bull_researcher.py`
- `tradingagents/agents/researchers/bear_researcher.py`
- `tradingagents/agents/utils/debate_utils.py`
- `tradingagents/prompts/zh.py`
- `tradingagents/prompts/en.py`
- `tradingagents/agents/utils/agent_states.py`（仅状态类型/默认值，如必要）
- 对应 debate/graph/report tests

## 验收

- 两个研究员 prompt fixture 明确包含七报告唯一标记。
- 6消息 fixture：第2-6条都必须回应对方ID，至少一条new claim target对方；round_messages完整。
- 非法块、空responded、响应己方、擅自resolve对方均红；有效协议绿。
- result_data现有持久化无需另改时，测试证明round_messages随investment_debate_state保留。
- `.venv310` 定向测试、compileall、diff-check；推远端精确SHA。
- 不 @项目调度助手。
