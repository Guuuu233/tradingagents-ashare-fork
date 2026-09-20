## DAV-1039 发布前聚合全量回归报告：df753841 RT-FULL-OFFLINE

### 环境快照（门禁口径）

- 候选 SHA：`df7538413ba7bb55593b1757feaf90b0bd514d1c`（干净隔离 worktree `/tmp/ta-df753841`，`git worktree add --detach`，未用主仓脏工作树）
- 解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -V` → **Python 3.10.20**（锁定解释器，非系统 python3）
- `git diff --check`（worktree HEAD）：干净，exit=0
- 代理：`unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY`；`env | grep -i proxy` 无输出（快照已核实为空）
- `DATABASE_URL=sqlite:////var/folders/8k/kg3t37vn3_b1jxs_gdbwpk0c0000gn/T/tmp.ZN65Al0DrL/rt.db`（mktemp 隔离临时库，未写 `data/tradingagents.db`）
- 命令：`pytest tests -q -p no:randomly --deselect tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields`
- deselect 声明：该用例为 DAV-979 主干既有死锁（%CPU=0 挂死），非本候选引入，按 AGENTS 铁律单条 `--deselect`；汇总行 "6 deselected" = 本条 1 + `addopts = "-m 'not network'"`（pyproject.toml:74）标记 deselect 5 条，口径与基线一致。

### 结果

**4 failed, 4921 passed, 1 skipped, 6 deselected, 183 warnings, 3 subtests passed in 369.47s (0:06:09)**

失败清单（逐项）：

1. `tests/test_game_theory_integration.py::test_rt10_real_graph_builder_routing_reachability_and_execution`
2. `tests/test_h1b_gates.py::TestH1bV2OnlySampleFilteringAndIndustry::test_verify_h1b_gates_script_runs_and_verifies_v2_only`（断言 `excluded["legacy_null"] == 3` 实测 0 —— 依赖 golden sample/DB 内容，环境性失败）
3. `tests/test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param`（`cn_akshare.get_cninfo_announcement_content` 缺 date 参数白名单）
4. `tests/test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json`（`sample_count` 断言 60 实测 2 —— 数据窗口依赖）

### 与 e295b58 候选基线逐项比对

基线（DAV-1033 开发者报告 + DAV-1034 复审确认）：`8 failed / 4910 passed` —— game_theory RT10、h1b_gates、provider_date_guards、recalculate_weekly_metrics、topology×4。

| 失败项 | e295b58 | df753841 | 结论 |
|---|---|---|---|
| game_theory RT10 | FAILED | FAILED | 既有失败保留 |
| h1b_gates | FAILED | FAILED | 既有失败保留 |
| provider_date_guards | FAILED | FAILED | 既有失败保留 |
| recalculate_weekly_metrics | FAILED | FAILED | 既有失败保留 |
| topology ×4 | FAILED | **PASS** | 已消除（DAV-1035 陈旧断言更新随 df753841 合入） |

**新增失败：0 项。消除失败：4 项（topology×4）。净 -4。**

### 结论

df753841 全量回归 **0 新增失败**，失败集合为 e295b58 基线的真子集（4/8 保留，4/8 消除）。本次聚合回归补齐了台账记录的「前六次合入缺一次合规聚合全量回归」证据缺口。

### 边界

只读执行：未改码、未合入、未部署、未重启服务、未写生产库、未触网（代理已清空）。原始日志 `work/df753841-rtfull-agg.log`。
