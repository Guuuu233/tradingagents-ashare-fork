Bug B 精简重派：当前主干 `bb1b693156d60f2e582e9e0c2568e610d65295ab`，只修改 `tradingagents/prompts/zh.py` 的研究经理“各分析师 Verdict 全景概览与动态加权”要求，和一个对应测试文件。

必须把以下七位逐一点名为独立条目且每项有 verdict + 权重：宏观板块、市场（技术面）、舆情（情绪）、新闻、基本面、主力资金、量价。明确禁止合并多个分析师为一个视角、禁止省略。保留现有短线/中线动态权重规则；不改Python逻辑、机读块、配置、主干。

用 `.venv310` 跑定向 prompt/research_manager 测试。提交并推 `target` 分支，回复精确 SHA 和 `git ls-remote` 证据。不要重复读取父任务历史。

[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff)
