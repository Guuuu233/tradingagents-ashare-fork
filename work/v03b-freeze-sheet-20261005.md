# V-03b 收益对照测量 · 冻结规格升版（2026-10-05，DAV-1479 / P2）

> 本文件是 `work/v03-freeze-sheet-20260909.md`（V-03a）的升版。V-03a 冻结项
> 逐条保留，本节只写**有变化的差异**。按旧口径算出的历史结论一律标记
> 「须重算后引用」。

## 差异一：结算真相源统一（唯一事实源）

- **裁定**（计划 v1.0 §4 P2 / D-072）：`return_labels.py` 成为唯一结算真相
  源，V-03 只消费、不再自算日期。
- **"T+N" 定义**：T+1 开盘入场，**信号日起第 N 个交易日**收盘退出
  （中线 N=40，短线 N=10）。
- V-03a 旧口径 `exit = get_t_plus_n_date(entry, hold_days)`（即 T+1+N）
  已退役。

## 差异二：参数改名与映射

- `hold_days` → **`eval_offset_from_signal`**。
- 映射：`eval_offset_from_signal = hold_days + 1`（旧 hold_days=5 == 新
  offset=6，T+1 open→T+6 close 窗口数值等价）。
- `--hold-days` 命令行参数保留一个版本并告警（DeprecationWarning +
  stderr 提示）；引擎构造参数 `hold_days` 为已废弃 shim，同上映射并告警。
- 新增 `--horizon-key`：`short`(T+10/roll≤2)、`medium`(T+40/roll≤5)、
  `legacy`（旧 6 日窗口，仅供 V-03a 对照，roll 沿用 short=2）。

## 差异三：三种口径分开存放

| 口径 | 入场 | 退出 | 说明 |
|---|---|---|---|
| **预测** (`prediction_*`) | T+1 开盘 | `target_calendar_date` 收盘，**不顺延** | 目标日停牌→类型化状态（`target_suspended`），不取价、不顺延 |
| **执行**（默认字段） | T+1 开盘 | `actual_exit_date` 收盘，**含停牌顺延**（short≤2/medium≤5) | 套用 V-03a 冻结成本模型 |
| **研究** (`research_*`) | T 收盘 | `target_calendar_date` 收盘 | 学术口径，`research_` 前缀，**不得与前两者混用** |

- 沪深300 超额保留为**次要字段**（`benchmark_*` / `excess_return`，执行
  窗口同口径）。

## 差异四：申万一级行业基准腿（主相对基准）

- 归属：`tradingagents/dataflows/sw_industry.py::resolve_sw_l1_benchmark`
  （`in_date <= T < out_date`，Y/N 两批都查），DAV-1453 口径 A。
- 行情：Tushare **`sw_daily`**（申万一级指数日线，D-072；**禁止**用
  `index_daily`，官方明确不含申万行情）。
- 实现：`tradingagents/dataflows/sw_benchmark.py`。
- 每样本**预测、执行两个窗口各算一次**：指数入场侧取当日 `open`、出场侧
  取当日 `close`（与股票口径一致）。
- **`y_rel = 1[R_stock − R_SW > 0]`**（预测窗口，R_stock 为毛收益）；
  同时记录 R_stock、R_SW 与窗口两端日期。
- 凭据仅从 `.env` 经 `_query_tushare_api` 注入；不落盘、不打印。行情
  原始行只进进程内缓存，不提交（D-040）。

## 差异五：样本标记

- `cohort_tag`：`created_at − trade_date > 2` 自然日 → `backfill`（D-067
  回填 cohort 单独标记），否则 `live`；时间戳不可解析 fail-closed→`live`。

## 差异六：状态机映射（label → V-03b 输出）

| `OutcomeStatus` | V-03b `outcome_status` / reason |
|---|---|
| `evaluated_ok` | `evaluated` |
| `unexecutable_entry` | `untradable`（reason: suspended / limit_up_locked / limit_down_locked） |
| `pending_due` | `typed_missing`(`pending_due`) |
| `suspension` | `typed_missing`(`exit_suspension_window_exhausted`) |
| `data_missing` | `typed_missing`（细化为 entry_bar_missing / exit_bar_missing / label_data_missing） |
| `provider_failure` | `typed_missing`(`provider_failure`) |
| `unsupported_price_basis` | `typed_missing`(`unsupported_price_basis`) |

- `signal_date ∉ trading_days`（回填伪交易日）→ `typed_missing`
  (`calendar_unavailable`)，诚实暴露，不静默。
- 日历覆盖不足 `T+N+max_roll` → `typed_missing`
  (`calendar_insufficient_coverage`)。

## 新旧对照（同一冻结输入）

- `V03ReturnMeasureEngine.measure_sample_v03a_legacy` 为冻结的 V-03a 单窗
  口径副本（仅供对照，不参与产出）。
- `emit_v03a_v03b_comparison` / CLI `--output-comparison` 产出逐样本
  差异清单（落 `work/`）。
- **实测（1013 份中线样本,eval_offset=40)**:legacy(对齐 39 日）与新口径
  锚定窗口**零差异**(`exit_date_convention`=0);158 条 `new_typed_status`
  差异 = 150 non_actionable 决策分类 + 7 池过滤覆盖差 + 1 回填伪交易日
  `calendar_unavailable`（数据质量暴露）。

## 不变项（重申）

只读副本、成本模型、股票池过滤、OOS 三段、类型化缺失（NULL/分母守恒/
禁 carry-forward/禁静默 drop)、25 字段审计表、回归标的隔离、元数据盖章
——全部原样保留。
