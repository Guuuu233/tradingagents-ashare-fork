# V-03a DAV-866 合入前全量对照（2026-09-13）

## 执行对象

- 候选：`020d3e3b18147f5ea20e90d3878b20952d20fd97`
- 候选直接父：`8c69eab186bde58e49cbc134fb4da5f015c77e92`
- 线上/主干基线：`a227cdc3bb466edf2e910419cb6013cfc021d309`
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20）
- 命令：`env -u PYTHONPATH .../python -m pytest -q -p no:randomly`
- 执行位置：两个独立 detached worktree，未使用脏根目录工作树。

## 结果

| 版本 | passed | failed | skipped | deselected | 收集用例 |
|---|---:|---:|---:|---:|---:|
| 候选 `020d3e3` | 4291 | 19 | 1 | 3 | 4314 |
| 父版本 `8c69eab` | 4288 | 19 | 1 | 3 | 4311 |

候选相对父版本新增的 3 个通过用例来自 DAV-866 默认 provenance 覆盖；失败项没有新增。

## 失败集合

候选与父版本的 pytest `lastfailed` 集合逐项相同，双向差集均为空（`comm` 无输出）：

1. `tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[False]`
2. `tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[True]`
3. `tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_dual_horizon_persists_debate_states_to_result_data`
4. `tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_intent_hoist_persists_debate_states_to_result_data`
5. `tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_hold_decision_persists_note_and_metrics_consistency`
6. `tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_with_legitimate_empty_probability_persists_note_and_4_of_4_metrics`
7. `tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_single_horizon_persists_debate_states_to_result_data`
8. `tests/test_h1b_gates.py::TestH1bV2OnlySampleFilteringAndIndustry::test_verify_h1b_gates_script_runs_and_verifies_v2_only`
9. `tests/test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param`
10. `tests/test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json`
11. `tests/test_signal_processing.py::test_negated_build_position_is_not_buy`
12. `tests/test_signal_processing.py::test_negated_buy_phrase_is_not_buy`
13. `tests/test_signal_processing.py::test_plain_hold_is_hold`
14. `tests/test_social_data_api.py::test_status_proves_four_dimensions_independent`
15. `tests/test_two_stage_analyst_topology.py::test_full_7_analysts_two_stage_topology`
16. `tests/test_two_stage_analyst_topology.py::test_mixed_subset_topology`
17. `tests/test_two_stage_analyst_topology.py::test_phase1_only_topology`
18. `tests/test_two_stage_analyst_topology.py::test_phase2_only_topology`
19. `tests/test_v03_return_measure.py::test_rt_s4_david_account_clean_population_counts`

## 结论

满足 DAV-866 合入前门禁：相对父版本零新增失败；候选代码审查 DAV-868 已由“代码审核员”对同一完整 SHA 明确准予合入（HIGH=0、MEDIUM=0、LOW=1）。本记录只支持受保护 FF，不自动授权部署、重启或真实数据写入。
