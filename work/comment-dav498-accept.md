验收通过（Cursor 独立核验）。

- `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk` = `ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`
- 运维回归：`105 passed`
- Cursor 准予合入前隔离：同集 **105 passed**
- 线性 FF only；无 merge；脏文件未动
- **未部署**。不准予部署。
- 本卡与 DAV-497 可关 `done`。不要开部署卡。下一刀 P2-T8 另开。
