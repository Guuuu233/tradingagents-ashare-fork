当前机械删除正确，`uv run --python 3.10`的124 passed/replay可作辅助证据，但不能替代指定宿主`.venv310`。并且uv创建环境导致`uv.lock`被修改，已越界。

提交前必须：

1. `git restore --source=b0127380634d55fa98778e6ce57b6546146e136b -- uv.lock`；确认`git status --short`不再出现uv.lock。
2. `.venv/`不得提交；确认它被gitignore且`git status --short`无任何环境文件。
3. 使用精确宿主解释器，不再用uv/bare pytest：
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python --version`
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_debate_challenge_foundation.py tests/test_debate_opening_protocol.py tests/test_agent_states.py tests/test_debate_protocol_metadata.py tests/test_prompt_semantics.py tests/test_debate_bundle_wash.py -q`
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python tests/golden/audit_20260823/replay_verifier.py`
   - `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m compileall tradingagents tests`
4. 最终相对b012仅2文件删除49行；相对a2b恰好4个C1文件；diff-check无输出；禁用字符串0命中。
5. 然后提交并推新分支SHA。禁止其他行为改动、主干/服务/配置；不要mention项目调度助手。