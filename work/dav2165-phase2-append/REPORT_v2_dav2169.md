# DAV-2169 返修报告｜DAV-2165 phase2 namechange 4 列键重做 2026 分区

## 1. 结论

DAV-2168 复审的 🟡 数据正确性问题**已修复并重出 year=2026 分区**。
修复方式：把 `fetch_append_cache.py` 的 namechange 合并键由 3 列
`(ts_code, name, start_date)` 改为 4 列 `(ts_code, name, start_date, end_date)`，
与 `m2_sampler.build_st_intervals` 对齐；只重新生成了 overlay 里的
`namechange.pkl` 与 year=2026 分区，冻结缓存与 2015–2025 分区零改动。

## 2. 根因（比复审描述更严重）

复审指出「3 列键丢 45% 行」。实测表明影响不止于 2026 增量，**连冻结期的
历史行也被误删**：同 `(ts_code, name, start_date)` 而 `end_date` 不同的历史行
共 29,627 条被 `drop_duplicates(keep="last")` 抹掉。这些历史行正是
`build_phase2_chunks.build_st_intervals` 生成 ST 区间的输入。

| 口径 | namechange rows | build_st_intervals 区间对 | ST 股票-交易日（2015-10-08..2026-10-16, 2679 个交易日） |
|---|---:|---:|---:|
| 冻结缓存（真值） | 34,755 | 6,213 | 868,904 |
| DAV-2165 三列键（缺陷） | 18,966 | 3,093 | 668,235（少 200,669，对称差 200,669） |
| **DAV-2169 四列键（本次）** | **20,795** | **3,828** | **868,904（与冻结对称差 = 0）** |

同时 `m2_sampler.build_st_intervals`（4 列去重）在四列键下与冻结
sym-diff = 0；三列键下少 1,818 个区间对。

## 3. 变更文件清单（白名单内，仅 `work/dav2165-phase2-append/`）

| 文件 | 动作 |
|---|---|
| `fetch_append_cache.py` | 改：`merge_on_key` 调用键 3 列 → 4 列；`merge_on_key` 增加 docstring 说明为何不能省列 |
| `manifest_v2_dav2165.json` | 改：`manifest_version` → `v2.1-dav2169-namechange-4col`，新增 `rework` 段，2026 分区统计与 sha256 刷新 |
| `stat_year2026.json` / `stats_v2_year2026.json` / `validation_report.json` | 改：换成重出后的统计与校验 |
| `build_stdout_v2.txt` / `frozen_p1_hashes_v2.txt` / `base_cache_hashes_v2.txt` / `base_cache_hashes_v2_aggregate.txt` | 新增 |
| `rt_full_stdout.txt` | 新增：RT-FULL 全量回归输出 |
| `fetch_journal.jsonl` | 追加一行 rework 记账 |
| `REPORT.md` / `stat_year2026.json` 等原文件 | 保留为 DAV-2165 首版证据，未删 |

**产品代码（`tradingagents/`、`scripts/`、`tests/`）零改动**，`git diff --stat`
仅落在 `work/dav2165-phase2-append/`。

## 4. 重出 year=2026 分区结果

| 项 | 缺陷版（三列键） | **修复版（四列键）** |
|---|---:|---:|
| rows | 1,035,505 | **1,035,505**（不变） |
| symbols | 5,597 | 5,597 |
| evaluated_ok | 811,595 | **811,598**（+3） |
| unexecutable_entry | 336 | **333**（−3） |
| suspension / data_missing / pending_due | 60 / 1,265 / 222,249 | 60 / 1,265 / 222,249（不变） |
| y_rel_nonnull | 811,407 | **811,410**（+3） |
| universe_ok_rows | 911,107 | **886,905**（−24,202） |
| is_st 行数 | 61,456 | **85,658**（+24,202） |
| is_st 股票数 | 381 | **503**（+122） |
| `data.parquet` sha256 | `97db0967…` | **`528285464c120969f306033d5b88af1d38af51ee836941c5ff6614c85ea75249`** |

逐列对比（32 列，1,035,505 行，按 `(ts_code, signal_date)` 对齐）：
仅 `is_st` / `universe_ok` 变化 24,202 行；`outcome_status` 等价字段变化仅
**3 行 3 只**（`000620.SZ@20260430`、`002219.SZ@20260113`、`600821.SH@20260306`，
均由 3 列键下的假 `unexecutable_entry` 恢复为 `evaluated_ok`）。
另有 **1 行连带变化**（DAV-2170 复审 🟢-1，DAV-2182 补记）：`002217.SZ@20260423` 两侧
`outcome_status` 均为 `evaluated_ok`，但 ST 限幅恢复（5%）后 `actual_exit_date`
由 `20260624` 顺延至 `20260625`、`roll_days_used` 0→1、`exit_close` 2.28→2.17、
`r_rel` −0.6323→−0.7296。逐列实测变化计数（3 列键 vs 4 列键，共 1,035,505 行）：
`is_st` 24,202、`universe_ok` 24,202、`actual_exit_date` 4、`exit_close` 4、
`r_stock`/`r_stock_raw`/`r_sw`/`r_rel` 4、`roll_days_used` 1、`entry_open` 3、
`y_rel` 3、`outcome_status` 3、`adj_factor_exit` 3，其余 22 列 0。
ST 股票-交易日净恢复 24,202。

