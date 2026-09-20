# 全面终审 泳道二：多智能体与图工作流审计 (tradingagents/agents/ & graph/)
【严格只读，不修改代码，不合入，不重启】

重点审计范围：
1. `tradingagents/graph/`（trading_graph.py, data_collector.py, setup.py, propagation.py, reflection.py 等）
2. `tradingagents/agents/analysts/`（7 个分析师节点）
3. `tradingagents/agents/researchers/`（多头、空头、研究总监辩论节点）
4. `tradingagents/agents/risk_mgmt/` 与 `trader/`（风控三方、组合经理、交易员）

审查要求：
- 逐行检查各节点对 LLM 流式输出（astream）与非流式回退（invoke）的处理是否规范一致；
- 检查 token emit 机制是否有遗漏（确保所有分析师和辩论者都能正确驱动前端气泡）；
- 检查 Prompt 模板变量拼装、A 股制度检查字段（解禁、质押、两融、业绩预告等）是否完整注入且防前视；
- 检查图状态传播在节点失败时的降级容错机制；
- 产出详细缺陷清单（文件、行号、问题现象、风险等级、优化建议）；
- 评论末尾不要 mention 项目调度助手。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
