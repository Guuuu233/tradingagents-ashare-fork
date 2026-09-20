## 只读审计对象

- trunk/service：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 真实报告：`af8f029a1ab842eca7ba80d43ec882d8`
- 分析完整完成：7 分析师 + 6 条投资辩论 + research manager + trader + 3 风险辩手 + risk manager。
- DB `llm_call_logs` 对该 report_id 只有 7 条，agent_name 恰为七分析师；0 NULL report_id。

## 任务

严格只读，不修改代码/测试/配置/DB/服务/用户设置：

1. 追踪所有角色的 LLM 调用链：analyst、bull/bear researcher、research_manager、trader、aggressive/conservative/neutral、risk_manager。
2. 确认 `log_llm_call` 的实际调用点、哪些 client/path 会调用、哪些不会；区分流式/非流式、LangChain wrapper、直接 invoke/stream。
3. 判断 DAV-340 的 D1 需求“新落库 llm_call_logs.report_id 非空”是仅保证已有日志关联，还是要求所有角色调用都落日志。以原 issue/测试/代码证据为准，不扩写需求。
4. 对本报告时间窗口核对服务日志中的 HTTP LLM 请求数量与 DB 7 行差异，给出确定缺口清单。
5. 给出若需修复的最小文件范围、RED 测试设计和风险；不要实现。

交付：文件:行号、调用图、实际角色覆盖表、结论 PASS（预期边界）或 BLOCK（D1 覆盖不完整）。明确 0 code changes / 0 tests，未合入/未重启/未上线。不要 mention 项目调度助手。