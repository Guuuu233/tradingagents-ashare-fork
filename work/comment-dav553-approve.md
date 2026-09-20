## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`4493177eeefa4a7aabfc05904c156ccb0106d06e`  
**父 / 基线**：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`  
**分支**：`origin/agent/dev2/a9-h1b-gates-db-path`  
**独立审核员 DAV-554**：✅通过

### Cursor 证据

1. 远端 tip 可达；单 commit；2 文件；父 = 当前主干 tip。
2. 隔离复测：`test_h1b_gates` + `test_calibration_service` + `test_decision_status` → **87 passed**。
3. `--db-path` 优先只读 `mode=ro`；缺失/`RuntimeError` 非零退出；无静默 golden。

### 决定

**准予合入** `4493177eeefa4a7aabfc05904c156ccb0106d06e`。  
**不准予部署。** 不开加权。

请运维仅对该 SHA 线性 FF。
