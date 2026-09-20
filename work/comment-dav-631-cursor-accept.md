## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `d56232f81acc4d501eadb8ce808784baecfebb94`  
**父 tip：** `d4d145fae714a21bd919fad3ad66dba7fa1ae852`  
**分支：** `origin/agent/1/f33e81bab088`

DAV-633 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav631-d56232f`：仅新增 `work/2026-09-05-c09-daily-basic-eval.md`；`pytest -k tushare` **32 passed**。冻结 `trade_date <= as_of`、禁止最新市值回填历史、缺列上报。无 `tradingagents/` 改动。残留：markdown 行尾空白。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止接线生产。
