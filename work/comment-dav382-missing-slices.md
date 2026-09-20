当前worktree仍在父SHA `f89b600`、两文件未提交；192项绿仅证明 `_build_result_payload` 切片，**禁止现在提交**。DAV-383指出的两项仍未体现在当前diff：

1. `api/main.py:3128-3167` 多horizon聚合顶层；
2. `api/main.py:3241-3274` query/单周期hoist顶层；
3. `_apply_structured_report_fields` 后刷新field completeness的时序。

请继续同一worktree严格TDD：

- 先在现有 `test_dual_horizon_persists_debate_states_to_result_data` 和 `test_intent_hoist_persists_debate_states_to_result_data` 增加顶层protocol/flags/metrics断言并确认RED；
- 构造structured/resolved字段后，断言最终job result与saved result中的`field_completeness`使用写入后的confidence/probability/target/stop/note，不是提前0/4，并确认RED；
- 在api/main里新增一个小的私有P1-M挂载/刷新helper（若能消除重复），由 `_build_result_payload`、dual/query组装和每个 `_apply_structured_report_fields` 后调用；只复用`get_protocol_metadata/calculate_all_debate_metrics`；
- 顶层挂载所有8个P1-M字段；生产new-state nested同步metrics，legacy nested无key逐字保持；不得修改输入对象；无LLM/网络/DB；
- dry_run不挂载；canonicalize无需修改。

完成后复跑192项矩阵、整个state persistence、dual/report/job tests、replay、compileall、diff-check；仍只两文件，不跑全量。推送前展示最终diff中确实包含3128/3241/每个structured后刷新调用点。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
