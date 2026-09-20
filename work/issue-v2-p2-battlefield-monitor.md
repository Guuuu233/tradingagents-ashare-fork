# P2-M 只读：v2 报告五战场覆盖监控

## 基线

主干/服务 `4fa2e2453de739c0ec10d42a4bac591a6effff53`。规格 10.1。

当前库内 v2 完成报告只有 3 份（B4：000858/000063/000651），**不足 10**。本卡只读统计，禁止新开分析任务、禁止改代码/配置/3/1。

## 要做

1. 从 `data/tradingagents.db` 只读列出所有 `protocol_version=v2_structured_disagreement` 且 completed 的报告。
2. 每份统计 bull/bear opening 的 battlefield 集合、是否各 ≥3、macro_policy / fundamentals 是否进入 opening。
3. 检查是否出现连续 3 份只集中 capital_flow + price_volume。
4. 产出计数：样本 n、达标数、缺口。n<10 时明确“监控未满，不得宣称 P2 战场验收通过”。

评论贴表：report id、ticker、日期、双方战场列表。不要 mention 项目调度助手。
