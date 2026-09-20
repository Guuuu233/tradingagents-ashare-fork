项目规划：work/2026-08-04-development-plan.md 的 M2 里程碑（对齐竣工定义②：裁决必须基于证据强度）。

## 问题（KNOWN_ISSUES #2）
研究经理/裁决者看不到分析师一手报告（fundamentals/market/news 正文只进 memory，不进 prompt）。裁决依据是"哪方论证写得漂亮"而非"哪方证据硬"。这与你 3000 字提示词第 4 节"裁决必须依据证据强度"直接冲突。

## 现状接线（已核实的覆盖表）
- bull/bear: market/social/news/fundamentals 全部进 prompt；smart_money/volume_price 部分
- research_manager: sentiment 进 prompt，其余只进 memory；smart_money+volume_price 进 prompt
- risk 三方: 全部进 prompt
- trader/risk_manager: 四个都只进 memory
- macro 分析师: 产出既不进 prompt 也不进 memory（纯浪费）

## 任务
1. 核实 macro 的接线缺失（git log 查三个新 analyst 加入时的模板改动）
2. 补齐 research_manager 的一手报告访问（至少 fundamentals/market/news 摘要）
3. 评估并实现提示词注入扩展到 trader + 风险经理（3000字版约束目前只覆盖 bull/bear/manager）
4. 回归验证：裁决引用证据的密度是否提升

## 验收
- research_manager 能看到关键分析师报告摘要
- 3000 字版提示词（置信度上限/证伪条件）对关键角色生效
- 测试覆盖 + 独立审查

完成小段工作后自动 @项目调度助手 触发下一轮。
