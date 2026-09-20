# 全量测试失败基线（2026-09-08）

**用途：** 供后续包对照。D-010 要求「全仓有已知失败须对照基线，不夸称全仓通过」，此前无此台账。

## 执行事实

```
工作树：/Users/davidliu/Documents/TradingAgents-AShare-dav744
版本：  d41c6cae90c55f75b239b08ff31709285d110c1e（= trunk 3496280 + E-01，该 commit 纯新增 2 个文件、0 删除）
命令：  env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly
结果：  17 failed, 3214 passed, 1 skipped, 3 deselected, 173 warnings in 478.65s
```

因 `d41c6ca` 是纯新增文件，这 17 项失败**同样存在于 trunk `3496280`**（未在 trunk 独立复跑，此为推断，非实测）。

> 作废记录：首次尝试带 `--timeout=120`，因未装 `pytest-timeout` 直接报 `unrecognized arguments` 退出，**不产生任何测试证据**。上表为去掉该参数后的有效重跑。

## 分组

### 组 1：与当前语义契约不一致（14 项，独立复跑均可复现）

**不得仅凭失败即宣布测试无效**，须由审核员判定是「测试待更新」还是「实现走偏」。

| 文件 | 项数 | 断言 vs 实际 |
|---|---|---|
| `test_signal_processing.py` | 3 | 断言 `HOLD`，实际 `WAIT`（`最终建议：观望为主`） |
| `test_two_stage_analyst_topology.py` | 4 | 断言分析师直连 `Bull Researcher`，实际图中有 `Run Integrity Gate` |
| `test_dav27_report_semantics.py` | 2 | 断言 `BUY`/`HOLD`，实际 `NO_TRADE` |
| `test_debate_state_persistence.py` | 5 | 同上 + `extraction_note` 为 None vs `概率未提供/未提取`；`assert 0 == 4` |

指向 D-009 四元拆分（`WAIT`≠`HOLD`、fail-closed `NO_TRADE`）与 P0-1 拓扑变更（插入 `Run Integrity Gate`）。若判定为「测试待更新」，须逐文件开卡，不得在功能卡里顺手改。

### 组 2：结构护栏失败（1 项，独立可复现）

`test_provider_date_guards.py::test_all_time_sensitive_get_methods_have_date_param`

违规项：`cn_akshare.get_cninfo_announcement_content`。分类已完成，推荐方案 (c)（移除名实不符的别名，护栏不动），影响面实测为零调用方。见 `work/2026-09-08-date-guard-classification-card.md`。**推荐≠已批准，实现须另开卡。**

### 组 3：全量与独立运行结果不一致（2 项）

| 文件 | 全量 | 独立 |
|---|---|---|
| `test_h1b_gates.py::...::test_verify_h1b_gates_script_runs_and_verifies_v2_only` | FAILED | passed |
| `test_recalculate_weekly_metrics.py::TestCliIntegrationAndSubprocess::test_cli_subprocess_format_json` | FAILED | passed |

两项均为 subprocess 用例。

**已证明：** 全量运行与独立运行结果不同（`-p no:randomly`，执行顺序确定）。
**未证明：** 根因。尚未区分 pytest 状态污染、cwd/env 交互、临时文件互相覆盖或 subprocess 继承环境差异。**不得表述为「已确认顺序污染」。** 需单独定位卡。

## 复现命令

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare-dav744
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly \
  tests/test_dav27_report_semantics.py tests/test_debate_state_persistence.py \
  tests/test_signal_processing.py tests/test_two_stage_analyst_topology.py \
  tests/test_provider_date_guards.py
# 预期：15 failed, 56 passed, 1 skipped
```
