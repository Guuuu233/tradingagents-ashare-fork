## Cursor 同 SHA 隔离复测 — 准予合入

**候选 SHA（完整 40 位）**：`c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`  
**父提交**：`0d21d1950350d42f65a7e3cb42040c05552eb3e0`（含 H1 empty-window）  
**分支**：`agent/dev2/p2-t12-social-report-gates`  
**独立审核员 DAV-508**：✅通过（必要前置，非最终许可）

### Cursor 证据

1. `git merge-base --is-ancestor 0d21d19… c0bfac5…` → PASS；父提交匹配。
2. 隔离 worktree `@ c0bfac5`，无代理 pytest：
   - 核心 5 模块：**50 passed**, 4 warnings in 0.52s
   - 扩展 15 模块：**165 passed**, 4 warnings in 34.67s
3. 先前 High 返修 spot-check：
   - disabled/shadow + legacy 正文无「不可判断」→ pass
   - active + empty 无标记 → fail；有标记 → pass
   - `apply_report_quality_gate` 写入 social depth 失败 ledger → OK

### 决定

**准予合入** `c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`。  
**不准予部署。**

请运维仅对该 SHA 做线性 FF（见即将开启的 FF 卡）。禁止 merge / 禁止其它 SHA。
