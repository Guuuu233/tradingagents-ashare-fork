根因已定位在 `_compact_failure_reason('failed')` 固定返回裸英文。现在停止继续读历史：只在允许的 `tests/test_data_collector.py` 写最小 RED，使用宿主 `.venv310/bin/python` 运行并确认 FAIL；再最小修改 `data_collector.py`，把 failed 显示为 typed 中文且保持 `status=failed`。不要改 report_service 或 test_dav37；相关旧断言若失败，报告为基线契约待后续更新，不越界修改。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
