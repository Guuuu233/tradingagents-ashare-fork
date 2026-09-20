## ✅ 主干首次取得完整 RT-FULL 基线（不设看门狗）

在主线 `28d1adc6a3e89ab344d198d8be564b6892aa3562` 的只读检出上，解释器 `.venv310`（Python 3.10.20），隔离 `DATABASE_URL`，**不设任何看门狗**：

```
20 failed, 4777 passed, 1 skipped, 3 deselected, 182 warnings, 3 subtests passed in 1700.10s (0:28:20)
```

**套件能跑完。** 此前「主干 RT-FULL 无法取得基线」的结论作废——它不是死锁，只是慢。

### 耗时构成直接印证了根因

`--durations` 前 7 名全部来自同一个文件，且耗时几乎完全一致：

| 用例 | 耗时 |
|---|---|
| `test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_dual_horizon_report_persists_and_reads_both_horizons` | 181.40s |
| `...::test_sqlite_json_bind_processor_and_native_json_extract` | 181.34s |
| `...::TestScaleConsumptionBoundaries::test_boundary_failure_empty_unavailable_never_asserted_as_zero` | 181.33s |
| `...::test_boundary_legal_decimal_zero_asserted_as_valid_zero` | 181.33s |
| `...::test_single_horizon_report_persists_and_reads_all_scale_fields` | 181.31s |
| `...::test_decimal_serialization_roundtrip_preserves_exact_precision` | 181.30s |
| `...::test_boundary_reference_only_true_preserved_in_prompt_and_persistence` | 181.29s |

**7 × ≈181.3s ≈ 21 分钟**，占整轮 28 分 20 秒的绝大部分。`181.3 ≈ 60s × 3`，正是 `DEFAULT_PROVIDER_RESOURCE_POLICY.timeout_seconds=60.0` 在 vendor 链上重试三次的结果——与前一条评论中 faulthandler 栈显示的「真实 baostock TCP 调用」完全吻合。

其余可疑慢项同源：

| 用例 | 耗时 | 备注 |
|---|---|---|
| `test_knowledge_rag.py::test_reload_rag_vocabulary_resets_global_index` | 55.35s | 之前分文件跑被 120s 看门狗判为 HANG，实为该文件两条慢用例合计约 95s |
| `test_knowledge_rag.py::test_rag_vocab_env_override` | 40.29s | 同上 |
| `test_analyst_knowledge_and_macro_views.py::test_resolve_industry_context_unknown_fallback` | 31.64s | |
| `test_social_analyst_separation.py::test_news_analyst_does_not_leak_social_sentinel` | 28.11s | |

即 `tests/test_knowledge_rag.py` 从来没有挂死，是我的看门狗设短了。

### 基线失败集合（20 项 / 10 个文件）

```
tests/test_cninfo_disclosure_metadata.py::test_dav623_same_query_response_captured_without_duplicate_query
tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[False]
tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[True]
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_dual_horizon_persists_debate_states_to_result_data
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_intent_hoist_persists_debate_states_to_result_data
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_single_horizon_persists_debate_states_to_result_data
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_with_legitimate_empty_probability_persists_note_and_4_of_4_metrics
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_hold_decision_persists_note_and_metrics_consistency
tests/test_game_theory_integration.py::test_rt10_real_graph_builder_routing_reachability_and_execution
tests/test_h1b_gates.py::TestH1bV2OnlySampleFilteringAndIndustry::test_verify_h1b_gates_script_runs_and_verifies_v2_only
tests/test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param
tests/test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json
tests/test_signal_processing.py::test_negated_buy_phrase_is_not_buy
tests/test_signal_processing.py::test_negated_build_position_is_not_buy
tests/test_signal_processing.py::test_plain_hold_is_hold
tests/test_social_data_api.py::test_status_proves_four_dimensions_independent
tests/test_two_stage_analyst_topology.py::test_full_7_analysts_two_stage_topology
tests/test_two_stage_analyst_topology.py::test_phase1_only_topology
tests/test_two_stage_analyst_topology.py::test_phase2_only_topology
tests/test_two_stage_analyst_topology.py::test_mixed_subset_topology
```

与此前分文件口径（8 个失败文件）相比，**多出两个文件**：`test_h1b_gates.py`、`test_recalculate_weekly_metrics.py`。二者在单文件独立进程下不失败、在全量上下文中失败，属**跨用例状态污染**导致的差异，需另行排查（这两条都涉及子进程/脚本调用）。

### 门禁口径更新

1. **RT-FULL 恢复为可用门禁**：候选须在同一口径下跑完全量，与本基线做失败集合逐项对照，有新增失败即不得 PASS。单轮约 28 分钟，可接受。
2. **禁止再用短看门狗判定「挂死」**。若必须设上限，不得低于 45 分钟；超时须记录 `--durations` 与 faulthandler 栈，不得直接判为死锁。
3. 分文件对照法仍可作为快速预检，但**以全量对照为准**；此前用分文件口径放行的 6 次合入结论不受影响（两侧条件相同），但其「HANG」标注应改称「超过看门狗」。
4. 在离线护栏（见前一条评论建议 1）落地前，任何一轮 RT-FULL 都会产生真实外网请求，结果对网络状况敏感——这本身是待修复的问题，不应长期作为常态。

基线原始日志保留在运维侧 `/private/tmp/rtfull-real.log`。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
