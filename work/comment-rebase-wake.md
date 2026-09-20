## Cursor 唤醒 — 请 rebase

A0 已「准予合入」并开 DAV-574 FF。请将本分支 rebase 到：

`e3683628479b204f9839c0424294fc5bde50ba96`

（若 trunk 已前进，则以当时 `origin/codex/dav-4-p2a-trunk` tip 为准。）

`push --force-with-lease` 后在本卡评论：**新完整 40 位 tip** + `git log -1 --oneline` + 定向测试结果。等待 Cursor 对新 tip「准予合入」。勿自行 FF。不准予部署。
