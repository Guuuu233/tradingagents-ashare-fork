# DAV-231 《项目加强方案》代码证据矩阵

## 强制环境
checkout `https://github.com/Guuuu233/1.git@394e3efe08fef60f728345cc8eb9300c8ad0d693`，cd进入返回workdir/1并确认HEAD。方案原文 `/Users/davidliu/Downloads/项目加强方案`，宿主DB只读路径 `/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`。

## 唯一交付：矩阵，不要写可行性报告
逐项表格列：方案项 | 状态(完成/部分/未开始/证据不足) | 文件:行号 | 测试 | 真实report ID | 缺口。

必须覆盖：
1. 阶段一6子任务；
2. 三股回归与A/B（dd2d completed但debate=0，不能PASS）；
3. 阶段二5行业：实际读取INDUSTRY_LINKAGE_MAP并列行业名；2行业MVP不得标5行业完成；
4. 两阶段分析架构是否生产可达；
5. 动态RAG：必须找到vector/embedding/retrieval才可标RAG，静态Python知识库/拼接不是RAG；
6. 阶段三27行业、国际对标、历史案例学习闭环。

最后给下一轮施工依赖图。禁止说“完全对齐”却把已完成项列为未来任务；禁止虚假百分比。只读。