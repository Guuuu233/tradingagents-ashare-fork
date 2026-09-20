# H-05a：双档顶层 not_applicable 聚合不得被单档覆盖

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `5fbd70ee2d09637bce8aa64e441a75e003897d32`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 双档任务落库/SSE 的**报告级** `not_applicable` 必须遵守 DAV-38：仅当所有 **completed** 档都是 `not_applicable=True` 时，顶层才为 True。已复现：混档（short False / medium True）和两档空 structured 默认 False 时，顶层被写成 True。  
**禁止：** 改七分析师、research_manager、collector、H-04、H-05b 前端组件、H-05c、E-03、计权、`role_bindings`/`providers`、C-04/C-09-3/Track B/H1b/PDF、FF/部署、push 主干。不要重写已通过的单档 `_apply_structured_report_fields` 语义（`test_structured_not_applicable_still_overrides_existing_false` 必须仍绿）。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-05a**。H-04d 已合入。H-04c 已跳过。Cursor 在 `5fbd70e` 与前一 tip `5eed1a2` 上复现同一组失败（非 H-04d 回归）：

```
FAILED tests/test_dual_horizon_e2e.py::test_dual_horizon_propagates_both_horizons_to_result_sse_and_reportdb
FAILED tests/test_dual_horizon_bugs.py::test_dual_horizon_save_propagates_aggregated_structured_fields
FAILED tests/test_dual_horizon_bugs.py::test_dual_horizon_save_uses_safe_defaults_when_structured_fields_are_empty
# 同文件其余 dual-horizon 用例在 5fbd70e 上为 35 passed / 3 failed（含 test_report_dual_horizon.py、test_trading_graph_multi_horizon.py）
```

根因方向（开卡前已读）：`report_service.aggregate_horizon_metadata` 已按 DAV-38 把混档聚成顶层 False。双档 save 随后走 `api/main.py` `_apply_structured_report_fields`：单档 `structured.not_applicable=True` 会覆盖聚合成 True；`trade_action` 为 WAIT/NO_TRADE 时也会把 `result["not_applicable"]=True`。这是报告级聚合被单档/状态机二次覆盖，不是要重写 `aggregate_horizon_metadata` 的单元契约。

## 允许改

- `api/main.py`（双档结果组装 / save / `_apply_structured_report_fields` 与双档路径的交接）
- 仅当测试证明必须：`tradingagents/agents/utils/decision_status.py`（不要改计权、不要改单档 INVALID/ABSTAIN 语义）
- 仅当测试证明聚合函数本身算错：`api/services/report_service.py` 的 `aggregate_horizon_metadata`（默认不要动；`test_aggregate_horizon_metadata_*` 必须仍绿）
- 测试：优先让上述三个失败用例转绿；可在 `tests/test_dual_horizon_bugs.py` / `tests/test_dual_horizon_e2e.py` 增补断言。不要改 `tests/test_dav37_stage16_regressions.py` 里已通过的单档覆盖用例除非它与双档契约冲突——冲突则停下来在评论里说明，不要擅自废掉 DAV-38。

## 契约

1. 双档 completed+completed、short `not_applicable=False`、medium `True` → 顶层 `not_applicable is False`，`not_applicable_by_horizon` 保留分档。
2. 两档 structured 缺省 `not_applicable=False` → 顶层 False，不得因 WAIT/NO_TRADE 把整份双档报告标成不适用。
3. 两档 completed 且都 True → 顶层仍可为 True（`test_aggregate_horizon_metadata_all_not_applicable_is_true_when_all_completed`）。
4. 单档路径：`test_structured_not_applicable_still_overrides_existing_false` 保持通过。
5. 不平均 short WAIT / medium BULL；单档失败不把另一档改成 not_applicable。不改前端。

## 测试

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_dual_horizon_e2e.py tests/test_dual_horizon_bugs.py tests/test_report_dual_horizon.py tests/test_dav37_stage16_regressions.py tests/test_trading_graph_multi_horizon.py
```

上述三个失败用例必须转绿；dav37 聚合与单档 structured 覆盖用例必须仍绿。一个 commit，push 功能分支，评论完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数（含失败转绿证据）。