## 5. 哈希与环境

| 对象 | sha256 |
|---|---|
| `out_v2/chunks/year=2026/data.parquet` | `528285464c120969f306033d5b88af1d38af51ee836941c5ff6614c85ea75249` |
| 独立 `--force` 复跑（`out_v2_rerun/…/data.parquet`） | **同上，逐字节一致** |
| `out_v2/stats.json` | `7bfe11d37ddb2e9a1cf51ef351f5e8e7c65216adea83be0046c2c875543354d1` |
| `out_v2/chunks/year=2026/stat.json` | `47239ddc9bffa71198283d5ce2c5faf98907a19665fe7e45ecb5f7b84f317a5b` |
| `out_v2/validation_report.json` | `af47ccb6b9e23c92b4a5514e721608b3a8c4739d4b24ed949181e491afc4f61e` |
| `manifest_v2_dav2165.json` | `9e14bb7a3cca636d4491687a7df2c1ac2713ddcc199134cffd63c737efc03dbf` |
| 冻结 P1 manifest `out/base_t10/stats.json` 回读 | `15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696` = DAV-2142 冻结表值 ✅ |
| 冻结 phase2 缓存 39,571 文件清单汇总 | `151734831573d907fb30e1bad2677d588243971875fb660473962d57a8e7b302` = DAV-2165 记录值 ✅ |
| overlay `namechange.pkl`（四列键） | `57ff7a79bed3a52eeb01727dd984c50db5e361831a11ca6cc5680d2319e17dd8` |

冻结区校验：`shasum --check` 对 22 个 P1 冻结文件与 39,571 个缓存文件全部
`OK`（exit 0），与 DAV-2165 首版清单逐行 `diff` 为空。

