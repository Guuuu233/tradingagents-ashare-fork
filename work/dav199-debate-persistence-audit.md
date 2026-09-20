# DAV-199 辩论轮次与状态持久化只读审计

## 固定事实

- 正确用户：`davidliu022305@gmail.com`，user_id `429163f7-50b6-4982-8bdf-96ae99506843`
- 京东方报告：`4af2575757e24892a2a248b0c07314b6`，真实 completed
- 用户运行配置：`max_debate_rounds=2`、`max_risk_discuss_rounds=1`
- 日志实际 Bull 2 次、Bear 2 次；研究经理与风险经理完成
- reports.result_data 中未发现 `investment_debate_state` / `risk_debate_state`
- reports 表无独立 debate state 列

## 目标

只读定位两个独立问题，不修改代码或用户配置：

1. 运行时轮次覆盖链：`user_llm_configs` → `_build_runtime_config` → `TradingAgentsGraph` → `ConditionalLogic`，证明本报告为何是2轮/1轮；确认单次 `/v1/analyze` 请求通过 allowlist `config_overrides.max_debate_rounds=3,max_risk_discuss_rounds=3` 是否能覆盖且不持久化。
2. 状态持久化链：Graph final_state → `trading_graph.propagate` 返回 → API `_run_job_inner` → report_service canonicalize/save → ReportDB.result_data，定位 `investment_debate_state` 和 `risk_debate_state` 在哪一步丢失。

## 必须输出

- 精确文件:行号与数据流；
- 京东方实际可证明的 debate count；
- result_data 应保留的最小结构（history、count、bull/bear history、judge_decision、claim ids；risk 对应字段）；
- 是否涉及 DB schema（优先只写入现有 result_data，不改 schema）；
- 最小修复文件与测试建议：先失败测试，修后验证；
- 不修改用户配置，不重跑报告，不提交代码。

## 交付

在 DAV-204 评论给出只读 PASS/缺陷报告；如果确认代码缺陷，创建/建议一个单问题修复任务，宁德时代当前运行不受影响，招商银行继续锁定。
