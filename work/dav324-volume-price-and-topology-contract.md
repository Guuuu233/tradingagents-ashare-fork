# P1：量价分析师深度联想契约与基本面-新闻并发承诺修正

**前置：DAV-323完成后从最新集成主干开工；当前保持todo，避免zh.py冲突。**

## 缺陷
- 量价分析师静态评分58：Prompt缺What/Why/SoWhat/WhatNext、未指导如何使用已注入Phase1宏观/技术/情绪、缺严格数据缺失与时滞/验证条件，测试漏掉volume_price。
- 基本面Prompt声称引用新闻报告，但fundamentals/news同属Phase2并发，永远不可见，契约虚假。

## 契约
1. volume_price Prompt新增四步结构：What客观K线量能；Why供求/筹码；SoWhat阶段与假突破；WhatNext结合Phase1宏观/技术/情绪，给验证条件、失效条件、时间窗口；不得让宏观叙事覆盖量价事实。
2. 强制引用至少一个Phase1结论并说明“确认/冲突/无关”，无可用上下文明确缺失；量价/日期/支撑阻力必须真实存在于输入。
3. 加严格【数据缺失】纪律；不得从无volume推断吸筹/派发。
4. 基本面Prompt删除“从新闻分析师报告提取”不可能承诺，改为使用直接新闻数据或Phase1报告；不改为三阶段（用户要求效率且现拓扑可用）。
5. 补volume_price进analyst prompt测试矩阵及负例；不得改分析顺序/轮数/用户配置。

白名单：zh.py/en.py、volume_price_analyst.py（仅注入标签/manifest如必要）、fundamentals Prompt对应测试。

验收：Prompt与mock节点证明Phase1引用；无量价数据fail-closed；定向pytest/compileall/diff-check；精确SHA。禁止@调度助手。
