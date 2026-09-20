## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `d82b0da8c6e32c0e85371cd9abb9d85ebe920387`  
**父 tip：** `e20c6acbbd4dca9f25b6df733ec6216a7e84bab6`  
**分支：** `origin/agent/1/d7196be18b79`

DAV-643 独立审核 ✅通过。Cursor 隔离 `/tmp/ta-iso-d82b0da`：`pytest tests/test_tushare_repurchase_collateral.py tests/test_tushare_forecast_collateral.py tests/test_tushare_daily_basic.py tests/test_cn_akshare_backup_sources.py tests/test_financial_announce_cutoff.py tests/test_fund_flow_evidence.py` **172 passed**。白名单 2 文件。复用 `_tushare_post`；PIT 仅 `ann_date`；空表 `collateral_empty`；`canonical_event_id` 恒 `None`；`proc=完成` 越界行丢弃。残留：测试文件末尾空行致 `git diff --check` 退出码 2；函数超过 60 行；异代码行 `continue` 而非 `symbol_mismatch`；日期比较走规范化 ISO 字符串。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止接线 `disclosure_date`。禁止 C-09-3。
