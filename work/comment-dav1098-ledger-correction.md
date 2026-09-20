## 账本二次更正：`ac8955e` 陈述作废（附 reflog 证据）

我此前汇报中关于 `ac8955e` 的说法（「只存在本地、被 ff 顺带推上远端」）**不成立，作废**。真实事实：

- `ac8955e8cee5dc86da6acc50b5abf6e646dab627`（DAV-1091）是 `7a98819` 的**祖先**，在本次合入前早已在远端 trunk。
- reflog 实测（可复验）：
  - `refs/remotes/origin/codex/dav-4-p2a-trunk@{2}: update by push → ac8955e`（合入前远端就已推过）
  - `codex/dav-4-p2a-trunk@{1}: merge origin/codex/dav-4-p2a-trunk: Fast-forward → ac8955e`（本地 trunk 当时也更新过）
- 我 checkout 时看到的 `behind by 1 commit` 应是本地分支在后续被重置回 `7a98819` 所致（steer 中临时起的服务进程曾写工作区，我执行过 `git reset --hard 7a98819` 恢复），并非「远端缺这个提交」。

**更正结论**：本次合入推送内容是且仅是 `7a98819..41772fa` 四个提交（DAV-1098 卡 1），无任何计划外提交进入远端。无安全后果，但陈述方向错误——fast-forward 合并不会「顺带推送本地独有提交」，推送内容以远端基点与推送目标的差集为准。与「直接父 ≠ 分支基点」一并入档，作为本轮两条口径教训。

（附：误开的 uvicorn 进程 17936 已当场终止，实际服务进程为 17940，监听正常。）
