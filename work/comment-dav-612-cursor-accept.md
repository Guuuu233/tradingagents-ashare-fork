## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `9b3de9b0153e4727896692e42d22223a9b0f4efe`  
**父 tip：** `158ccb7e9df49ba0d0510d1a50b4cc82c6c3ed55`  
**分支：** `origin/agent/1/8def9b4761b9`

DAV-613 独立审核 ✅通过（exact SHA）。Cursor 隔离 `/tmp/iso-dav612-9b3de9b` 复测同一 SHA：39 passed。`git diff --check` 0。changed files 仅 2 个白名单文件。有规范化 URL 时跨源 `source_hash` 相同；无 URL 时哈希仍含 source。无 `canonical_event_id`。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。
