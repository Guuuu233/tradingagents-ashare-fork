## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`aa2750fb3d9e1580885c5a24ccc90c0ae66accea`  
**父 / 基线**：`ccda1be9c96e4d9a5f334fa03280342badeb4306`  
**分支**：`origin/agent/dev2/a11-t5-no-vacuous-pass`  
**独立审核员 DAV-560**：✅通过

### Cursor 证据

1. 远端 tip 可达；单 commit；2 文件；父 = 当前主干 tip。
2. 隔离复测：`test_h1b_gates` + `test_tplus5_shadow_backfill` → **60 passed**。
3. `due==0` → `completeness_rate=0.0` / FAIL / `reason=no_due_samples`；禁止 N≥60 虚高 100%。

### 决定

**准予合入** `aa2750fb3d9e1580885c5a24ccc90c0ae66accea`。  
**不准予部署。** 不开加权。

请运维仅对该 SHA 线性 FF。
