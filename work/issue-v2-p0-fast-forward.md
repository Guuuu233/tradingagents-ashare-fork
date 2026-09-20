## 固定对象

- 仓库：`https://github.com/Guuuu233/1.git`
- 目标主干：`codex/dav-4-p2a-trunk`
- 当前主干：`45821dd4f21a5f65578dbf54f5d916970ae835c0`
- 已审核组合分支：`agent/1/5f311b604593`
- 已审核候选 SHA：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 最终独立复审：DAV-353 PASS
- 回归证据：106 定向通过；replay 通过；全量 1802 passed / 1 skipped / 0 failed；diff-check/compileall 通过

## 任务

项目主管执行唯一合入动作。不得修改代码、配置、数据库、用户模型/providers/role bindings/API Key；不得重启服务或跑业务分析。

1. 先用代理可用路径核验远端 refs：主干仍为 `45821dd`，候选分支仍为 `23e09e5`。
2. 核验 `git merge-base --is-ancestor 45821dd 23e09e5` 成立，提交链为四个线性提交，无额外祖先。
3. 将 `target/codex/dav-4-p2a-trunk` **fast-forward** 到 `23e09e5`。禁止 merge commit、禁止 squash、禁止 cherry-pick 重写、禁止 force push。
4. 推送后用 `git ls-remote target refs/heads/codex/dav-4-p2a-trunk` 读回，必须精确等于 `23e09e5ed2cc8623b88bcbda94d701df5d6b2150`。
5. 输出实际命令、推送结果、读回 SHA、祖先关系；明确：已合入=是，未重启=是，未上线=是。

若远端主干已被其他提交推进或不能 fast-forward，立即 BLOCK，不得强推。评论不要 mention 项目调度助手，直接交付给 Hermes。