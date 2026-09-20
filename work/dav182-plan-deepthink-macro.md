# 宏观多维联动与全行业深度思考能力增强计划书 (DAV-182)

## 一、任务背景与目标
增强 TradingAgents-AShare 系统中 15 个 Agent（特别是宏观分析师、基本面分析师、新闻事件分析师、社交舆情分析师等）的宏观视野与深层联想推理能力，实现从“全球宏观 → 中国宏观/大盘 → 产业链联动 → 微观企业”的多层次联动分析。
不仅限于半导体/石油，而是对所有行业与标的均具备深度联想与情景推演能力。

## 二、执行原则与铁律（严格遵守 AGENTS.md）
1. **修改原路径**：严禁新建 `_v2` / `_new` 等并行逻辑，提示词原地改写，保持末尾机读摘要 `<!-- VERDICT: ... -->` 契约不变。
2. **防前视偏差**：不得为历史回测引入未截断的未来数据；新增知识库为静态常识结构。
3. **数据层严格规范**：按列名取数，禁止位置切片；失败显式返回 `【数据获取失败】...`。
4. **单任务单关注点**：分阶段实施，严格执行代码审核与测试回归。

## 三、分阶段实施拆解

### 阶段一：纯静态全行业宏观联动知识库与 Prompt 原地深度增强（零数据风险，立即施工）
- **T1**：新建 `tradingagents/knowledge/industry_linkage.py` 与 `macro_events.py`，构建覆盖 20+ 主流行业的上下游、宏观驱动、周期及地缘敏感图谱。
- **T2**：改写 `tradingagents/prompts/zh.py` 中的 `macro_system_message`，注入大盘、全球宏观、货币利率、情景推演框架。
- **T3**：改写 `tradingagents/prompts/zh.py` 中的 `fundamentals_system_message`，强化产业链议价权、行业周期、宏观敏感度与极端情景推演。
- **T4**：改写 `tradingagents/prompts/zh.py` 中的 `news_system_message` / `social_system_message` / `market_system_message` / `smart_money_system_message`，注入 What/Why/SoWhat/WhatNext 及跨市场联动思维。
- **T5**：编写知识库与提示词语义单测，确保全量测试无回归。

### 阶段二：DataCollector 全球宏观与大盘视图接入（受控扩展）
- **T6**：在 `data_collector.py` 中安全聚合全球主要指数与大类资产历史数据（复用已有 provider 接口，严格进行 `<= trade_date` 历史截断）。
- **T7**：在对应分析师节点安全接入全球大盘上下文。
- **T8**：执行完整端到端测试与历史日期无泄漏验证。

## 四、当前指令
请项目主管按阶段一（Prompt 与知识库增强）立即派发任务并安排专业开发与代码审核。
