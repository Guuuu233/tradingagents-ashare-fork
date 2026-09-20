## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `e20c6acbbd4dca9f25b6df733ec6216a7e84bab6`  
**父 tip：** `e8130b69eb153fc4d0a3079f3ec7986bd9f58dd5`  
**分支：** `origin/agent/2/816a5c379640`

DAV-641 独立审核 ✅通过。Cursor 隔离 `/tmp/ta-iso-e20c6ac`：`pytest tests/test_tushare_forecast_collateral.py tests/test_tushare_daily_basic.py tests/test_cn_akshare_backup_sources.py tests/test_financial_announce_cutoff.py tests/test_fund_flow_evidence.py` **157 passed**；`tests/test_tushare_forecast_collateral.py tests/test_tushare_daily_basic.py tests/test_cninfo_disclosure_metadata.py` **51 passed**；`git diff --check` 0。白名单 2 文件。复用 `_tushare_post`；按列名 zip；PIT 用 `ann_date <= as_of`，未用报告期 `end_date` 截断；空表 `collateral_empty`；`canonical_event_id` 恒 `None`。残留：`_fetch_tushare_forecast` 超过 60 行；签名仍接受查询用 `end_date`（行过滤仍走 `ann_date`）。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止接线 `repurchase`/`disclosure_date`。禁止 C-09-3。
