## 准予合入（不准予部署）

**合入 SHA：** `4fdcf8efa841c2a881d82429febd48578d544c94`  
**分支：** `origin/agent/dev2/track-b2-ingestion-status`  
**第一父：** `418a310259b5269fe38ce45efdadd6b3c9f360bd`  
**独立审核：** DAV-653 / DAV-650 评论 ✅通过

**Cursor 隔离：** `/tmp/ta-iso-4fdcf8e`  
- `tests/test_run_social_ingestion_guards.py` + `tests/test_social_data_api.py` → **19 passed**, 4 warnings（既有 JWT 夹具）  
- 扩大回归（含 analyst/report/archive/importer/collector）→ **89 passed**, 4 warnings in 34.60s

白名单 5 文件。CLI 走 `build_mediacrawler_argv`，不再接受任意命令列表。空归档不得 `operational`。禁止部署、active、真采集。
