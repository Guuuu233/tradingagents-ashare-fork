# DAV-278 阶段三：知识库动态 RAG 注入（方案 DAV-198）

**基线父提交必须是 `2873f302536960a158be4b3cb3d22373a82ba285`。禁止基于 `8866494` 开工。不必等主干 FF。禁止自己推主干。**

对照《项目加强方案》§5.3：将现有 27 行业图谱 + 19 宏观情景知识库，通过检索注入分析师 prompt。当前知识库是静态文档；分析师无法按标的/宏观情景检索。

## 产品契约

1. 检索源：`tradingagents/knowledge/` 现有 27 行业图谱 + 19 宏观情景，**禁止另起一套知识文本**。
2. 注入点：宏观 / 基本面 / 新闻（阶段一+阶段二已有报告字段）。检索结果格式化为可读文本，缺命中写 `【知识库未命中】`，禁止编造行业事实。
3. 禁止引入付费向量库/新云 API。优先：本地关键词/结构化索引（industry_name、aliases、政策词、传导链）。embedding 仅当已有本地依赖且不新增 pip 包。
4. 禁止 `_v2` 并行 prompt；改原路径。
5. 历史案例闭环（方案 §5.4）**本卡不做**。
6. 禁止改 `.env`、providers、role_bindings、资金流、setup.py 拓扑、INDUSTRY_LINKAGE_MAP。

## 允许修改

- `tradingagents/knowledge/`（检索入口，不删 27/19 正文）
- `tradingagents/agents/utils/knowledge_context.py`（已有注入，扩展检索）
- 宏观/基本面/新闻分析师读取检索结果的现有字段
- `tests/` 追加检索命中/未命中/不编造

## 验收

独立 worktree。父提交 `2873f30`。
`.venv310` 定向知识库测试 + `TUSHARE_TOKEN='' pytest tests/test_analyst_knowledge_and_macro_views.py tests/test_knowledge*.py tests/test_industry_linkage*.py -q` + compileall + diff-check。
推送独立分支精确 SHA，不合主干。不得 @项目调度助手。
