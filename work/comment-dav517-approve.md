## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`7876f1cd5c798382e03cf42210f6dc7c0d3bf565`  
**父提交**：`c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`  
**分支**：`origin/agent/dev2/p2-t13-social-ingestion-ops`（`git ls-remote` 已一致）  
**独立审核员 DAV-517**：✅通过

### Cursor 证据

1. 远端对象可达；`merge-base --is-ancestor c0bfac5… 7876f1c…` PASS；白名单 7 文件 +1386。
2. 隔离 worktree `@ 7876f1c`，无代理：
   - 新增 CLI/守卫：**14 passed** in 0.27s
   - 扩展社交矩阵（含新增）：**168 passed**, 4 warnings in 32.01s
3. 契约 spot-check：5 必需 CLI `required=True`；loopback / sqlite / IngestionLock；默认 comments=true、sub_comments=false；钉住 `d6f7c5bb…`；文档为禁止落盘凭据说明（无明文 secret）。

### 决定

**准予合入** `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`。  
**不准予部署。**

请运维仅对该 SHA 线性 FF（即将开 FF 卡）。禁止 merge / 禁止其它 SHA。
