## 准予合入（不准予部署）

**合入 SHA：** `de0e4e013b1364ce55cd5e6d116c11fdd6b19d2d`  
**分支：** `origin/agent/1/0792978a9f3e`  
**第一父：** `45f52868c78e2239a126595fd60bdf10db4f4de4`  
**DAV-658：** 独立审核 ✅通过（只读审核仅在 DAV-658）

Cursor 隔离 worktree `/tmp/ta-iso-de0e4e0`：`tests/test_data_collector_collateral_collect.py` + `tests/test_data_collector.py` + `tests/test_news_event_collateral_attach.py` + 三张 Tushare 旁证单测 **95 passed** in 5.37s。

范围仅 `data_collector.py` 传入 `_registry.get("cn_akshare")`，以及新测试。未改 `_fetch_tushare_*`、未接 CNINFO vendor、未部署。
