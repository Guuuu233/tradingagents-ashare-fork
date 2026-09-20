C2.1/C2.2的宿主3.10 RED→GREEN有效，C2.3账本RED已形成。提交前必须修正一处权威stage语义：

当前新增：
`is_challenge_stage = v2_enabled and (current_stage == "challenge" or message_index in (3, 4))`
并在update/attempt路径使用同类`current_stage == "challenge" or message_index in (3,4)`。

这会让显式`protocol_stage="tiebreak"/"manager"`但count异常、恢复或重放中的state因message_index=3/4被强行拉回challenge，违反“protocol_stage为权威来源”。B1已保证Bear opening成功后写入challenge，因此正常v2路径不需要猜测。

必须：
1. 当state明确含`protocol_stage`时，challenge判定只允许`current_stage == "challenge"`；update/attempt的debate_round/stage同样不得用message_index覆盖显式tiebreak/manager。
2. 只有`protocol_stage`键真正缺失时，如确需兼容，可单独按message_index回退，并写明确测试；不得把空值/显式其他stage混为缺失。
3. 新增回归：v2 state显式`protocol_stage="tiebreak"`（另测manager更佳）、count=2时，不得走challenge分支，不得把round_message/stage改回challenge。该测试应先在当前实现RED，再修GREEN。
4. 保持Opening message1/2权威契约与legacy路径不变。
5. 继续C2.3账本后再C2.4；禁止prompt/verifier/conditional_logic。

不要mention项目调度助手。