## 固定基线

- fresh基线：`target/agent/2/128052b9e828@91b4cf51b80fb3edae0d6a2df02ae9928dd665a8`
- C1 foundation已独立复审PASS：Challenge TypedDict、初始空容器、sanitizer/self_win_prob结构校验。
- 直接父必须为91b4cf5；禁止从b012、a2b或主干另起。

## 本卡唯一目标：B2-C2 Challenge协议硬闸、ID账本与stage推进

只实现Challenge生产协议与state入账，不实现prompt/retry（C3）、evidence verifier/fatal映射（C4）、tiebreak路由/manager（B3）。

允许文件原则上仅：
- `tradingagents/agents/utils/debate_utils.py`
- 新建`tests/test_debate_challenge_protocol.py`

如确需补`agent_states.py/propagation.py`，先证明C1缺字段；默认禁止。禁止researchers/prompts/evidence_verifier/conditional_logic/setup.py/api/main.py/manager/DB/config/frontend/provider/主干/服务。

## 严格TDD：按行为逐个RED→GREEN

所有测试普通同步`def`，禁止pytest-asyncio。所有命令显式：
`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest ...`

### C2.1 stage分相与基本契约
先写最小RED：state为v2+protocol_stage=challenge+count=2时，Bull message3合法Challenge应绕过legacy Check C；message自身stage=challenge/debate_round=2。
最小GREEN只加入challenge stage分相。

### C2.2 硬闸
逐行为RED→GREEN：
- `new_claims`必须严格`[]`；
- `challenges`至少1条；
- 每条target_claim_id必须存在、未resolved、属于对手；
- weakest_point非空且长度上限500；
- evidence至少1条非空；
- severity仅fatal|major|minor；
- self_win_prob必须存在且为有限0..1（sanitizer已做类型边界，协议层仍要求缺失invalid_protocol）；
- responded_claim_ids必须至少包含被challenge的target；不得只塞无关对手ID；
- resolved_claim_ids在challenge不能直接否决/解决对手claim，沿用阵营权限闸。

每个错误typed `invalid_protocol`，error_detail命中精确字段；不得改测试迁就实现。

### C2.3 ID与账本
逐行为RED→GREEN：
- accepted Bull message3分配`CH-1`，Bear message4分配`CH-2`；
- challenge对象落库：challenge_id/speaker/speaker_key/stance/target_claim_id/weakest_point/evidence/severity/status=open/evidence_status（未核验可为空或`unverified`，固定一种并测试）/message_index/debate_round/stage；
- accepted round_message保存challenge_ids、self_win_prob、stage=challenge；
- Challenge不得新建INV claim，不得修改target claim status为resolved/rejected；
- failed/missing/invalid attempt不推进count、protocol_stage、challenge_counter、challenges，不消耗CH ID；
- duplicate：同speaker+同target+normalize_text(weakest_point)完全相同或similarity>=0.82，typed invalid且不入账；同target不同实质弱点可接受。

### C2.4 stage推进
- accepted Bull message3后authoritative state仍challenge；
- accepted Bear message4后state protocol_stage=`tiebreak`；两条round_message/challenge自身仍stage=challenge；
- C2只推进字段，不修改conditional_logic，不执行tiebreak。

## Legacy与边界

- v1 message3/4/5/6 Check B/C/D和DAV-346逐claim防重不变；
- B1 Opening16全部不回归；
- Challenge当前没有stage prompt是预期，C3再实现；本卡测试不得调用researcher LLM或修改prompt。
- 不实现evaluate_challenges/challenge_verification回写/fatal裁决。

## 验收

- 每个子行为有真实宿主3.10 RED→GREEN记录；
- C2专项、C1 foundation13、Opening16、legacy e2e/bundle wash/information gain、protocol metadata/state persistence/metrics全绿；
- replay、compileall、diff-check；不跑全量；
- changed files严格允许范围；
- 推新远端branch/SHA，直接父91b4cf5；报告RED/GREEN、测试、文件范围；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。