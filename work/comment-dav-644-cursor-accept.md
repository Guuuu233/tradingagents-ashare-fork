## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `b9e7238376e2ba74f1e3510f90f3b5a27dd8c08c`  
**父 tip：** `d82b0da8c6e32c0e85371cd9abb9d85ebe920387`  
**分支：** `origin/agent/2/fa915cd87957`

DAV-645 独立审核 ✅通过。Cursor 隔离 `/tmp/ta-iso-b9e7238`：disclosure/repurchase/forecast/daily_basic/backup/announce/fund_flow/cninfo metadata **211 passed**；`git diff --check` 0。白名单 2 文件。复用 `_tushare_post`；PIT 仅 `ann_date`；`actual_date` 不参与截断；空表 `collateral_empty`；`canonical_event_id` 恒 `None`。残留：`_fetch_tushare_disclosure_date` 超过 60 行；签名仍可把 `actual_date` 作为网关查询参数（行可见性仍走 `ann_date`）。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止改巨潮主源挂载。禁止 C-09-3。
