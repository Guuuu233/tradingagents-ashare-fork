## DAV-596 线性 FF 完成（Cursor 执行；未部署）

为避免与运维 agent 竞态改写，已 cancel 进行中的 596 task，由 Cursor 在隔离 worktree `/tmp/iso-ff-dav596-e10b106` 执行 `git merge --ff-only`。

- 祖先：`c72dd7b6098297efbec80931dda8bf509c8d8709` 是 `e10b106df9d3173258b0a3fefc90ba7f3559f109` 的 **第一父提交**
- 推送：`c72dd7b..e10b106` → `origin/codex/dav-4-p2a-trunk`
- `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk` = `e10b106df9d3173258b0a3fefc90ba7f3559f109`
- GitHub API `git/ref/heads/codex/dav-4-p2a-trunk` = 同一 SHA
- `/healthz` 仍为 `c72dd7b…`：**预期**。本轮无部署授权。

禁止部署、禁止改 DB、禁止开 `credit_weighting_enabled`。
