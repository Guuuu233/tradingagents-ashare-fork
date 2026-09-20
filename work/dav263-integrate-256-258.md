# DAV-263 组合 256+258 到独立树（不合主干）

基线：`target/codex/dav-4-p2a-trunk@0b10041f9e68b5d0116b76c36cd629acb365f10d`

两候选文件不重叠，均可叠在 `0b10041`：

- DAV-258 PASS：`a3f9d91d7aa56ea565580687dceed0ca7badb61e`
  - `tradingagents/agents/utils/knowledge_context.py`
  - `tradingagents/agents/analysts/fundamentals_analyst.py`
  - `tests/test_analyst_knowledge_and_macro_views.py`
- DAV-256 PASS：`0111b1a253b6d331660f78d6f5d053aa7ca2f3bf`
  - `tradingagents/dataflows/industry_linkage.py`
  - `tradingagents/dataflows/providers/industry_linkage_provider.py`
  - `tradingagents/graph/data_collector.py`
  - `tests/test_industry_linkage.py` 及 collector/dataflows 测试

固定顺序：先 cherry-pick `a3f9d91`，再 `0111b1a`。冲突则停止。禁止改宿主、不合主干、不部署。

独立 worktree。`.venv310`：定向知识库+产业链测试 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check。

推送 `target/integration/dav256-dav258` 精确 SHA。不得 @项目调度助手。
