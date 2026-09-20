## 固定基线与任务边界

- fresh checkout：`target/codex/dav-4-p2a-trunk@ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- 本卡只做P1-B/B1 Opening垂直切片；不实现challenge、tiebreak、manager dispute map。
- 禁止进入/修改宿主项目树；必须独立checkout、宿主`.venv310`测试。
- 禁止主干/服务/DB/个人配置/前端/provider/setup.py。

## 允许文件

先完整读调用点后，原则上只允许：
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/graph/propagation.py`
- `tradingagents/graph/trading_graph.py`（仅请求级flag进入initial state，如确需）
- `tradingagents/agents/utils/debate_utils.py`
- `tradingagents/agents/researchers/bull_researcher.py`
- `tradingagents/agents/researchers/bear_researcher.py`
- `tradingagents/prompts/zh.py`、`en.py`（仅stage契约，保持镜像）
- 新增/更新Opening专用测试及必要既有协议测试

如发现必须改`api/main.py`或其他文件，先停止并报告，不得越界。

## 严格TDD垂直切片

### O1 请求级启用与state

先写RED：
- 默认config无override时initial state仍`v1_legacy`、`v2_debate_enabled=false`；legacy 6消息/路由不变。
- 单次config `v2_debate_enabled=true`时initial state为`v2_structured_disagreement`、stage=`opening`；不得写数据库或持久设置。
- 两个state深拷贝隔离。

最小GREEN：复用P1-M metadata/flags，不另造默认常量。

### O2 stage记录与Opening协议

先写RED：
- v2 message_index 1/2均stage=`opening`，round=`1`；round_message保存stage。
- claim同时保存`debate_round=1`、`message_index`、`battlefield`。
- opening `responded_claim_ids=[]`、所有`target_claim_ids=[]`。
- 每方`new_claims`必须2-3条，并覆盖至少3个不同battlefield：capital_flow/sentiment_theme/price_volume/macro_policy/fundamentals。
- 少于3战场、错误battlefield、opening回应/target对手均`invalid_protocol`。
- Bear opening message_index=2不触发legacy Check B/C。
- flag关闭时旧message_index>=2规则及DAV-346逐claim去重完全不变。

### O3 双盲最终prompt捕获

用可记录最终prompt的fake streaming LLM先写RED：
- Bull/Bear opening均传`history=''`、`current_response=''`、claims/focus/unresolved为空。
- Bear opening prompt不含Bull opening独特句子、claim ID、summary或当前response，0命中。
- opening调用`memory.get_memories(..., n_matches=0)`或完全不调用；不得检索当前ticker历史辩论记忆。
- 双方七报告输入逐字段、逐字节对称；report manifest一致。
- retry prompt仍遵守opening：不得要求respond/target对手或泄漏对手claim。

最小GREEN：按stage构造隔离视图，不清空authoritative state；challenge后续可使用完整state。

## 非目标

- 不实现challenge结构/验证；
- 不改conditional_logic为4条进总监；
- 不实现tiebreak、总监prompt/dispute map、shadow credit；
- B1候选不得合入/部署，避免开启不完整v2。

## 验收

- 每个切片真实RED→GREEN；
- Opening测试覆盖Bull/Bear及retry；
- legacy协议/6消息、DAV-346、e2e protocol、state persistence、P1-M矩阵全绿；
- replay、compileall、diff-check；不跑全量（B3最终统一跑）；
- changed files仅允许范围；推新远端branch/SHA；报告父SHA、TDD、测试、限制；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。