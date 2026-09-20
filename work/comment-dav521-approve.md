## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`7db2882d43276c22dd87e259b259dd1f500c6bd0`  
**父提交**：`7876f1cd5c798382e03cf42210f6dc7c0d3bf565`  
**分支**：`origin/agent/dev2/p2-t14-social-rollout-gates`（`git ls-remote` 一致）  
**独立审核员 DAV-521**：✅通过

### Cursor 证据

1. 祖先含 T13 tip；白名单 4 文件 +577/-4。
2. 隔离 worktree `@ 7db2882`，无代理：
   - 定向（rollout + analyst_separation + collector）：**37 passed** in 32.50s
   - 扩展社交相关：**153 passed**, 4 warnings in 32.10s
3. Spot-check：shadow `source_mode=legacy_proxy`（M6）+ `direction_allowed=False` + legacy 正文；active insufficient **无** `get_news`/legacy 回退。

### 决定

**准予合入** `7db2882d43276c22dd87e259b259dd1f500c6bd0`。  
**不准予部署。**

请运维仅对该 SHA 线性 FF。禁止 merge / 禁止其它 SHA。
