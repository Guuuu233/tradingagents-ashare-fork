# P1：历史报告逐轮辩论与事实核验可视化

**基线/线上：`79a552f4b28742983c01d3af91fa45bc17f191f0`。前端独立分支，不合主干。**

## 现状

`result_data.investment_debate_state` 已持久化：round_messages、attempts、claims、manager_verdict、evidence_verification、report_manifest；实时 Analysis 页有内存 DebateDrawer，但历史 Reports/ReportViewer 无入口，刷新后不可审计。

## 契约

1. 历史报告详情增加“多空辩论与裁决证据”入口，不改变现有10个报告章节。
2. 展示逐轮：轮次、Bull/Bear、正文、responded/target/new claim IDs、information_gain_score、新证据数、parse_status；无效attempt单独折叠显示。
3. Claims账本：claim ID、阵营、轮次、文本、证据、置信度、状态、被谁回应。
4. 研究总监：胜负、方向、仓位、入场/目标/止损、赔率、自洽状态、adopted/rejected。
5. 事实核验：verified/unsupported/contradicted/source_unavailable，fatal醒目标红；不得泄漏模型密钥或隐藏reasoning_content。
6. 老报告缺字段时优雅显示“此报告生成时尚未记录结构化辩论”，不报错。
7. 直接从报告 detail API result_data渲染；不依赖实时Zustand内存。
8. 控制长文本与内存：默认折叠，每轮按需展开，列表key稳定。

白名单：frontend相关类型、ReportViewer/Reports、可复用/新增HistoricalDebateDrawer、前端测试。禁止改后端、配置、模型。
验收：新结构完整渲染；旧结构fallback；fatal/invalid样式；构建/tsc/前端测试；推精确SHA。禁止@调度助手。
