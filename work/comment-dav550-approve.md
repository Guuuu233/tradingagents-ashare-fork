## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`018fdef6f79c82fc8b24e2ac4630774f57cf6338`  
**父 / 基线**：`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`  
**分支**：`origin/agent/dev2/a8-calibration-v2-sample-honesty`  
**独立审核员 DAV-551**：✅通过（exact tip；56 / 150 passed）

### Cursor 证据

1. 远端 tip 可达；单 commit；父 = 当前主干 tip。
2. 隔离复测：`tests/test_calibration_service.py` + `tests/test_decision_status.py` → **56 passed**。
3. 承认 completed 合格 v2 `winner∈{bull,bear}` 且 probability 为 null 的样本；分桶/Brier 仅在显式概率上计算；暴露 `winner_only_admitted` / hits / rate。
4. 无 confidence→probability 编造；hold 窗口未缩短。

### 决定

**准予合入** `018fdef6f79c82fc8b24e2ac4630774f57cf6338`。  
**不准予部署。** 不开加权。

请运维仅对该 SHA 线性 FF。
