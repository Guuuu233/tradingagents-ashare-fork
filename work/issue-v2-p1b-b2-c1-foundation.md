## 固定基线与输入

- fresh基线：`target/agent/2/1f3eb19a5a03@a2b7515c7a549a642d4c6438cab36132bb0228b5`
- Challenge测试设计只读来源：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav393-test_debate_challenge_protocol.py`
- SHA-256：`ae9a72943fb683bec9c3b0b0117b2d5ec6ecdcb3528608fb71a49b09dea00f34`
- DAV-393因在宿主3.10有效RED前改生产代码而BLOCK；其未提交生产改动全部弃用。禁止复用原worktree或复制其生产diff。

## 本卡唯一目标：B2-C1 Challenge foundation

只实现可独立验收的Challenge基础层，不实现C2协议硬闸、ID入账/stage推进、prompt/retry、evidence verifier。

允许文件：
- `tradingagents/agents/utils/agent_states.py`
- `tradingagents/graph/propagation.py`
- `tradingagents/agents/utils/debate_utils.py`（仅machine payload schema/sanitizer）
- 新建`tests/test_debate_challenge_foundation.py`

禁止其他文件、主干、宿主树、服务、DB/配置、api/main.py、conditional_logic.py、setup.py、researcher/prompt/manager/provider。

## 严格TDD

### RED测试（先写、宿主3.10）

1. `Propagator(...v2=true)`的`investment_debate_state`初始包含：`challenges=[]`, `challenge_counter=0`, `challenge_verification=[]`；两个state深拷贝隔离，v1默认也可带空容器但行为不变。
2. `InvestDebateState`/Challenge TypedDict声明完整字段，不引入并行state类型。
3. `DEBATE_STATE` sanitizer保留顶层`challenges`和`self_win_prob`，不再报告unknown_fields；每条challenge只保留：`challenge_id?`, `target_claim_id`, `weakest_point`, `evidence`, `severity`。
4. sanitizer结构类型：challenges非数组、challenge非object、evidence非list/string、self_win_prob为bool/string/非有限/越界时必须typed invalid或保留明确invalid信号，不能静默变成合法值；合法0和1保留。
5. 旧payload无challenge字段仍完全按原schema解析，legacy machine block测试不回归。

测试必须普通同步`def`；不使用`pytest.mark.asyncio`。

先运行并保存：
```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python --version
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_debate_challenge_foundation.py -vv
```
RED必须因缺Challenge foundation而失败，不得是环境/语法/fixture错误。

### GREEN

最小实现上述foundation。禁止提前实现：target对手校验、new_claims=[]、weakest_point长度、severity枚举、duplicate、CH ID分配、stage推进、prompt、evaluate_challenges。这些留C2/C3/C4。

## 验收

- Foundation专项全绿；
- B1 Opening16、agent_states、protocol metadata、prompt semantics、machine-block quarantine矩阵全绿；
- 宿主Python3.10、replay、compileall、diff-check；不跑全量；
- changed files恰好允许范围；
- 新远端branch/SHA，直接父a2b7515；报告RED/GREEN、测试、文件范围；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。