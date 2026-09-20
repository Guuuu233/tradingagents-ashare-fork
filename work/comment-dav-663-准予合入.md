## 准予合入（不准予部署）

**合入 SHA：** `705b4a684b0e09c417c94b427c1ceb500a906722`

**分支：** `origin/agent/2/8a101617fd94`

**第一父：** `9a2878c8e94a0bf5ccab3ba222067d9f47d89138`

**DAV-664：** 独立审核 ✅通过（只读审核仅在 DAV-664）。

**Cursor 隔离复测：** `/tmp/ta-iso-705b4a6` 上 `tests/test_data_collector_cninfo_qualify.py` + 公告/IR collect + `test_news_event_coverage.py` + `test_cninfo_disclosure_metadata.py`：**83 passed**。

**范围：** 仅 `tradingagents/graph/data_collector.py` 与 `tests/test_data_collector_cninfo_qualify.py`。调用已有 `qualify_cninfo_content`；`provider_failure` 跳过 qualify。
