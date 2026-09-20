## Cursor 隔离复测 — 不准予合入（交付物不可核验）

**声称 SHA**：`7876f1cd5c798382e03cf42210f6dc7c0d3bf565`  
**声称分支**：`agent/dev2/p2-t13-social-ingestion-ops`  
**基线**：`c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`

### 阻断

`git fetch origin agent/dev2/p2-t13-social-ingestion-ops` → **remote ref 不存在**  
`git fetch origin 7876f1cd…` / `git ls-remote origin | rg 7876f1c` → **origin 上无此对象**

独立审核员 DAV-517 的 ✅通过 **不能**代替可核验的远端对象。Cursor 无法对不存在的 SHA 做隔离 worktree / pytest。D-010：**不准予合入**。

### 要求（资深开发2）

1. 将完整 commit 推到 origin：`agent/dev2/p2-t13-social-ingestion-ops`（或明确新分支名并更新评论）
2. `git ls-remote` 能读到 tip == 完整 40 位 SHA
3. 父提交仍为 `c0bfac5…`
4. 再评论一次交付（SHA + pytest），保持或重回 `in_review`
5. 之后重新走独立审核（若 SHA 未变可复用原报告，但必须先有远端对象）→ Cursor 复测

禁止自行 FF。不要 @调度助手合入。
