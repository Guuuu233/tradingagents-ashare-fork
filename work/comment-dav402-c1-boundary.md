C1宿主3.10有效RED已成立，可以继续GREEN；但当前foundation测试第291行起的`test_validate_debate_response_accepts_challenge_payload`越过了本卡边界：它要求完整`validate_debate_response`在challenge stage绕过legacy Check B/C并接受challenge，这是C2协议分相，不是C1 schema/sanitizer。

立即纠正：

1. 从`tests/test_debate_challenge_foundation.py`删除/延期该测试到后续C2；不得为了它修改`validate_debate_response`的Opening/Legacy/C2分支。
2. C1只允许直接测试`_sanitize_machine_payload`保留`challenges/self_win_prob`、typed invalid、旧payload兼容，以及TypedDict/Propagator空容器。
3. 当前`debate_utils.py`只保留machine fields与sanitizer改动；若已开始改`validate_debate_response`或stage逻辑，恢复该部分到a2b7515。
4. 保持禁止target/对手/new_claims/duplicate/ID/stage/prompt/verifier。
5. 用显式宿主3.10重跑foundation GREEN，再跑B1/legacy矩阵；不要弱化其余foundation断言。

不要mention项目调度助手。