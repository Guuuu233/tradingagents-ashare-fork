## R3 未完成：改动仍在工作树，未提交、未推送

上一个返修运行（`01a0a4a1`）已于 10:47 结束，但**没有产生候选 SHA**——修改还留在未提交工作树里，且自报证据为 `159 passed, 1 skipped, 1 failed`，关联回归未全绿。此后本卡空转 3.5 小时。工作树改动有丢失风险，请优先落盘。

本轮请完成并且只完成这些：

1. **先修掉那 1 个 failed**，贴出失败用例全名、失败原因、修复后的复跑结果。
2. **提交并推送**到精确 ref `origin/agent/support/dav946-on-b95a-r2`（注意是 `b95a`，不是 `b95`）。
3. 提交前用 `git ls-remote origin codex/dav-4-p2a-trunk` 核对主线 tip，**直接父必须等于当时的 tip**；若期间主线已推进，先 rebase 到新 tip 再交付。
4. 按固定全量命令跑一次（解释器 `.venv310`、`-p no:randomly`、deselect DAV-979 用例、隔离 `DATABASE_URL`），贴精确数字与 `python -V` 输出。
5. 交付格式：完整 40 位 SHA、直接父、精确远端 ref、白名单文件清单、`git diff --check`、clean 工作树。

**止损**：全量卡住超 10 分钟不要反复重跑，记录百分比 + `ps -o %cpu,etime` + `/usr/bin/sample <pid> 5 -mayDie` 栈回报即可。运维已实测存在两类病灶：38% 处 `%CPU=0` 死锁（DAV-979），以及 96% 处 `%CPU≈171` 高 CPU 空转，均为主干既有，不计入你的新增失败。

不合入、不部署、不重启、不写生产库。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
