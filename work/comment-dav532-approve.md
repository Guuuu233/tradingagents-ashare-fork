## Cursor 同 SHA 隔离复测 — 准予合入

**候选 tip**：`0cda99b6072116874b7a458432d0c7bc7b0a29e3`  
**基线父**：`883bdedb64693d6f1a9923a9b515243a0677d89f`  
**分支**：`origin/agent/dev2/p2-med3-integrity-residuals`  
**独立审核员 DAV-533**：✅通过

### Commit 清单

| ID | SHA |
|---|---|
| R1 | `daaee8276b66913ffa2985cca6511960f8a5f470` |
| R2 | `ea7f73619f6741c72a184b3b6464f3b007b3898d` |
| R3 | `0cda99b6072116874b7a458432d0c7bc7b0a29e3` |

### Cursor 证据

1. 三 commit 可达；祖先含 MED2 tip；未删 legacy。
2. 隔离：定向 7 模块 **101 passed**；rollout+import CLI **19 passed**。
3. Spot-check：`JSONDecodeError` 行级捕获；`REASON_SOCIAL_INVALID_INGEST_RUN` / `ARCHIVE_CORRUPT`；importer 无 commit → ValueError「default fabrication is forbidden」。

### 决定

**准予合入** tip `0cda99b6072116874b7a458432d0c7bc7b0a29e3`。  
**不准予部署。** Gate 4 未开。

请运维仅对该 tip 线性 FF。
