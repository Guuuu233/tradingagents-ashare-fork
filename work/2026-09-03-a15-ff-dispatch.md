# Track A15 线性 FF（运维）

**触发**：Cursor 已对 DAV-583 签署「准予合入」。

## 精确目标
- **合入 SHA**：`31c32f0f877e86fc3c06eb58a34b4dc08a453044`
- **源分支**：`origin/codex/dav-a15-industry-backfill-ensure`
- **目标主干**：`codex/dav-4-p2a-trunk`
- **方式**：线性 `git merge --ff-only`（禁止 merge commit / rebase 改写已审 SHA）

## 步骤
1. `git fetch origin`
2. 确认 `origin/codex/dav-a15-industry-backfill-ensure` 仍指向 `31c32f0f877e86fc3c06eb58a34b4dc08a453044`
3. 确认 `origin/codex/dav-4-p2a-trunk` 仍为 `03cfb47c2f01981a6f80254fba8ac6a857b4d987`（若已前进，先停并回报；可能需在新 tip 上 rebase 后重新审核——**不得自行改 SHA 强推**）
4. 本地检出 trunk → `git merge --ff-only 31c32f0f877e86fc3c06eb58a34b4dc08a453044`
5. `git push origin codex/dav-4-p2a-trunk`
6. 回报：新 tip 完整 40 字符 SHA（应为 `31c32f0…`）、`git log -1 --oneline`、push 结果

## 禁止
- 不准予部署 / 不重启生产 / 不改 feature flags / 不改 DB 生产数据
- 不碰脏文件 `AGENTS.md`、`frontend/src/services/api.ts`（若工作树脏，用干净 worktree 操作）
