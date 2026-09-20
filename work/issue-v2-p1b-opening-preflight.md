## P1-B/B1 Opening只读架构与兼容预检

固定基线：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`。严格只读，0 code/test/config/DB/service changes；禁止全量。

审计并输出精确路径矩阵：
1. `AnalyzeRequest.config_overrides`→runtime config→TradingAgentsGraph→Propagator initial state，如何请求级启用v2且默认关闭；是否需要api/main.py，若需要给精确行号与最小替代。
2. `protocol_stage`的唯一权威来源：accepted valid messages与state字段如何同步；失败attempt不能推进stage/count。
3. validate_debate_response旧Check A/B/C/D如何按v1/v2+stage分相；Opening的2-3 claim/3 battlefield校验最小位置。
4. update_debate_state_with_payload中claim当前只写`round_index=message_index`，需如何同时保存debate_round/message_index/stage/battlefield且不破坏legacy。
5. Bull/Bear最终prompt所有泄漏通道：history/current_response/claims/focus/unresolved/round_summary/round_goal/past_memory/retry_instruction/custom prompt；指出每个必须隔离或保留的字段。
6. 七报告字节对称的验证方法；现有horizon_ctx/role prompt差异不应误判为报告不对称。
7. 现有测试哪些硬编码Bear message2必须respond/target，列出需要v1 fixture显式化和新增v2 fixture的文件；不得为绿测删除legacy断言。
8. B1建议精确文件范围、风险、PASS/BLOCK。特别检查是否应在B1暂不改conditional_logic，避免不完整v2路由被部署。

输出文件:行号、生产调用链、最小测试清单；0 tests/0 changes；未合入/未重启/未上线，3/1不变。不要mention项目调度助手。