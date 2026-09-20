## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `90fcfc435ccc4f4efd32d5ac5c4fbfbfe45975e9`  
**父 tip：** `0dfb5c5e8507cf994e05000c954ccc57538d01c1`  
**分支：** `origin/agent/2/41451c5029b3`

DAV-607 独立审核 PASS。Cursor 隔离 `/tmp/iso-dav606-90fcfc4` 复测同一 SHA：167 passed（含 isolation 17 + 关联回归）。`git diff --check` 0。

changed files 仅 3 个白名单文件。缺省为 `vendor_qfq`，断言含 `!= "raw"`。未接 RAW/PIT 数据源。

**准予合入。** 线性 FF。禁止 merge。禁止部署。禁止开加权。
