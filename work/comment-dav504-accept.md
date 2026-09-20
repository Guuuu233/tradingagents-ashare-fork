验收通过（Cursor 独立核验）。

- `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk` = `0cc34278c8024680e0b687bd029295876b6e0c98`
- 运维回归：`99 passed`
- Cursor 准予合入前隔离：brief **29** / 扩跑 **99 passed**
- 线性 FF only；无 merge；脏文件未动
- **未部署**。不准予部署。
- 本卡与 DAV-503 可关 `done`。不要开部署卡。下一刀 P2-T11 另开。
