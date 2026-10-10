# work/

一次性脚本与审计产物目录。**注意（DAV-1771 B-6b 起）**：本目录下读取
`reports.result_data` 的脚本仅适配**明文存储**的数据库。对启用
`REPORT_STORAGE_MODE=compressed` 的库请改用
`tradingagents.storage.compressed_json.decode_result_data`（BLOB → dict）
或在 SQL 里 `COALESCE(result_data_zst, result_data)` 取出后交给它解码；
写路径同步使用 `encode_result_data` / `compress_json_bytes` 维护
`result_data_zst` + `result_data_zst_len`。参考实现：
`scripts/backfill_tplus5_shadow.py`（`_rd_text` / `_RD_COL` 模式）。
