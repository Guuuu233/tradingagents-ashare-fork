# DAV-228 代码级《项目加强方案》完成度审计

## 强制环境

1. 执行：`multica repo checkout https://github.com/Guuuu233/1.git --ref 394e3efe08fef60f728345cc8eb9300c8ad0d693`
2. 必须`cd`进入工具返回的`workdir/1`路径，运行`git rev-parse HEAD`确认SHA。
3. 方案原文：`/Users/davidliu/Downloads/项目加强方案`。
4. 宿主DB：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`，只读查询真实report ID。

若没有完成checkout与cd，不得输出结论；不得说“当前目录无文件”。

## 必须交付完整矩阵

1. 阶段一6项Prompt/流程：文件:行号、测试文件、真实E2E证据与缺口。
2. 三股回归与A/B：逐个report ID标有效/无效；`dd2d5cf...` completed但debate count=0，不是3/3 PASS。
3. 阶段二5行业要求：读取`INDUSTRY_LINKAGE_MAP`并列实际行业；核验provider、DataCollector、宏观/基本面Prompt注入。两行业MVP不得写成5行业完成。
4. 两阶段分析架构：检查graph实际拓扑和生产路径，不能凭文件名推断。
5. 动态RAG：检查是否有向量库/embedding/retrieval；静态Python知识图谱或直接拼接不得标为RAG完成。
6. 阶段三27行业、国际对标、历史案例学习闭环逐项状态。
7. 每项只能标：完成/部分完成/未开始/证据不足。附文件:行号、测试、真实report ID；禁止虚假百分比。
8. 给出下一轮施工拆分：同树代码写入串行，独立审核/数据探针可并行。

必须在issue评论中提交完整矩阵和结论；只写“下一步/需提供仓库”视为任务失败。

只读，不改代码、提交、部署、重启或新跑报告。