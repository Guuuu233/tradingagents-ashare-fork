Cursor 独立复审 DAV-501 / P2-T9。

候选 SHA（完整 40 位）：`46995ac19bb4894dc6cea328f299951eb12698c5`
父提交：`a375bdc9cf3d07584eb6c28c637bde9bca867876`（线性，无 merge）
分支：`agent/dev2/p2-t9-social-state-wiring`

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

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

结果：**92 passed**。

契约核对：
- `AgentState.social_data_context` + `TraceItem` 审计字段已加
- Propagator / sync+async graph 从 collected 提取并传入
- `api/main.py` 三处 `create_initial_state` 均传 `social_data_context`（AST 测钉死）
- `_build_horizon_result` 合并社交 ledger；仅 `failed/timeout/unavailable/refused/error` 进 `data_gaps`；empty/insufficient/not_applicable/partial 不进
- 白名单 7 文件；未改 ToolNode/analyst/prompts；未删 legacy

残留（不阻塞）：
1. `_log_state` 本地 JSON 日志尚未写入 `social_data_context`（horizon/API 结果已有）
2. 市场 ledger 仍允许 `status=None` 进 gaps（旧兼容）；社交侧要求显式 status

**准予合入** `46995ac19bb4894dc6cea328f299951eb12698c5` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要开 Task 10（另卡）。不要删 legacy_proxy。
