# DAV-150 建立 DAV-119 单一 integration branch

## 背景

DAV-149 最终组合验证已确认 BLOCKED：DAV-139、DAV-141、DAV-143 的 reviewed SHA 各自有未列出的父提交，DAV-143 还依赖 49b6a4e 并不能从 target trunk 直接 cherry-pick；此前组合树依赖手工移植，不能作为 exact integration 交付。

## 固定基线

target trunk：`codex/dav-4-p2a-trunk`

SHA：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`

## 唯一目标

由项目主管建立并推送一个明确的、可独立检出的非主干 integration branch，显式纳入 DAV-119 已审阅修复的全部 prerequisite 父链；不得再使用 detached 临时树手工移植作为交付。

必须记录：

- integration branch 名称和远端 ref；
- 最终 integration SHA；
- 从 trunk 到 integration 的完整 ancestry/merge-base；
- 每个 DAV 修复对应的 commit/文件范围；
- 同文件冲突的解决理由；
- 是否包含 DAV-135、DAV-136、DAV-139、DAV-141、DAV-143、DAV-144、DAV-145 以及它们的父提交；
- `git diff --check` 与 clean checkout 证据。

## 边界

- 只做 integration branch 物化，不修改用户配置、providers、模型绑定、API Key、数据库或服务；
- 不推送 target trunk，不合入主干，不重启服务；
- 不通过手工复制几行代码掩盖缺少的父提交；如果某个提交无法安全纳入，必须停下并列出缺失 ancestor/冲突；
- 不把真实 EM/THS 可比性缺口伪装成代码完成。

## 验收

integration branch 必须能被独立 clone/checkout，且在 clean checkout 中：

1. `git merge-base --is-ancestor f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef <integration_sha>` 成功；
2. `git log --graph` 能看到完整父链；
3. 目标文件和测试均来自可追溯 commit，不是临时手工 patch；
4. `.venv310` 定向测试/compileall/diff-check 由后续 DAV-151 执行，不在本卡伪造；
5. 完成后把精确 branch/SHA 交给 DAV-151 做组合回归。

新 integration branch/SHA 产生前 DAV-119、DAV-140、DAV-122 继续 blocked；不重启、不上线。若无法建立干净父链，明确失败，不再重复长上下文。 

执行者：项目主管。
要点：这是编排/集成任务，不是重新派发 coder。
