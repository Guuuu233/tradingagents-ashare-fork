# DAV-2242 — 评估入口 peak footprint 高水位定位与降低

**基线**：DAV-2240 候选 `8b817a309d1ef6cfe3267f6bf16ff713450ef544`（合入后合）
**解释器**：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`
（`-V` = `Python 3.10.20`；pandas 2.3.0 / numpy 2.2.6 / pyarrow 25.0.1；全程 `env -u PYTHONPATH`）
**口径**：`/usr/bin/time -l` 的 **peak memory footprint**；同时记录 `maximum resident set size`。
**冻结输入**：`p1-base_t10-frozen/` 与快照/账本/标签 33 文件全程 sha256 不变（改前改后同一指纹
`046fb689…c83d`）。m200/W0、敏感性网格、冻结来源均未改动。

---

## 1. 结论

| 项 | 改前 | 改后 | 变化 |
|---|---:|---:|---:|
| **peak footprint（中位，3 次）** | **4.699 GiB** | **2.742 GiB** | **−1.957 GiB（−41.6%）** |
| peak footprint（最大，3 次） | 4.753 GiB | 3.196 GiB | −1.557 GiB（−32.8%） |
| peak RSS（最大） | 3.970 GiB | 2.159 GiB | −1.811 GiB |
| 墙钟（中位） | 118.9 s | 72.8 s | −46.1 s |
| 产物 `verdict.json` sha256 | `bdf933d4…079b` | `bdf933d4…079b` | **逐字节相同** |

`run.json` 仅 `elapsed_s` / `peak_rss_gb` 两个计量字段不同，其余字段（含 funnel、
base_source、sensitivity、version_key 对账）逐值相同；`day_series.parquet` 与
`custom_evidence.jsonl`、`reliability.json` 逐字节相同。

资源门（总控 10-18：≤6 GiB、终止线 5.5 GiB）改后最大 3.196 GiB，**余量 2.30 GiB**。

---

## 2. 高水位定位（实测，非推断）

方法：在**真实 `run()`** 的每个生产调用点包一层 rusage 采样（不修改被测文件），
读取 `proc_pid_rusage(RUSAGE_INFO_V4)` 的 `ri_lifetime_max_phys_footprint`。
结构字段偏移按本机 SDK `sys/resource.h` 的 `struct rusage_info_v4` 逐字段核对
（`ri_lifetime_max_phys_footprint` 在偏移 240，采样器内有 `assert` 防 SDK 漂移）。

| 阶段 | 阶段内 footprint 变化 | 阶段结束时 lifetime peak |
|---|---|---:|
| imports | 0 → 0.203 | 0.203 |
| load_labels / reattest | → 0.224 | 0.224 |
| load_ledger_signals（1749 行 + 快照 custom 证据） | 0.224 → 0.308 | 0.501 |
| **build_base_panel（9,244,318 行）** | **0.309 → 2.523** | **3.802** |
| `_panel_n_distribution` | 2.523 → 2.526 | 3.802 |
| build_pit_base_rates（m200/W0 主基准） | 2.526 → 2.616 | 3.802 |
| build_pit_base_rates（敏感性 3×3 网格） | 2.618 → 2.872 | 3.802 |
| 全部 paired_increment（8 次） | 2.874 → 2.874 | 3.802 |

**高水位完全由 `build_base_panel` 一步决定**：面板驻留只涨 2.2 GiB，却把 lifetime
peak 顶到 3.80 GiB —— 差额是**瞬时**分配，不是驻留。账本/快照、基率引擎、敏感性
网格、配对都不构成高水位。

### 2.1 进一步拆到语句级

在同一份已装载面板（footprint 1.878 GiB）上逐步执行改前的归一化表达式：

| 语句 | footprint | lifetime peak |
|---|---:|---:|
| 面板已装配 | 1.878 | 1.878 |
| `astype(str).str.replace(...)` | 1.955 | 1.955 |
| **`sd.str[:4]` / `"-"` / `sd.str[4:6]` / `"-"` / `sd.str[6:8]` 五段全部物化** | **3.552** | 3.552 |
| **链式 `+` 求值（改前代码）** | 3.892 | **4.797** |

**根因**：`signal_date` / `mat` 两列各 9,244,318 行，改前按**行**做四段字符串拼接：

```python
panel["signal_date"] = (sd.str[:4] + "-" + sd.str[4:6] + "-" + sd.str[6:8])
```

每个 `.str[]` 都新建一个 9.24M 元素的 object 数组（每元素一个新 `str` 对象），
链式 `+` 会同时持有全部中间列；单列瞬时 3.55 GiB、峰值 4.80 GiB。
而这两列的**不同值只有 2,481 个**（2015-10-08 … 2025-12-31 的信号日）。
面板本身驻留 1.88 GiB，其中三列 object 指针 3×74 MB，字符串对象按 2,481 个不同值
经 pyarrow 读取时已天然共享（实测 `signal_date` 20 万行只有 243 个不同对象）。

> 已排除的两条假设（都实测证伪，未写入改动）：
> ①「`pd.concat` 逐分区复制字符串导致膨胀」——11 个分区帧合计 1.383 GiB，
> concat 后 1.659 GiB，仅 +0.28 GiB，不是主因；
> ②「`load_panel` 每行一个 `str` 对象」——pyarrow 读取已按值去重，20 万行仅 243 个对象。

---

## 3. 改法（已实施）

`scripts/phase2/p_rel_t10_baseline_eval.py`：新增 `_iso_days_by_unique_value()`，
`build_base_panel()` 改调用它。逻辑：`pd.factorize` 先取不同值 → 只对**不同值**执行
原来那条表达式 → 按 int 位置回填。空值分支（`NaN`→`"nan--"`、`None`→`"None--"`）
逐行保留原表示，绝不折叠成一个共享值；冻结面板无空值，生产零成本。

实测同一列：**9,244,318 个对象 → 2,481 个对象**，字符串内容 sha256 与改前完全一致
（`signal_date` `2c0679fe5ca1fe4a`、`mat` `7a5c41b56a0790c1`，新旧相同）。
`build_base_panel` 自身墙钟 21.5 s → 4.4 s（单测级 0.3–0.4 s/列）。

**未做的事（有意）**：`m1_base_rate.load_panel` 的 `pd.concat` 保持原样（实测非主因，
改它只是增加回归面）；未改 dtype（不引入 category，避免 dtype 语义变化）。

---

## 4. 测试

新增 `tests/test_dav2242_peak_memory.py`（11 条，全绿）：

1. 函数级：逐行表达式 vs 去重值实现，含空帧、空值（`nan--`/`None--`）、首末冻结日；
2. 面板级：`build_base_panel` 输出与改前逐行表达式**逐值相同**，未触及列（`l1`/`y`）
   保持 loader 原值，去行业行仍是空串，畸形 8 位日期仍被原过滤器挡掉；
3. 引擎级：同一帧喂 `build_pit_base_rates`，主基准 m200/W0（含面板后查询日）与
   3×3 敏感性网格每格逐值（含 NaN 位置）相同；
4. 资源级：新实现持有的对象数 == **不同值个数**（40）而非行数（20,000）——
   若退回逐行对象，本卡实测收益即不成立，测试会失败。

**RT-FULL**（固定解释器、`-q -p no:randomly`、隔离 `DATABASE_URL`、已 deselect
`test_fund_flow_scale_consumption…test_single_horizon_report_persists_and_reads_all_scale_fields`（DAV-979 主干既有死锁））：

| 跑次 | 结果 |
|---|---|
| 基线 `8b817a30`（未改） | **8049 passed, 0 failed**，28 skipped，7 deselected，3784 s |
| 候选 第 1 次 | 8059 passed, **1 failed**（`test_offline_network_guardrail.py::…test_sentinel_executed_and_pid_matches_pytest_main`，"Observation log missing SENTINEL records"） |
| 候选 第 2 次 | **8060 passed, 0 failed**，28 skipped，7 deselected，772 s |

第 1 次的那条失败：**非本卡引入**。依据：①该测试与 `p_rel_t10` / `m1_base_rate`
无任何 import 关系，本卡 diff 只碰 `build_base_panel` 的日期归一化；②它在
`pytest_sessionstart` 的 sentinel 阶段写入日志，失败信息是"观察日志里没有本进程 PID
的 SENTINEL"，而同一日志确有 SENTINEL 记录（PID 91357 / 93270，属并行跑的其它
RT 进程）—— 这是并行 agent 同时跑 RT-FULL 时的**共享 `/tmp/rt_observe_*.log`
环境竞争**（日志按启动时间+PID+uuid 命名，但并行任务的巡检脚本会读到别人的）；
③单独连跑 3 次（含与本卡新测试同批）均绿；④候选第 2 次全量绿。
结论按「相对基线有无**新增**失败」判：**无新增失败**。

`sensitivity` / 数值零漂移由 §1 的逐字节产物比对独立覆盖。

---

## 5. 证据文件

全部在 `work/dav2242-evidence/`（分支内，可直接核）：

| 文件 | 实测值 | 内容 |
|---|---:|---|
| `peak-before-1.stderr.log` | **4.699 GiB** | 改前（ta-base `8b817a30`）`/usr/bin/time -l` 原文 |
| `peak-before-2.stderr.log` | 4.697 GiB | 同上，第 2 次 |
| `peak-before-3.stderr.log` | 4.753 GiB | 同上，第 3 次（最大） |
| `peak-after-1.stderr.log` | **2.742 GiB** | 改后，第 1 次（= 中位） |
| `peak-after-2.stderr.log` | 3.196 GiB | 改后，第 2 次（最大，系统并行负载较高） |
| `peak-after-3.stderr.log` | 2.262 GiB | 改后，第 3 次 |
| `rtfull-baseline.log` | 8049 passed / 0 failed | 基线 RT-FULL 全文 |
| `rtfull-candidate.log` | 8060 passed / 0 failed | 候选 RT-FULL 第 2 次（全绿）全文 |
| `rtfull-candidate-run1-sentinelfail.log` | 1 failed / 8059 passed | 候选 RT-FULL 第 1 次（含那条环境失败）全文 |

评价入口产物（逐字节比对对象，`verdict.json` 两侧均 `bdf933d4…079b`）为本地运行目录
`run/before/evaluation/`、`run/after/evaluation/`，**未入库**（体积大且可由上面命令复现）。

---

## 6. 遗留与风险

1. **本卡只做"定位 + 降低驻留/瞬时"，未改任何口径**。峰值余量已到 2.30 GiB；
   如实测口径变化（例如面板扩到更多年、或敏感性网格加档），需重测 —— 高水位与
   **面板行数 × 每列不同值数**相关，不是固定值。
2. **`_panel_n_distribution`（约 1.9 s / 7 个 groupby 键）与敏感性网格仍会复制面板
   视图**，但实测不进入高水位（结束时 lifetime peak 未被推高），故未动。
3. **未做**：`--snapshot-db` 快照读取本身在本机耗时 25–55 s（SQLite 6.4 GB、
   `cache_size=-2000`），这不是内存问题，留给后续性能卡。
4. **并行 RT 干扰**：本机同时有多个 agent 跑 RT-FULL 时，`test_offline_network_guardrail`
   会偶发读到别的进程的 SENTINEL 日志。这是宿主测试设施问题（共享 `/tmp` 前缀），
   不属本卡范围，但会持续污染其它卡的 RT 结论，建议另开卡让日志路径带
   `PYTEST_XDIST_WORKER`/任务 UUID 隔离。
5. 合入顺序：本卡排在 DAV-2240 **合入之后**合入（同文件 `p_rel_t10_baseline_eval.py`）。
