## 准予合入（不准予部署）

**合入 SHA：** `da9a69d6dbf11a0e359732bce3d9b11968b3e1b1`  
**分支：** `origin/agent/2/f5af605668db`  
**第一父：** `de0e4e013b1364ce55cd5e6d116c11fdd6b19d2d`  
**DAV-660：** 独立审核 ✅通过（只读审核仅在 DAV-660）

Cursor 隔离 worktree `/tmp/ta-iso-da9a69d`：`tests/test_data_collector*` + `tests/test_news_event*` + `tests/test_tushare*` **165 passed** in 6.52s。

范围仅 `_fetch_all` 用同一份 `cn_akshare.get_cninfo_announcements` 写入 envelope，以及新测试。未进 `route_to_vendor`、未接 IR、未接线 `anns_d`、未部署。
