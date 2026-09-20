DAV-392预检已PASS，B1继续冻结`conditional_logic.py`与`api/main.py`，不提前开放HTTP白名单、不部署不完整v2。请吸收以下精确边界：

1. `protocol_stage`以accepted valid message count为权威；失败attempt不得推进count/stage或注册claim。
2. Opening双盲必须隔离history/current_response/claims/focus/unresolved/round_summary/past_memory，并使用stage专属retry；七报告原始字段和manifest保持一致。
3. **纠正规格冲突**：当前`battlefield`是每claim单值，硬闸又要求每方至少3个不同战场；因此2条claim不可能同时满足。不得采用预检报告里“2条claim仅2战场+gap”的放宽。合法Opening实际必须是3条claim、3个不同合法battlefield；1/2/4条均应invalid_protocol。若要支持2条claim，必须先另行设计多battlefield字段并获批，本卡禁止。
4. v1默认路径的Bear message2 responded/target硬断言必须完整保留；新增v2 fixture，不改写旧fixture来迁就新协议。
5. 请求级B1测试可直接通过Graph/Propagator config启用；HTTP `config_overrides` allowlist留到B4统一集成。

继续当前唯一worktree严格O1→O2→O3 RED/GREEN；禁止提交前遗漏Bear opening retry泄漏测试。