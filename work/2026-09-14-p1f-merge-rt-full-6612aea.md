# P1-F 连板天梯：实施、复审、RT-FULL 与合入证据

核验日期：2026-09-14（Australia/Perth）

## 固定对象

- 设计基线：`d816a8c7c57c850ff2e1d57d852d3ad7f6e0d477`
- 实施候选：`6612aea82e0fb3212d3682c5d09835f529ffec16`
- 候选直接父：`623c37a71f7a50fdf9945f9158f78cf9f57e5b0a`
- 线上回归基线：`026349614a3f1b92a95dc06c0515f10ebec193bc`
- 目标分支：`origin/codex/dav-4-p2a-trunk`
- 设计与红队范围：`work/2026-09-14-p1f-limit-up-ladder-design.md`

候选相对设计基线的改动严格为 8 个白名单文件：

1. `tradingagents/dataflows/providers/cn_fuyao_provider.py`
2. `tradingagents/dataflows/interface.py`
3. `tradingagents/default_config.py`
4. `tradingagents/agents/utils/game_theory_tools.py`
5. `tradingagents/agents/utils/agent_utils.py`
6. `tradingagents/graph/data_collector.py`
7. `tradingagents/dataflows/social/prompt_formatter.py`
8. `tests/test_limit_up_ladder.py`

候选工作树 clean，`git diff --check d816a8c...6612aea...` 通过；返修提交只改 provider 和天梯测试两个文件。

## 同 SHA 只读复审

DAV-908 由**代码审核员**完成，结论为 `PASS`。复审确认：

- 完整 SHA、直接父、实际远端分支和 clean 状态一致；
- 三个 DAV-906 阻断已真实修复：紧凑日期先规范化、`item[idx].date` 与窗口日期逐项一致、`board_caps` 必须为完整六板块字典；
- 设计第 6 节 10 条红队验收全部通过；
- 唯一 Fuyao 来源、历史日期请求前拒绝、失败不回退/不合成/不裁剪、`limit_up_ladder` 与 `zt_pool` 分栏、无方向/投票/权重/收益信号等契约保持；
- 审查全程只读，未改代码、未提交、未合入、未部署、未写生产库、未启动真实分析或社交采集。

审查关联定向集合为 `300 passed`；这是定向证据，不替代下列全量对照。

## RT-FULL 对照

两个新的 detached worktree 使用同一机器、同一 Python 3.10.20 和同一命令：

```text
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q
```

工作树：

- 基线：`/private/tmp/ta-p1f-rt-baseline-0263496-20260914`
- 候选：`/private/tmp/ta-p1f-rt-candidate-6612aea-20260914`

结果：

| 对象 | 选中测试 | 结果 |
|---|---:|---|
| 基线 `026349614...` | 4445（总收集 4448，3 deselected） | `19 failed, 4425 passed, 1 skipped, 3 deselected` |
| 候选 `6612aea...` | 4465（总收集 4468，3 deselected） | `19 failed, 4445 passed, 1 skipped, 3 deselected` |

候选新增的 20 项为天梯专项测试，均通过。两份工作树的 `.pytest_cache/v/cache/lastfailed` 均为 19 项；按测试节点名排序后做双向 `comm` 比对：

- 候选独有失败：0
- 基线独有失败：0
- 相对基线新增失败：0

共同失败节点如下，均未由 P1-F 新增：

```text
tests/test_cninfo_disclosure_metadata.py::test_dav623_same_query_response_captured_without_duplicate_query
tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[False]
tests/test_dav27_report_semantics.py::test_ordinary_and_streaming_paths_merge_gaps_and_keep_graph_fallback[True]
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_dual_horizon_persists_debate_states_to_result_data
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_intent_hoist_persists_debate_states_to_result_data
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_hold_decision_persists_note_and_metrics_consistency
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_job_execution_with_legitimate_empty_probability_persists_note_and_4_of_4_metrics
tests/test_debate_state_persistence.py::TestJobExecutionDebatePersistence::test_single_horizon_persists_debate_states_to_result_data
tests/test_h1b_gates.py::TestH1bV2OnlySampleFilteringAndIndustry::test_verify_h1b_gates_script_runs_and_verifies_v2_only
tests/test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param
tests/test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json
tests/test_signal_processing.py::test_negated_build_position_is_not_buy
tests/test_signal_processing.py::test_negated_buy_phrase_is_not_buy
tests/test_signal_processing.py::test_plain_hold_is_hold
tests/test_social_data_api.py::test_status_proves_four_dimensions_independent
tests/test_two_stage_analyst_topology.py::test_full_7_analysts_two_stage_topology
tests/test_two_stage_analyst_topology.py::test_mixed_subset_topology
tests/test_two_stage_analyst_topology.py::test_phase1_only_topology
tests/test_two_stage_analyst_topology.py::test_phase2_only_topology
```

## 合入与发布边界

合入前远端 `origin/codex/dav-4-p2a-trunk` 精确为设计基线 `d816a8c...`，候选是其后代；在干净 detached 集成树执行 `git merge --ff-only 6612aea...`，无合并提交，随后推送成功。合入后远端回读为：

```text
6612aea82e0fb3212d3682c5d09835f529ffec16  refs/heads/codex/dav-4-p2a-trunk
```

本证据只覆盖代码审查、全量回归和线性合入，不覆盖部署。当前 8000 服务只读回读仍为：

- PID `19944`，工作目录 `/private/tmp/ta-release-p1e-0263496-20260914`
- `/healthz` 完整 SHA `026349614a3f1b92a95dc06c0515f10ebec193bc`
- 生产 SQLite `quick_check=ok`，reports `1409 / 793 / 616`

P1-F 设计明确不授权本次部署；未启动真实分析、未写生产报告、未采集真实社交数据、未启用信用加权。后续上线必须单独执行备份、预启动、切换、healthz、只读业务烟测和数据库回读。
