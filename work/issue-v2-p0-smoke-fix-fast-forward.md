## Fast-forward 固定对象

- 目标分支：`codex/dav-4-p2a-trunk`
- 当前远端 trunk：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 已审核组合分支：`agent/1/d39965d412f0`
- 已审核组合 SHA：`50e115347b49bcb9e767c593296045a356099006`
- DAV-365 最终静态复核：PASS
- 全量原始结果：1852 passed / 1 skipped / 0 failed

## 唯一动作

1. 重新 `git ls-remote`，若 trunk 不再等于 `23e09e5`，立即停止并报告。
2. 验证 `23e09e5` 是 `50e1153` 的祖先。
3. 只执行 fast-forward refspec：
   `git push target 50e115347b49bcb9e767c593296045a356099006:refs/heads/codex/dav-4-p2a-trunk`
4. 读回远端 trunk，必须精确等于 `50e115347b49bcb9e767c593296045a356099006`。

禁止修改代码、配置、DB、用户设置；禁止重启服务；禁止运行测试；禁止 force push。交付 push 原始摘要和读回 SHA。不要 mention 项目调度助手。