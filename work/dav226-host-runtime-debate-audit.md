# DAV-226 宿主运行证据审计：dd2d报告辩论状态为何为0

## 审计位置（必须使用宿主，不是隔离checkout）

- 项目：`/Users/davidliu/Documents/TradingAgents-AShare`
- DB：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`
- 服务日志：当前uvicorn后台日志、`/tmp/tradingagents-ashare.log`、`work/dav213-service.log`（按实际存在文件使用）
- Report ID：`dd2d5cf259db490d946f18ed38704718`
- 服务SHA：`394e3efe08fef60f728345cc8eb9300c8ad0d693`

## 已知事实

- reports表只有`result_data` JSON，没有`debate_count`或`debate_rounds_executed`列；禁止查询虚构列。
- 报告真实存在，status=completed，user_id=`429163f7-50b6-4982-8bdf-96ae99506843`。
- `result_data.investment_debate_state.count=0`，`risk_debate_state.count=0`。
- 请求明确传入3/3覆盖，持久配置保持3/1。

## 只读任务

1. 用sqlite读取该report的完整result_data、created_at/updated_at，并按UTC→AWST转换日志时间窗口。
2. 检查该时间窗口服务日志中的：
   - graph启动与config轮次；
   - Bull/Bear、Aggressive/Neutral/Conservative、research_manager/risk_manager初始化和消息；
   - prompt injection、debate token/message；
   - final_state / result assembly线索。
3. 检查`api/main.py`实际普通`POST /v1/analyze`路径：astream循环如何累加final_state。重点对比：
   - 双horizon路径是否`horizon_final.update(chunk)`；
   -普通单horizon路径是否仅`final_state = chunk`导致末块覆盖累计状态。
4. 检查LangGraph的`stream_mode`真实为`values`还是updates，不能凭注释猜。
5. 明确判断：辩论未执行、config未生效、或执行后状态在astream/result assembly丢失。
6. 检查`work/verify_dav213_cmb_analysis.py`的真实schema漂移：`reports`无`report_text`列，应使用具体报告字段/result_data。
7. 输出证据表：命令、真实DB结果、日志行、文件:函数:行号、最小修复建议。

禁止改代码、提交、重启、部署或新发报告。不要在隔离checkout里查宿主报告。立即执行。