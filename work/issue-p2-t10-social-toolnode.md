# P2-T10：移除 social ToolNode 的新闻工具（不新增 social tool）

## 目标

social 分析师 ToolNode **不得再挂 `get_news`**，切断“社交分析回退到新闻工具”的路径。news ToolNode 保持不变。**禁止**新增 `get_social_sentiment_bundle` 或任何会在图里被调用的 social LangChain tool。不创建 `social_data_tools.py`。

本卡**不**重写 analyst prompt / 适配层（Task 11）。不删 `legacy_proxy`。不部署。默认 mode 仍 disabled。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `46995ac19bb4894dc6cea328f299951eb12698c5`
- **新建**隔离分支，例如 `agent/dev2/p2-t10-social-toolnode`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 10
- D-009 / D-010

## 现状（基线 tip）

`tradingagents/graph/trading_graph.py` `_create_tool_nodes` 中：

```python
"social": ToolNode(
    [
        # News tools for social media analysis
        get_news,
    ]
),
```

news 节点另有独立的 `get_news`，必须保留。

## 文件白名单

1. `tradingagents/graph/trading_graph.py`（仅 `_create_tool_nodes` 的 social 节点；必要时同文件极小配套）
2. 现有图/工具相关测试中与 ToolNode 断言相关的文件（优先改已有测试；若无则新建 `tests/test_social_toolnode_no_news.py`）
3. **可选**：`api/main.py` 仅当存在 `get_social_sentiment` **进度文案**需改为“读取社交归档”——只改文案字符串，不引入新 tool 名、不改业务逻辑

禁止：analyst/prompts、`analyst_adapter`、`prompt_formatter`、`data_collector`、删 legacy、新建 `tradingagents/agents/utils/social_data_tools.py`、改辩论轮次 / 开加权。

## 行为契约

1. social ToolNode 工具列表**不含** `get_news`（按 name / 可调用对象断言）。
2. 若空列表被 LangGraph `ToolNode` 拒绝：保留 ToolNode 节点，但不挂任何数据工具（文档允许的退路）；交付里说明取舍并用测试钉死。
3. news ToolNode 仍含 `get_news`；其它分析师节点工具集不得被本卡改动（可用快照/集合差断言）。
4. **禁止**新增 `get_social_sentiment_bundle` 或任何 social 数据 tool。
5. 图编译 / 既有 graph 测试仍绿。

## 测试（TDD）

离线；禁止实网。

至少：

1. `_create_tool_nodes()["social"]` 的 tools 中无 `get_news`
2. `_create_tool_nodes()["news"]` 仍含 `get_news`
3. 源码或对象级断言：不存在新 social data tool 注册
4. 相关既有 graph 测试仍绿（例如 `tests/test_trading_graph_multi_horizon.py` 及任何直接测 tool nodes 的用例）

建议命令：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_toolnode_no_news.py \
  tests/test_trading_graph_multi_horizon.py \
  tests/test_social_api_main_wiring.py \
  tests/test_report_social_context.py
```

（若测试文件名不同，交付写清。）

## 交付

1. 单 commit：`fix(graph): stop social analyst tool fallback to news`
2. 推送隔离分支；评论写完整 40 位 SHA、`git diff --stat`、pytest 精确数字
3. 状态 `in_review`；不要 @项目调度助手；不要自行 FF / 部署

## Cursor 验收标准

- 白名单内；social 无 get_news；news 不变；无新 social tool
- 隔离复跑全绿后才「准予合入」
- **不准予部署**
