# P1-M 指标语义只读审计

## 固定对象
- 父候选：`33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- 返修 DAV-370 正在只补生产挂载；本卡严格只读，不修改任何代码/测试/配置/DB/服务，不运行全量测试。

## 必审项
1. 每个指标的 numerator/denominator/rate 是否数学一致，尤其 `bull_bear_verified_delta` 不能用“verified数量差/总证据数”却把 rate写成两率绝对差而无清晰语义。
2. 数字事实提取是否误把股票代码、日期、年份、版本号、claim id、价格区间的两端等当成独立报告事实；手工复算 2-3 个小夹具。
3. evidence recycling 的 round判断是否兼容真实字段 `debate_round` 与 `message_index`，不能只认不存在/不稳定的 `round_index`。
4. verified metrics 是否能读取真实 `manager_verdict.claim_evidence_summary` 形状与 evidence_verification；DB golden中真实字段直查，不凭测试假fixture。
5. field completeness 对“probability为空但有 extraction_note=概率未提供/未提取”应如何计分：规格要求语义允许为空时有note，不应简单永远算缺失；给出明确契约判断。
6. A/B harness 对同一 legacy result_data仅覆盖 protocol version时 delta=0，应明确这是结构兼容基线，不得冒充真实v2质量对比；检查输出标签是否误导。
7. 检查 `agent_states.py` 新增类型/import是否有重复 Optional、无用导入或默认浅拷贝共享可变对象风险。

## 交付
- 文件:行号、生产真实数据形状证据、手工复算、PASS/BLOCK。
- 若有阻断，给最小修复文件/测试范围；不要实现。
- 0 code changes / 0 tests；未合入/未重启/未上线。

不要 mention 项目调度助手。严禁运行全量pytest。