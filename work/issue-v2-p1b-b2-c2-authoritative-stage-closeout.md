## 固定输入与基线

- fresh基线：`target/agent/2/128052b9e828@91b4cf51b80fb3edae0d6a2df02ae9928dd665a8`
- 冻结C2补丁：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav405-uncommitted.patch`
- 补丁SHA-256：`836c57963c0c7e49401444b9760f760fd513b34d4218245bf7fe69415d57c128`
- 冻结C2测试：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav405-test_debate_challenge_protocol.py`
- 测试SHA-256：`67e589c1a745c2cb6d59484c27ea004134b6e39f74a71d5912a93cdb510abc77`
- DAV-405未提交/未推送；其19项C2与139项矩阵曾绿，但三处stage推导错误，禁止原样提交。

fresh checkout后先校验hash；应用补丁与复制测试。禁止复用原worktree。允许文件仅`tradingagents/agents/utils/debate_utils.py`与`tests/test_debate_challenge_protocol.py`。

## 唯一缺陷

补丁中三处存在：
`current_stage == "challenge" or message_index in (3, 4)`
导致显式`protocol_stage="tiebreak"/"manager"`但count异常/恢复态被强行拉回challenge。权威stage必须优先。

## 严格TDD

1. 应用冻结现场后，先新增测试、不得先改生产：
   - v2 state显式`protocol_stage="tiebreak"`, `count=2`，表面合法Challenge payload不得被`validate_debate_response`当作Challenge合法动作；至少断言不能返回valid/不能绕过后续stage规则。
   - v2 state显式`protocol_stage="manager"`, `count=2`，调用`update_debate_state_with_payload`处理missing/invalid或表面Challenge时，返回state与attempt/round_message不得把stage/protocol_stage写成challenge。
   - 如保留stage缺失兼容回退，再单独测试`"protocol_stage" not in state`才可按message_index推导；不需要则不实现。
2. 宿主3.10运行上述测试，在当前补丁上必须RED，失败原因是被错误拉回challenge，不得是语法/fixture/环境错误。
3. 最小GREEN：
   - validate路径：显式stage存在时，`is_challenge_stage = v2_enabled and current_stage == "challenge"`；
   - accepted update与unaccepted attempt：显式stage存在时绝不按message_index覆盖；challenge时debate_round=2；tiebreak/manager保持其stage，round可按message_index计算但不得改stage；
   - 仅字段真正缺失时才可受测回退。
4. 搜索候选源码不得再有`current_stage == "challenge" or message_index in (3, 4)`或等价显式stage覆盖。

## 保留既有C2行为

- C2专项现有19项全部保留：硬闸、duplicate、CH-1/CH-2账本、失败attempt、四消息推进；不得弱化。
- 不修改prompt/retry/verifier/conditional_logic/setup.py/api/main/manager/agent_states/propagation/DB/config/frontend/provider。

## 验收

- 新恢复态RED→GREEN；
- C2专项（含新增测试）、C1 foundation13、Opening16、legacy e2e/bundle wash/information gain、protocol metadata/state persistence/metrics：宿主`.venv310`全绿；
- replay、compileall、diff-check；不跑全量；
- changed files恰好2个；
- 新远端branch/SHA，直接父91b4cf5；报告hash、RED/GREEN、测试、范围；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。