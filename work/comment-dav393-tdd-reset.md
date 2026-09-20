TDD硬闸：你在宿主3.10有效RED之前已经修改了`agent_states.py`、`debate_utils.py`、`propagation.py`；当前唯一RED来自Python3.14裸pytest，且测试仍含pytest-asyncio标记。这些生产改动不能继续叠加。

立即执行以下重置，不丢测试设计：

1. 仅将当前已改的3个生产文件恢复到精确基线a2b7515：
   - `git restore --source=a2b7515c7a549a642d4c6438cab36132bb0228b5 -- tradingagents/agents/utils/agent_states.py tradingagents/agents/utils/debate_utils.py tradingagents/graph/propagation.py`
   - Challenge测试文件保留。
2. Challenge测试中删除所有`@pytest.mark.asyncio`；所有async测试改普通`def`并在内部`asyncio.run(_run())`。不得安装插件/改pytest配置。
3. 运行并记录：
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python --version` → 必须3.10.20；
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_debate_challenge_protocol.py -vv`。
4. 有效RED必须来自缺失Challenge生产能力：缺schema/未知字段、legacy Check C误触、无evaluate_challenges、stage不进tiebreak等；不得有async插件、语法、fixture错误。
5. 只有保存有效3.10 RED后，才按C1→C2→C3→C4逐个RED→GREEN重新写生产代码；禁止一次横向实现全部。
6. 当前分支不提交、不推送，主干/服务/配置不动。不要mention项目调度助手。