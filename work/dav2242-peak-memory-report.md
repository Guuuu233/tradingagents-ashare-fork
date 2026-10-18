# DAV-2242 — 评估入口 peak footprint 高水位定位与降低（重底后 v2）

**总工 10-19 执行令**：DAV-2255 已合入主干 → 本卡在**新主干 `01467c8e` 上重底**，
重跑 RT-FULL 与 peak 复测，交 DAV-2263 复审。
**候选**：`agent/senior-dev-1/dav-2242-peak-mem`。
**代码提交 = `26000bc9`，其直接父 = 新主干 `01467c8e74ed473e1332ff5fd433f5826b0a06d9`**
（`git rev-parse 26000bc9^` 实核）；其后为证据/报告提交，**分支 tip 见交付评论**（tip SHA 会随证据提交变化，故不写死在本文件里）。
**解释器**：`.venv310`，`-V` = `Python 3.10.20`；pandas 2.3.0 / numpy 2.2.6 / pyarrow 25.0.1；全程 `env -u PYTHONPATH`。
**口径**：`/usr/bin/time -l` 的 peak memory footprint。
**冻结输入**：`p1-base_t10-frozen/` + 快照/账本/标签 33 文件 sha256 全程不变（改前改后同一指纹 `046fb689…c83d`）。

---

## 1. 结论（重底后实测）

| 项 | 改前（主干 `01467c8e`） | 改后（重底候选） | 变化 |
|---|---:|---:|---:|
| **peak footprint 中位（3 次）** | **4.456 GiB** | **3.374 GiB** | **−1.082 GiB（−24.3%）** |
| peak footprint 最大 | 4.674 GiB | 3.416 GiB | −1.258 GiB（−26.9%） |
| peak RSS 最大 | 3.321 GiB | 2.123 GiB | −1.198 GiB |
| 墙钟中位 | 77.3 s | 75.3 s | −2.0 s |

资源门（总控 10-18：≤6 GiB、终止线 5.5 GiB）：改后最大 **3.416 GiB**，**余量 2.58 GiB**。

**口径零漂移**：两次入口产物 `verdict.json` 均 `a600b22a…9ec9`，
`day_series.parquet` / `custom_evidence.jsonl` / `reliability.json` 逐字节相同；
`run.json` 仅 `elapsed_s`、`peak_rss_gb` 两字段不同。

### 与重底前（v1，父 `8b817a30`）的比例差异必须说明

v1 报的是 −41.6%（4.699 → 2.742 GiB）。重底后同样改动只拿到 −24.3%，**不是改动失效**，
而是 **DAV-2255 引入了新的高水位来源**，把"地板"抬高了。逐阶段证据见 §2.1：

- 我的改动效果**完全未变**：`build_base_panel` 峰值 4.113 → 2.249 GiB（**−1.86 GiB**，
  与 v1 在旧主干的 3.802 → 2.343 一致）；
- 但 2255 新增的 `base_query_audit` 行级日期解析（`matured_rows["mat"].map(day_key)`，
  9.24M 次 Python 调用）与 W>0 敏感性网格接入查询日后，把整条链的峰值抬到 3.44 GiB，
  使面板改造后**总峰值不再由面板决定**。

## 2. 高水位定位

### 2.1 阶段级（主线 `01467c8e` vs 重底候选，同一 run()、同输入）

方法：在真实 `run()` 的生产调用点包 rusage 采样（不改被测文件），读
`proc_pid_rusage(RUSAGE_INFO_V4).ri_lifetime_max_phys_footprint`；字段偏移按本机 SDK
`sys/resource.h` 的 `struct rusage_info_v4` 逐字段核对（`ri_lifetime_max_phys_footprint`
偏移 240，采样器带 assert 防 SDK 漂移）。

| 阶段 | 主干 `01467c8e` 结束时峰值 | 重底候选 结束时峰值 | 说明 |
|---|---:|---:|---|
| imports + labels + reattest | 0.500 | 0.412 | — |
| **build_base_panel** | **4.113** | **2.249** | ← 本卡改动命中处，−1.86 GiB |
| `_panel_n_distribution` | 4.113 | 2.249 | 不推高 |
| m200/W0 主基准（base_q_map） | 4.113 | 2.837 | 引擎侧固定开销 |
| **base_query_audit（DAV-2255 新增）** | 4.113 | **3.095** | 行级 `map(day_key)`，见 §2.2 |
| 敏感性 3×3 网格（含查询日） | **4.607** | **3.442** | 主峰在这两步 |

即：改前主峰 4.607 出现在敏感性网格；改后主峰 3.442 也出现在敏感性网格 —— **面板不再是主峰**。

### 2.2 DAV-2255 新增的残余热点（如实记录，本卡未改）

`base_query_audit` 里为统一 ISO 口径引入：

```python
matured_rows = panel.loc[with_mat]
mat_keys = matured_rows["mat"].astype(str).map(m1.day_key).to_numpy()
```

