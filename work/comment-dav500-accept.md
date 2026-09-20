验收通过（Cursor 独立核验）。

- `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk` = `a375bdc9cf3d07584eb6c28c637bde9bca867876`
- 运维回归：`113 passed`
- Cursor 准予合入前隔离：同集 **113 passed**（拒收过 `0214a4f` 超时映射后返修）
- 线性 FF only；无 merge；脏文件未动
- **未部署**。不准予部署。
- 本卡与 DAV-499 可关 `done`。不要开部署卡。下一刀 P2-T9 另开。
