# DAV-258 修复主干既有失败：基本面知识库注入测试

基线：`target/codex/dav-4-p2a-trunk@0b10041f9e68b5d0116b76c36cd629acb365f10d`

DAV-253 终审记录：`tests/test_analyst_knowledge_and_macro_views.py:354`
`test_fundamentals_analyst_node_with_collector_pool` 在主干 `c956e43`/`0b10041` 均失败。
断言：HumanMessage 含 `【行业常识知识库 - 白酒与精制茶酒`（标的 `600519`）。

这是阶段一知识注入缺口，不是 248/249 引入。与 DAV-256 文件不重叠。

## 允许修改（尽量最小）

- `tradingagents/agents/utils/knowledge_context.py`
- `tradingagents/agents/analysts/fundamentals_analyst.py`
- `tests/test_analyst_knowledge_and_macro_views.py`（仅当断言与产品契约不一致时改测试，必须说明）

禁止改资金流、api/main.py、`.env`、主干、部署。

## 验收

独立 worktree。先 RED 复现该失败，再 GREEN。
`.venv310` 跑该文件 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check。
推送独立分支，不合主干。不得 @项目调度助手。
