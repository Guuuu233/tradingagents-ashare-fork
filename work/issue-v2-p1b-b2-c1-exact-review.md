## 固定审核对象

- B1基线：`a2b7515c7a549a642d4c6438cab36132bb0228b5`
- C1行为提交：`b0127380634d55fa98778e6ce57b6546146e136b`
- C1边界收口：`91b4cf51b80fb3edae0d6a2df02ae9928dd665a8`
- 最终远端：`agent/2/128052b9e828`
- 线性链必须a2b→b012→91b；相对a2b恰好4文件；相对b012仅2文件删除49行。

严格只读：0 code/test/config/DB/service changes；宿主Python3.10，不跑全量，不合入/不重启。

## 必审

1. C1 only：Challenge TypedDict字段、InvestDebateState challenges/challenge_counter/challenge_verification、Propagator v1/v2空容器与deepcopy隔离。
2. Sanitizer：顶层challenges/self_win_prob被保留且不报unknown；challenge字段白名单；challenge_id可选；evidence list/string规范化；结构类型错误typed invalid；self_win_prob bool/string/非有限/越界拒绝，0/1保留；legacy payload不变。
3. 边界：相对a2b不得新增`is_challenge_stage`、challenge protocol gates、target/opponent/new_claims/duplicate/ID allocation/stage transition、prompt/retry、evaluate_challenges；`validate_debate_response`的协议分相相对a2b必须0行为diff。
4. 测试不得包含`test_validate_debate_response_accepts_challenge_payload`；Challenge目前仍会走legacy Check B/C是C2未实现的预期，不得据此BLOCK C1。
5. 范围4文件；uv.lock/.venv、api/main.py、conditional_logic.py、setup.py、researchers/prompts/evidence_verifier/manager/DB/config均0变动；diff-check无输出。
6. 独立宿主`.venv310`：foundation13、Opening16、agent_states14、protocol metadata9、prompt semantics、bundle wash5；replay、compileall。可合并命令，但必须真实终态。

输出PASS/BLOCK、文件:行号、命令终态、精确SHA。明确未合入/未重启/未上线，3/1不变。不要mention项目调度助手。
