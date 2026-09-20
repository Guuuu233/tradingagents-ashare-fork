[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) 请立即开工 Track A10（本卡 DAV-556）。

基线 tip（完整 40 位）：`4493177eeefa4a7aabfc05904c156ccb0106d06e`  
分支建议：`agent/dev2/a10-backfill-db-path`

问题：`backfill_report_industry.py` 与 `backfill_tplus5_shadow.py` 的 `--db-path` 未接线（A9 同款）。详见本卡描述。

单关注点；先 push；完整 tip + pytest → `in_review`。禁止 FF / 部署 / 开加权 / 动脏文件 trio / 对生产库实写回填。
