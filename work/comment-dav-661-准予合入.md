## 准予合入（不准予部署）

**合入 SHA：** `9a2878c8e94a0bf5ccab3ba222067d9f47d89138`  
**分支：** `origin/agent/1/8f355cd233fb`  
**第一父：** `da9a69d6dbf11a0e359732bce3d9b11968b3e1b1`  
**DAV-662：** 独立审核 ✅通过（只读审核仅在 DAV-662）

Cursor 隔离 worktree `/tmp/ta-iso-9a2878c`：IR 专项 + 公告/旁证/collector 关联套件 **168 passed** in 13.43s。

范围仅 `_fetch_all` 接入 `get_cninfo_ir_surveys` 与新测试。空串降为 `provider_failure`，未进 `route_to_vendor`，未改公告抓取，未部署。
