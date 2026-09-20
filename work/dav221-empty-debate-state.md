# DAV-221 P0：3/3请求完成但辩论状态落库为0

## 精确生产证据

- 当前远端/宿主 SHA：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 服务 PID：11490，DB：`data/tradingagents.db`
- 正确账户：`davidliu022305@gmail.com` / `429163f7-50b6-4982-8bdf-96ae99506843`
- 请求：`600036.SH`，`config_overrides={"max_debate_rounds":3,"max_risk_discuss_rounds":3}`
- 报告：`dd2d5cf259db490d946f18ed38704718`，status=completed
- 但result_data：
  - `investment_debate_state.count=0`，history/bull_history/bear_history/claims/judge均空
  - `risk_debate_state.count=0`，三方history/claims/judge均空
  - `risk_feedback_state`存在
- 用户持久配置仍3/1，符合不得篡改要求。
- 旧验收脚本因查询不存在的`report_text`列而提前退出，不能作为PASS。

## 目标

定位并修复：为什么API请求的3/3覆盖没有进入最终Graph状态，或真实辩论已执行但最终状态被空初始值覆盖。

## 施工纪律

1. 从精确主干`394e3ef`新分支施工；一个写入者。
2. 先追踪完整数据流：AnalyzeRequest.config_overrides → `_build_runtime_config` → TradingAgentsGraph构造 → ConditionalLogic轮次 → graph final state → api/main result assembly → report_service。
3. 必须区分“实际没跑辩论”和“跑了但持久化被空状态覆盖”，用该report的服务日志/LLM调用记录证明。
4. 先写失败测试，生产形状必须可达；不得只mock最终dict。
5. 修原路径，不新增_v2，不改用户配置、Provider、模型、Key、Prompt、轮次默认值。
6. 完成后要求：单次3/3覆盖得到investment count=6、risk count=9；不带覆盖仍保持用户持久3/1；最终state非空且machine validation通过。
7. 推送远端分支和精确SHA；给RED→GREEN、定向测试、全量测试、compileall、diff-check证据。
8. 禁止合入、部署、重启或再跑真实LLM报告。

立即施工。