`panel.loc[...]` 先整体复制一份 9.24M 行面板，`map(day_key)` 再逐行做 Python 调用
（`day_key` 里是 `str().strip().replace()`）。实测（面板已装配、footprint 2.035 GiB 起）：

| 语句 | 累计 footprint | 峰值 |
|---|---:|---:|
| `mat.astype(str).str.strip()` | 2.043 | 2.222 |
| `+ replace("-","").str.fullmatch(r"\d{8}")` | 2.112 | 2.222 |
| **`panel.loc[with_mat]` + `map(day_key)`** | **2.811** | **3.087** |
| `l1.astype(str).to_numpy()` | 2.811 | 3.087 |

**注意**：`with_mat` 在真实冻结面板上 9,244,318 行全部为 True（无无效 mat），所以
`panel.loc[with_mat]` 是一次**全量复制**，白付一份面板。这一处**属于 DAV-2255 的
已合入代码**，本卡不擅自改它 —— 见 §6 建议。

## 3. 改法（本次重底未改动逻辑，仅重底 + 补证据）

`scripts/phase2/p_rel_t10_baseline_eval.py`：`build_base_panel()` 的日期归一化从
**按行**四段字符串拼接改为 `_iso_days_by_unique_value()`：`pd.factorize` 先取不同值 →
只对 2,481 个不同值执行原表达式 → 按 int 位置回填。

空值分支保留原表示（`NaN`→`"nan--"`、`None`→`"None--"`）—— 两者在旧表达式下渲染不同，
折叠成一个共享值会造成静默漂移；冻结面板无空值，故生产零成本。

重底后在**新主干**上重核：同一列 **9,244,318 个对象 → 2,481 个对象**，
`signal_date` sha256 `2c0679fe5ca1fe4a`、`mat` `7a5c41b56a0790c1`，**新旧完全一致**
（`prof/verify_identity2.py`，对照组为 `01467c8e` 的 `build_base_panel` 原体）。

`build_base_panel` 墙钟 11.1 s → 5.3 s。

## 4. 测试（重底后重跑）

- 定向：`tests/test_dav2242_peak_memory.py`(11) + `tests/test_dav2255_sensitivity_query_days_iso_days.py` +
  `test_m1_base_rate` + `test_dav2240_query_days_base_rate` + `test_dav1883_p_rel_t10_baseline` +
  `test_m1_base_rate_bridge` = **113 passed, 10 skipped**（含 2255 的新用例，确认两卡不冲突）。
- **RT-FULL**（`-q -p no:randomly`、隔离 `DATABASE_URL`、已 deselect DAV-979 既有死锁用例
  `tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields`）：

| 侧 | 结果 |
|---|---|
| 父（主干 `01467c8e`） | **8124 passed / 0 failed**，29 skipped，7 deselected，464.6 s |
| 候选（重底后） | **8135 passed / 0 failed**，29 skipped，7 deselected，484.5 s |

候选 = 父 + 11 条（本卡新增测试）。两侧均 0 失败，**无新增失败**。

## 5. 证据文件（均在 `work/dav2242-evidence/`，分支内可核）

| 文件 | 实测值 |
|---|---:|
| `peak-before-1/2/3.stderr.txt` | 4.674 / 4.456 / 4.424 GiB（主干 `01467c8e`） |
| `peak-after-1/2/3.stderr.txt` | 3.416 / 3.311 / 3.374 GiB（重底候选） |
| `rtfull-baseline.txt` | 8124 passed / 0 failed |
| `rtfull-candidate.txt` | 8135 passed / 0 failed |

评价入口产物（逐字节比对对象）在本地 `run2/before-*/evaluation/`、`run2/after-*/evaluation/`，
未入库（体积大、可由 `run/eval_cmd2.sh` 复现）。

## 6. 遗留与建议

1. **DAV-2255 的 `base_query_audit` 值得单独优化**（本卡不擅改已合入的他卡代码）：
   `with_mat` 在真实面板上是全 True，`panel.loc[with_mat]` 白复制一份 9.24M 行面板；
   `map(day_key)` 逐行 Python 调用。改为「先判有无无效 mat，全 True 则跳过 loc；
   day_key 按不同值（2,481 个）算一次再按 int 位置回填」可省约 0.6～0.8 GiB 峰值，
   且结果逐值相同（已用 `np.unique + return_inverse` 实测 `IDENTICAL: True`）。
   建议另开卡，由 2255 执行人做，避免本卡 diff 越界。
2. 主峰现在落在**敏感性网格**（3.44 GiB）。若要把入口峰值继续压到 3 GiB 以下，
   需要动 `build_pit_base_rates` 的 `out_rows`（每条 cells 一个 dict，3 档 W × 2.48k 日 × 31 行业
   ≈ 230k dict）。本卡不动，留给后续。
3. 峰值与「面板行数 × 每列不同值数」相关，扩年或加档需重测。
4. 快照读取（SQLite 6.4 GB，25–55 s）不进内存高水位。
5. RT 观察日志路径建议加任务 UUID 隔离（并行 RT 会互相污染 sentinel 判定），本卡不改宿主测试设施。
