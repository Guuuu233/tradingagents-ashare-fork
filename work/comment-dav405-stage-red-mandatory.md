C2.4当前只新增完整4消息happy-path，仍未覆盖已指出的恢复态错误；生产代码三处仍保留`or message_index in (3, 4)`。该门未满足，禁止进入回归/提交。

立即先补单一RED：

1. 构造v2 state：`protocol_stage='tiebreak'`, `count=2`, claims/challenges容器有效；调用`validate_debate_response`传一个表面合法Challenge payload。断言不得被识别为Challenge合法动作（不能因message_index=3绕过后续阶段规则）。
2. 构造同类`protocol_stage='manager'`, `count=2`，至少测试`update_debate_state_with_payload`或失败attempt记录不得把round_message/state stage改成challenge。
3. 在当前实现运行，必须因被错误拉回challenge而RED；保存宿主3.10终态。
4. GREEN：
   - `validate_debate_response`：显式stage存在时，`is_challenge_stage = v2_enabled and current_stage == 'challenge'`；
   - accepted update与unaccepted attempt的stage/debate_round推导同样尊重显式stage；不得用message_index覆盖`tiebreak/manager`；
   - 仅当`'protocol_stage' not in state`时，如确有兼容需求，才允许按message_index推导，并需单独测试。否则无需回退。
5. 重跑上述RED用例、C2全部专项及Opening16。确认源码中不再出现`current_stage == 'challenge' or message_index in (3, 4)`。

完成后再继续C2.4/回归。禁止弱化测试、prompt/verifier/conditional_logic。不要mention项目调度助手。