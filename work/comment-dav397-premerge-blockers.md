## B4合入前新增BLOCK（Hermes独立实测）

当前全量可继续跑完收集证据，但结果出来后**禁止直接fast-forward、重启或激活DAV-398**，先处理以下两条：

1. 精确候选`ccc4c53a4985f8db32353586e6cb4317aa34f8cd..ec3e9030d3424fea9bb55058e2a2f0bc8705f16a`的`git diff --check`并非0：
   `tests/test_debate_challenge_protocol.py:1124: new blank line at EOF.`
   需要从`ec3e903`开严格机械微返修，仅删该EOF空行，推新SHA并做精确范围/diff-check/相关测试；不能amend已复审SHA。

2. DAV-398规定通过真实HTTP请求单次`config_overrides: {"v2_debate_enabled": true}`启用v2，但当前`api/main.py::_CONFIG_OVERRIDES_ALLOWLIST`（约598-602行）只允许LLM、轮次和语言键，**不含`v2_debate_enabled`**。`_build_runtime_config`会在约1748行静默过滤请求键，因此内部Propagator/config单测绿不证明真实`POST /v1/analyze`能启用v2。必须先写真实API/runtime-config RED，证明该键当前被过滤；最小GREEN只把`v2_debate_enabled`加入安全allowlist并测试默认关闭、单次请求开启、不持久化、不允许敏感键。该改动涉及`api/main.py`，需单独writer/精确SHA/独立复审，再组合到最终候选。

另：看板评论出现的`d43a3f8dc0948657866e721edb9d539f0b080820`目前`target ls-remote`不存在、本地Git不可解析；B4真实可验证候选是`agent/1/93fd11c25df0@ec3e9030d3424fea9bb55058e2a2f0bc8705f16a`。不得用不存在SHA做验收。

请完成当前`pytest tests/ -q`后报告原始终态，并将DAV-397保持blocked/in_progress等待两个微返修组合，不得合入或重启。不要mention项目调度助手。