验收通过（Cursor 独立核验）。

- `git ls-remote origin refs/heads/codex/dav-4-p2a-trunk` = `0d21d1950350d42f65a7e3cb42040c05552eb3e0`
- 运维回归：定向 **43** / 扩跑 **128 passed**
- Cursor 准予合入前：定向 43 / 扩跑 128；H1 复现已绿
- 独立审核员 DAV-512：✅通过
- 线性 FF only；未部署
- 本卡与 DAV-511 / DAV-512 / DAV-510（回顾）可关 `done`
- 下一刀：恢复 P2-T12（须以 `0d21d19` 为新基线 rebase/续作）
