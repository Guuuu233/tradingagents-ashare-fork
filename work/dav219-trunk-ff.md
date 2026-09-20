# DAV-219：主干 clean fast-forward 合入

立即执行，不要等待调度助手。

## 固定输入

- 当前主干必须仍是：`target/codex/dav-4-p2a-trunk@7ef89f63662ce01bacdcda9dd0996060ce903c83`
- 合入目标：`target/agent/2/01a02043@6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`
- 父提交：`7c2f19ab3f5d1176b235306bf3421fdbc14570b5`
- 祖先：`7ef89f63` 是 `6a8a896b` 的祖先，ahead 4 / behind 0
- 提交链：`ce6073d5`(DAV-214) → `97b597d1`(DAV-215窗口) → `7c2f19ab`(DAV-215 guard) → `6a8a896b`(DAV-216 EOF空行)
- DAV-217 独立终审 PASS（审查对象为 `7c2f19ab` 业务三连；`6a8a896b` 仅删3处测试EOF空行）
- DAV-218 预检 READY

## 唯一动作

将 `6a8a896b` clean fast-forward 到 `target/codex/dav-4-p2a-trunk`。

若主干已不是 `7ef89f63`，立即停止，不强推。

## 验收

1. `git ls-remote` / `gh api` 确认主干精确 SHA = `6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`
2. 祖先关系仍 clean
3. 评论给出主干新旧 SHA

禁止改代码、禁止部署、禁止重启、禁止跑报告。
