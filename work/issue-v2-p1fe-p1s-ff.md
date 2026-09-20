# P1-FE + P1-S 合入主干（禁止 force）

## 独立核验（Cursor，非代理口头）

当前 `target/codex/dav-4-p2a-trunk` = `0554216305b3c860cbe893681335b6b1a29e17ef`，服务 `/healthz` 同 SHA。

| 泳道 | 远端 branch | 精确 SHA | vs trunk | 独立测试 |
|---|---|---|---|---|
| P1-FE DAV-416 | `agent/1/2f93ccafaab1` | `059f0810674b107b1ee1f56cfe44a5cd704b74f5` | 祖先=trunk；仅 3 个 frontend 文件 | `cd frontend && npm test` → 10 files / 69 passed |
| P1-S DAV-417 | `agent/2/c27baef37a33` | `d9c7014684c844f0aa0042bb822986f41bb0c7ca` | 祖先=trunk；5 个后端/测试文件，与 FE 零重叠 | `.venv310` pytest 声称集合 → **310 passed** |

两提交是 trunk 上的兄弟，**不能**用单次 FF 吃掉两个 SHA。文件无冲突。

独立抽查：`isChallengePenetrated` 要求 fatal+verified+adopted，unsupported/contradicted/source_unavailable 不得击穿；`credit_weighting_enabled` 恒 False；`research_manager.py`/`zh.py` 无 shadow 信用数字注入。

## 唯一允许动作

施工远端：`https://github.com/Guuuu233/1.git`，主干 `codex/dav-4-p2a-trunk`。

1. 读远端：trunk 仍必须严格等于 `0554216…`；两候选 branch 仍严格等于上表 SHA。若任一变化 → BLOCK。
2. 先 FF 后端（线性）：
   `git push <target> d9c7014684c844f0aa0042bb822986f41bb0c7ca:refs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `d9c7014…`。
4. 再把 frontend 合入，**保留** `059f081` 为祖先（merge 或 cherry-pick 后读 `merge-base --is-ancestor 059f081 trunk` 必须为真）。禁止 squash 丢掉 FE SHA。禁止 `--force`。
5. 最终读回 trunk SHA，必须同时：
   - `merge-base --is-ancestor d9c7014684c844f0aa0042bb822986f41bb0c7ca <trunk>`
   - `merge-base --is-ancestor 059f0810674b107b1ee1f56cfe44a5cd704b74f5 <trunk>`
6. 评论写出最终精确 trunk SHA。

## 禁止

改代码/测试、force push、rebase、改 3/1、改模型/绑定/Key、重启服务、部署。本卡只推主干。

完成后 mention 代码运维测试员，不要 mention 项目调度助手。
