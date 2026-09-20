## 固定对象

- 父候选：`agent/agent/96bbb432c8d7@b0127380634d55fa98778e6ce57b6546146e136b`
- 直接父：`a2b7515c7a549a642d4c6438cab36132bb0228b5`
- b012合法部分：Challenge TypedDict、InvestDebateState字段、Propagator空容器、DEBATE_STATE machine fields/sanitizer、foundation测试。
- b012越界部分：`validate_debate_response`新增`is_challenge_stage`/`elif is_challenge_stage`，以及foundation测试`test_validate_debate_response_accepts_challenge_payload`与对应import。该部分提前改变C2协议分相，禁止保留。

## 唯一任务：机械边界收口

从b012738 fresh checkout，只做以下删除：

1. `tradingagents/agents/utils/debate_utils.py`
   - 删除相对a2b新增的`is_challenge_stage = ...`；
   - 删除相对a2b新增的`elif is_challenge_stage:`分支及其仅用于绕过legacy Check B/C的内容；
   - 恢复为Opening分支之后直接进入原legacy `else`；
   - 保留machine fields、Challenge sanitizer和self_win_prob sanitizer；不得更改其行为。
2. `tests/test_debate_challenge_foundation.py`
   - 删除`validate_debate_response` import；
   - 删除整个`test_validate_debate_response_accepts_challenge_payload`；
   - 保留其余foundation测试断言，禁止弱化。
3. 禁止修改`agent_states.py`和`propagation.py`，除非仅证明相对b012 0 diff。

禁止任何新行为、C2 target/new_claims/duplicate/ID/stage、prompt/retry、verifier、其他文件、主干/服务/DB/配置。

## 验收

- 新commit直接父=b012738；相对b012仅2文件删除越界内容；
- 相对a2b仍恰好4个C1文件；
- `git diff --check a2b..HEAD`无输出；
- 搜索新candidate相对a2b：不得出现新增`is_challenge_stage`、`elif is_challenge_stage`、`test_validate_debate_response_accepts_challenge_payload`；
- 宿主3.10：foundation剩余测试、Opening16、agent_states、protocol metadata、prompt semantics、bundle-wash全绿；replay、compileall；
- 推新独立远端branch/SHA，不amend/force旧分支；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。