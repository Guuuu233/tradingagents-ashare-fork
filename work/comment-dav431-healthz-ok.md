编排侧刚读到宿主 `/healthz` 已是 tip：

```json
{"status":"ok","commit_sha":"11309037de9334820603eec6dd801f291172f6ed","build_identity":"tradingagents-api@11309037de9334820603eec6dd801f291172f6ed","version":"0.6.0","source_id":"tradingagents-api","executor_queued":0,"executor_threads":1}
```

请确认持久 3/1 未变，并说明 `executor_threads` 从 4→1 是否预期；若非预期请恢复为 4。达标后贴报告并将本卡标 done。样本补齐脚本已 resume（当前 9/10），勿再 kill。
