# DAV-593 线性 FF（运维）

**触发**：Cursor 已对 exact SHA 签署「准予合入」（DAV-593 / DAV-594 同 SHA 复测 43+120 passed）。

## 精确目标
- **合入 SHA**：`e10b106df9d3173258b0a3fefc90ba7f3559f109`
- **源分支**：`origin/codex/dav-confirmation-gate-lifecycle`
- **目标主干**：`codex/dav-4-p2a-trunk`
- **当前主干 tip（开工前必须再 fetch 核验）**：`c72dd7b6098297efbec80931dda8bf509c8d8709`
- **方式**：线性 `git merge --ff-only`（禁止 merge commit / rebase 改写已审 SHA）

## 步骤
1. `git fetch origin`
2. 确认 `origin/codex/dav-confirmation-gate-lifecycle` 仍指向 `e10b106df9d3173258b0a3fefc90ba7f3559f109`
3. 确认 `origin/codex/dav-4-p2a-trunk` 仍为 `c72dd7b6098297efbec80931dda8bf509c8d8709`（若已前进，停并回报；不得自行改 SHA 强推）
4. **必须用干净隔离 worktree**，禁止在宿主脏 checkout 操作
5. 检出 trunk → `git merge --ff-only e10b106df9d3173258b0a3fefc90ba7f3559f109`
6. `git push origin codex/dav-4-p2a-trunk`
7. 回报：新 tip 完整 40 字符 SHA（应为 `e10b106…`）、`git log -1 --oneline`、push 结果

## 禁止
- 不准予部署 / 不重启生产 / 不改 feature flags / 不改 DB
- 不碰宿主脏文件；不 reset / clean / broad-stage
- 不开始 DAV-595
