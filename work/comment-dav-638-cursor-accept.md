## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `e8130b69eb153fc4d0a3079f3ec7986bd9f58dd5`  
**父 tip：** `5e254acae668bc0b391f62624a8f3057c732e683`  
**分支：** `origin/agent/1/5e081ba65b7d`

DAV-639 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav638-e8130b6`：`pytest tests/test_tushare_daily_basic.py tests/test_cn_akshare_backup_sources.py` **80 passed**，`git diff --check` 0。白名单 2 文件。复用 `_tushare_post`；按列名 zip；`trade_date > as_of` 不发网关；未注入 `scale_metrics`。残留：`_fetch_tushare_daily_basic` 超过 60 行；`_tushare_post_once` 把全部 API 的 HTTP 403 改成 `permission_denied`（资金流回归仍绿）。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止 C-09-3。
