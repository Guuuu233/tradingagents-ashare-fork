## B2固定基线与严格边界

- 必须从已独立复审PASS的远端`agent/2/1f3eb19a5a03@a2b7515c7a549a642d4c6438cab36132bb0228b5` fresh checkout；直接父必须是a2b7515。
- B1不合主干/不部署；B2在B1分支链上串行继续。
- 禁止宿主树、主干、服务、DB/配置、api/main.py、setup.py、conditional_logic.py、前端/provider、manager/tiebreak。若实际必须改冻结文件，先停止报告。

## B2 Challenge精确契约

1. **payload/state schema**：在同一DEBATE_STATE中增加`challenges`与必要counter；challenge对象字段：`challenge_id`, `speaker_key`, `target_claim_id`, `weakest_point`, `evidence`, `severity(fatal|major|minor)`, `status(open|adopted|rejected)`, `evidence_status(verified|unsupported|contradicted)`；同时stage动作提供`self_win_prob`(0..1)。不得新增并行v2 state类型。
2. **stage/message**：a2b完成后state.protocol_stage=challenge；Bull message3/Bear message4自身stage=challenge、debate_round=2。两条合法challenge完成后state进入`tiebreak`（B2只推进stage，不实现tiebreak路由/逻辑）。失败attempt不推进count/stage/challenges。
3. **协议硬闸**：challenge阶段`new_claims`必须严格为空；每方至少1条challenge；target必须存在、未解决、属于对手；weakest_point非空且限长；evidence至少1条非空；severity枚举；responded/target_claim_ids不能代替challenges；duplicate（同speaker+target+规范化weakest_point或等价稳定键）不得入库。
4. **ID/账本**：CH-1递增且失败attempt不消耗ID；accepted round_message保存`challenge_ids`、stage、self_win_prob；opening claims不可被challenge直接改成resolved/rejected。
5. **prompt/retry**：基于B1同一marker+`render_debate_prompt`机制参数化challenge契约，中英文镜像；challenge只显示对手未解决claims和必要上下文，不重申己方Opening；new_claims=[]；retry不得退化为legacy“新增claim”规则。Legacy最终prompt和6消息规则不变。
6. **证据核验**：使用现有`EvidenceFactualTruthEvaluator`同一标准，不另写弱化验证器。将每条challenge evidence映射、核验并持久化到`challenge_verification`，回写challenge.evidence_status：verified/unsupported/contradicted。定性证据仍需来源/关键词匹配。
7. **fatal纪律**：B2只产出证据状态，不在此阶段否决claim。unsupported fatal不能改变target claim状态；contradicted fatal自身应rejected；verified fatal仍保持open，留给B3 manager显式adopted后才可否决。
8. **信念轨迹**：Bull/Bear challenge payload各含`self_win_prob`有限0..1；accepted消息持久化；非法/缺失按协议typed invalid（是否强制缺失需按顶层规格与现有兼容最小化，先用RED明确）。退化检测留B3，不提前实现。

## 严格TDD垂直切片

- C1 schema/ID/存储 RED→GREEN；
- C2 challenge stage协议硬闸 RED→GREEN；
- C3 prompt与Attempt2 retry捕获 RED→GREEN；
- C4 verifier映射与fatal三状态 RED→GREEN；
- 每个切片一个行为，不先写生产代码后补测试。

建议新增`tests/test_debate_challenge_protocol.py`，并复跑B1 Opening16、legacy DAV-346/e2e/6消息、P1-M state/metrics/persistence、prompt/custom矩阵、replay、compileall、diff-check；不跑全量。

交付新远端branch/SHA、parent=a2b7515、changed files、真实RED/GREEN和测试终态。明确未合入/未重启/未上线，3/1不变。不要mention项目调度助手。