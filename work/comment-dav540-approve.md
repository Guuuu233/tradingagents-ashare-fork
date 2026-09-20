## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`d2f8aa05579d0520abe942972889a82060ea65e7`  
**父 / 基线**：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`  
**分支**：`origin/agent/dev2/a5-tplus5-shadow-backfill`  
**独立审核员 DAV-541**：✅通过

### Cursor 证据

1. 远端 tip 可达；单 commit；白名单 3 文件；不开加权。
2. 隔离复测：**54 passed**（`test_tplus5_shadow_backfill` + `test_shadow_credit` + `test_h1b_gates`）。
3. 独立审核员另报 19 / 54 / 82 passed。

### 决定

**准予合入** `d2f8aa05579d0520abe942972889a82060ea65e7`。  
**不准予部署。** 不开加权。

请运维仅对该 SHA 线性 FF。