| 项 | 值 |
|---|---|
| 解释器 | `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，`-V` = `Python 3.10.20`，`env -u PYTHONPATH` |
| 代码树 | `~/ta-work/dav-2165/tree` @ `25bf8501`（DAV-2182 已更正为 `ffbb5cbb`，见 §8），构建脚本 sha256 `c05073fc67d86f4a4800d2d8455de54a890d9eb8ede8f87ba719921ec6439a9d`（未改） |
| 构建耗时 / peak | 32.8 s / 1.928 GB（≤6 GiB） |
| 校验 | `PHASE2_CACHE_DIR=~/ta-work/dav-2165/overlay_cache env -u PYTHONPATH .venv310/bin/python scripts/phase2/validate_phase2_chunks.py --out-dir ~/ta-work/dav-2165/out_v2 --sample-per-partition 4000 --seed 2169` → `sampled=4000 checked=3145 mismatches=0`
  （**`PHASE2_CACHE_DIR` 必须显式传 overlay 目录**：校验器 `CACHE_DIR` 默认读冻结缓存，其日历止于 20251231；不传则 3,145 个 `checked` 行全部误判为 `invalid_signal`，即 3,145 个假 mismatch。DAV-2170 复审已实测确认。） |
| 数据源 | Tushare 私有网关只读重查 `namechange(20260101–20261016)` = 363 行，与首跑一致 |
| 生产库 | 未触碰：无 SQLite、无 `DATABASE_URL`（除测试临时库）、无模型/供应商调用、无服务重启 |

## 6. Python 3.10 固定口径测试结果

1. 定向单测：`tests/test_dav1547_qfq_label.py`、`tests/test_m2_sampler.py`、
   `tests/test_trade_execution_rules.py` → **58 passed**。
2. **RT-FULL 全量回归**（`.venv310`，`env -u PYTHONPATH`，`DATABASE_URL` 指向
   `mktemp -d` 临时库，已 `unset http_proxy https_proxy all_proxy`）：
   ```
   pytest -q -p no:randomly      --deselect tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields
   → 7977 passed, 28 skipped, 7 deselected, 273 warnings, 3 subtests passed in 457.42s
   ```
   **0 failed，无新增失败**。该 deselect 是主干既有死锁用例（DAV-979），非本卡
   引入。输出存 `rt_full_stdout.txt`。

## 7. 遗留与边界

- 原 DAV-2165 `REPORT.md` / `manifest` 内记录的旧 2026 分区 sha256 已作废，
  以本文件与 `manifest_version=v2.1-dav2169-namechange-4col` 为准。
- `index_member_all` 无 2026 增量源仍按镜像处理（DAV-2168 判为 🟢 已知边界，
  未在本卡变更）。
- 未触碰冻结 P1 分区与已登记 manifest；2015–2025 分区仍为冻结引用。

## 8. DAV-2182 追加：provenance 更正与可复现核验（DAV-2170 复审 🟢-2 / 🟢-3）

本节为 DAV-2182 追加，不改变任何数据产物。

### 8.1 🟢-2 provenance 更正

`manifest_v2_dav2165.json` 的 `build_provenance.git_sha` 原记为
`cd319ef6`（manifest）与 `25bf8501`（`stats_v2_year2026.json`），两者都是**产出前**的
提交：在这些 SHA 上 `work/dav2165-phase2-append/fetch_append_cache.py` 仍是三列键版本，
按此 provenance 复算得到的是已作废的 `97db0967…`，不是登记的 `52828546…`。

更正版（本卡追加提交）：

- `build_provenance.git_sha` = `rework_git_sha` = **候选提交 `ffbb5cbb`**（真正产出该分区的代码）；
- 新增 `base_git_sha` = 返修前提交（manifest 侧 `cd319ef6`、stats 侧 `25bf8501`），记录其用途；
- 新增 `provenance_note` 说明两个字段各自的含义；
- 新增顶层 `provenance_correction` 段登记 `corrected_by` / 旧值 / 新值 / 原因 /
  `data_files_unchanged=true` / `published_partition_sha256_unchanged=52828546…`。

`stats_v2_year2026.json` 同样更正（`git_sha` → `ffbb5cbb`，新增 `base_git_sha`、
`rework_git_sha`、`provenance_note`、`reproducibility_check_dav2182`）。
除这些字段外，两份文件与 `ffbb5cbb` 版本逐字段一致（已用程序化比对确认 `rest identical: True`）。

### 8.2 🟢-3 复跑命令口径

已把完整校验命令（含必须显式传入的 `PHASE2_CACHE_DIR`）补进 §5 表格，并写明不传时的
假 mismatch 现象与成因。

### 8.3 可复现核验（DAV-2182 §2）

从**全新干净检出的已提交候选 `ffbb5cbb`**（独立 worktree `~/ta-work/dav-2182/tree`）
对已搬运发布的 overlay 缓存
（`~/Documents/TradingAgents-AShare-cache/phase2/v2-20261016/overlay_cache`）
重跑补数，输出到**临时目录** `~/ta-work/dav-2182/repro/out_repro`：

```
PHASE2_CACHE_DIR=<published overlay_cache> env -u PYTHONPATH \
  .venv310/bin/python scripts/phase2/build_phase2_chunks.py \
  --out-dir <tmp>/out_repro --years 2026
→ rows=1,035,505 symbols=5597 ok=811,598，42.4 s / 1.438 GB
```

与已搬运的 `528285464c120969f306033d5b88af1d38af51ee836941c5ff6614c85ea75249` 逐行比对：

| 比对项 | 结果 |
|---|---|
| parquet 文件 sha256 | `528285464c…5249` = `528285464c…5249`，**逐字节一致** |
| 规范化行数据 sha256 | `3e3b8ac802a64c1d637ac7d2139ccfe73a118c618bed5fef5d32a9598d5daa81`，两侧相同 |
| 行数 | 1,035,505 = 1,035,505 |
| 逐列差异 | 32 列全部相同，`per_column_diff = {}` |
| dtype | 完全一致 |

即使忽略文件哈希、只比行数据（parquet 元数据差异不影响），结论同样为**一致**。
仅以下**非数据**元数据不同（预期内）：
`stats.json` 的 `build_started` / `build_finished` / `build_provenance.git_sha` /
`build_provenance.recorded_at` / `elapsed_s` / `peak_rss_gb` / `partitions[0].parquet` /
`partitions[0].peak_rss_gb` / `partitions[0].written_at`；
`stat.json` 的 `parquet`（输出路径）/ `peak_rss_gb` / `written_at`。
所有数据统计量（rows / symbols / 日期范围 / 五类 outcome / y_rel_nonnull /
universe_ok_rows / calendar_days）逐项相同。

证据文件：`repro_dav2182/`（`repro_dav2182.json`、`build_repro.log`、
`rowhash.py`、`fullcmp.py`）。分区数据产物本身未随本卡改动。

### 8.4 作废件清理（DAV-2182 §3）

见 `retired_files_dav2182.json`：3 项作废件在**产品代码、`scripts/`、`tests/`、
`batch_spec`、仓库内任何位置均无引用**（仅出现在历史报告文档的字面记录中），已删除。
`out_v2_rerun/`（同内容复跑件）与 `overlay_cache/`（发布源）**保留未动**。
