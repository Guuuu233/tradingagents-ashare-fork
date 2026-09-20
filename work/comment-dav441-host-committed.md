宿主已确认提交 H2.4：

- commit: `52353ed` on `codex/dav-4-p2a-trunk`
- 变更: `evaluation_schemas.py`（model_isolation + Gap 看板）、`recalculate_weekly_metrics.py`（CLI + 死代码清理）、`tests/test_evaluation_contracts.py`
- 证据: `pytest tests/test_evaluation_contracts.py tests/test_recalculate_weekly_metrics.py -q` → 36 passed

若 agent 分支重复实现，以宿主 `52353ed` 为准走 FF；勿重复合入。
