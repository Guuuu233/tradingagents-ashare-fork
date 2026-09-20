# 全面终审 泳道三：数据层与外部接口适配器审计 (tradingagents/dataflows/ & llm_clients/)
【严格只读，不修改代码，不合入，不重启】

重点审计范围：
1. `tradingagents/dataflows/providers/`（cn_akshare_provider.py, cn_fuyao_provider.py, cn_baostock_provider.py 等）
2. `tradingagents/dataflows/`（fund_flow_evidence.py, trade_calendar.py, interface.py 等）
3. `tradingagents/llm_clients/`（openai_client.py, factory.py, thinking_cleaner.py 等）

审查要求：
- 逐行检查 Tushare/东财/同花顺/新浪的数据解析防前视、单位换算（万元/亿元）、缺字段显式标记；
- 检查外部 HTTP/API 调用的超时、重试、并发控制与连接池泄露；
- 检查 LLM Client 的 token 统计、思考独白剥离与网络瞬时错误重试逻辑；
- 产出详细缺陷清单（文件、行号、问题现象、风险等级、优化建议）；
- 评论末尾不要 mention 项目调度助手。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)
