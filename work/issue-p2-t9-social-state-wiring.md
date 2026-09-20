# P2-T9：State / Propagator / Graph / api/main 三处传入 social_data_context

## 目标

把 `DataCollector` 已产出的 `pool["social_data_context"]` 贯穿到 AgentState、Propagator、TradingAgentsGraph（含 `_build_horizon_result`），以及 **`api/main.py` 三处** `create_initial_state`（双时间窗 / 流式 / 单时间窗）。

本卡**不**改 social ToolNode / analyst prompt / 删 legacy_proxy（Task 10+）。不部署。默认 mode 仍 disabled。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `a375bdc9cf3d07584eb6c28c637bde9bca867876`
- **新建**隔离分支，例如 `agent/dev2/p2-t9-social-state-wiring`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `docs/social_data/implementation_plan.md` Task 9 + §5.5 ledger→data_gaps 映射 + §8 SocialDataContext
- D-008 / D-009 / D-010
- contracts 已有 `SocialDataContext` / `create_default_social_data_context`（优先复用，禁止平行 TypedDict）

## 文件白名单

1. `tradingagents/agents/utils/agent_states.py`
2. `tradingagents/graph/propagation.py`
3. `tradingagents/graph/trading_graph.py`
4. `api/main.py`（仅接线 `create_initial_state` 三处 + 必要的 collected_pool 取值；禁止顺手大重构）
5. `tests/test_trading_graph_multi_horizon.py`（既有回归 + 必要断言）
6. `tests/test_report_social_context.py`（新建，若本卡覆盖 horizon result / data_gaps）
7. `tests/test_social_api_main_wiring.py`（新建：断言三处调用传入 social）

禁止：`data_collector.py`（T8 已合入）、`social_media_analyst.py`、prompts、`_create_tool_nodes` 删 get_news（Task 10）、删 legacy、改辩论轮次 / 开加权。

## 行为契约

### A. AgentState / TraceItem

- `AgentState` 增加 `social_data_context`（与 `market_data_context` 并列）。缺省可用 `create_default_social_data_context(...)`。
- `TraceItem` 增加（`total=False` 可）：`source_status` / `source_mode` / `bundle_id` / `direction_allowed` / `reason_codes` / `evidence_refs`（字段名与 plan §8 对齐；本卡至少让类型与赋值路径存在，analyst 填值可在后续卡）。

### B. Propagator

- `create_initial_state(..., social_data_context=None)`；写入 state。
- 同步 / 异步 propagate 路径：从 collected pool 提取 `social_data_context`（与 market 一样 fail-closed：缺失则 default context，不得静默省略键）。

### C. TradingAgentsGraph

- 所有 `create_initial_state` 调用传入 `collected.get("social_data_context")`（或等价）。
- `_build_horizon_result`：
  - 保存该 horizon 的 `social_data_context`
  - 合并市场 ledger + 社交 ledger → `data_gaps`（按 §5.5：仅 `failed/timeout/unavailable/refused/error`；**禁止**把 empty/insufficient/not_applicable 写成 failed gaps）
  - `direction_allowed=false` 时不得把社交 score 当成可交易方向证据写进结果摘要（本卡若生成 gaps/字段即可；完整 prompt 闸在更后任务）

### D. api/main.py 三处（硬约束）

当前 tip 调用点约：

| 路径 | 约行 |
|---|---|
| 双时间窗 | `api/main.py:2977` |
| 流式 | `api/main.py:3529` |
| 单时间窗 | `api/main.py:3717` |

以 `git grep create_initial_state api/main.py` 在基线 tip 上为准。三处都必须传入 `collected_pool["social_data_context"]`（或 collect 结果中的同键）。漏一处即不合格。

### E. 明确不做

- 不改 social ToolNode 的 `get_news`（Task 10）
- 不重写 analyst 输入（Task 11）
- 不删 `legacy_proxy`
- 不改 `TA_SOCIAL_MODE` 默认

## 测试（TDD）

离线；禁止实网；禁止 `@pytest.mark.asyncio`（需要则 `asyncio.run`）。

至少：

1. `create_initial_state` 含 `social_data_context` 键；默认 disabled/not_applicable 形状合法
2. graph 从 collected pool 传入后，final/horizon result 仍带该 context
3. `_build_horizon_result`：社交 ledger 的 failed/timeout 进 `data_gaps`；insufficient/empty **不**进失败 gaps
4. `test_social_api_main_wiring.py`：静态或轻量断言 `api/main.py` 三处 `create_initial_state` 均出现 `social_data_context` 实参（可用 AST/源码扫描，避免真起服务）
5. 既有 `tests/test_trading_graph_multi_horizon.py` 仍绿

建议命令：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_api_main_wiring.py \
  tests/test_report_social_context.py \
  tests/test_trading_graph_multi_horizon.py \
  tests/test_data_collector_social_integration.py \
  tests/test_data_collector.py \
  tests/test_social_data_collector.py \
  tests/test_social_contracts.py
```

（若某新建测试文件名你合并了，交付里写清实际文件名。）

## 交付

1. 单 commit：`feat(graph): propagate social evidence context`
2. 推送隔离分支；评论写完整 40 位 SHA、`git diff --stat`、pytest 精确数字
3. 状态 `in_review`；**不要** @项目调度助手；**不要**自行 FF / 部署

## Cursor 验收标准

- 白名单内；三处 api wiring 全覆盖
- 隔离复跑全绿后才「准予合入」
- **不准予部署**
