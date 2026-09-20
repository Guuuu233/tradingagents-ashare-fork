DAV-383路径审计给出可复现BLOCK；当前实现必须在同一两文件范围内同时闭环，禁止只修 `_build_result_payload` 后交付。

## 必补的三个同文件切片

1. **单horizon流式/非流式**：`_build_result_payload`挂载顶层P1-M与生产new-state nested P1-M，legacy nested无key逐字保持。
2. **query/dual-horizon手工组装路径**：`api/main.py:3241-3274`从`primary_r`漏hoist 8个P1-M字段；多horizon聚合`3128-3167`顶层也缺。必须在现有组装点复用/统一挂载，不能只把字段留在short_term/medium_term。
3. **field completeness计算时序**：graph/result builder时confidence/probability/target/stop/extraction_note尚未由resolve写入，提前计算会假0/4。所有路径必须在 `_apply_structured_report_fields` 完成后刷新 `calculate_all_debate_metrics(result)`，同步顶层和生产new-state nested metrics，再create_report/job event。不要重复算法；可在api/main定义一个小私有挂载/刷新helper以消除三路径复制，但仍只改api/main。

## 测试必须补齐

仍只改`tests/test_debate_state_persistence.py`：
- stream_events=True单horizon：job result和saved report字段完整，field completeness反映resolve后的字段；
- stream_events=False/query hoist路径：顶层protocol/flags/metrics存在；
- dual-horizon多周期：顶层与short/medium P1-M一致；
- legacy nested state逐字保持；输入不原地修改；6条消息保留；challenge=legacy_no_data。

当前RED证据有效。继续按垂直RED→GREEN完成以上三项；禁止新增文件、P1-B逻辑、配置/DB/服务修改。推送前定向测试、replay、compileall、diff-check；不跑全量。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
