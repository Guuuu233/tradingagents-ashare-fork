## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `158ccb7e9df49ba0d0510d1a50b4cc82c6c3ed55`  
**父 tip：** `4f6e45ec4a9a87da32c24ebfaea92f9ecf66dd9d`  
**分支：** `origin/agent/2/a981560c9daa`

DAV-611 独立审核 ✅通过（exact SHA）。Cursor 隔离 `/tmp/iso-dav610-158ccb7` 复测同一 SHA：63 passed。`git diff --check` 0。changed files 仅 2 个白名单文件。同一规范化 URL、不同 source、不同标题 → 一个 cluster。无 `canonical_event_id`。未接 CNINFO。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。
