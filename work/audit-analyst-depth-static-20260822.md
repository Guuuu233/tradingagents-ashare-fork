# 七分析师深度联想静态/契约审计（只读）

基线 `1eb280b`。对照两份原始文档，审计七分析师：macro/fundamentals/news/sentiment/market/smart_money/volume_price。

逐角色检查：输入数据、第一阶段上下文、产业链/RAG/历史案例注入、What/Why/SoWhat/WhatNext、量化传导、时滞、证据来源、数据缺失纪律、与其他报告交叉引用。检查两阶段拓扑是否真正使第二阶段能看到第一阶段报告，不只看 edge 名称。

输出每角色评分量表和可自动化验收规则（关键词不能单独算通过），给文件:行号与测试缺口。输出 `work/audit-seven-analysts-depth.md`，0代码改动，提交文档 SHA。
