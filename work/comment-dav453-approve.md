## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`  
**父 / 基线**：`9d5d53dd89bed6dcbfa7dd3aa991771993dddfea`  
**分支**：`origin/agent/cursor/a6-h1b-gates-v2-only`  
**独立审核员 DAV-538**：✅通过

### Cursor 证据

1. 远端 tip 可达；单 commit；白名单 3 文件；无 `work/h1b_gates_report.json`；`credit_weighting_enabled` 默认仍关。
2. 隔离复测：`test_h1b_gates` + `test_credit_weighting` **27 passed**。
3. 独立审核员另报 24 + 35 passed。

### 决定

**准予合入** `46a6dfee9601ca679b8d22c0c86a931fccb59b63`。  
**不准予部署。** 不开加权。Gate 4 未开。

请运维仅对该 SHA 线性 FF。
