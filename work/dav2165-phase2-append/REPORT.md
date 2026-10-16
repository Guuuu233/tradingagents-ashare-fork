# DAV-2165｜phase2 缓存只追加补到 2026-10-16（执行报告）

**结论：补齐完成，只追加。** 冻结表内 DAV-2142 §5 登记的 P1 manifest
（`out/base_t10/stats.json`）及其 11 个 2015–2025 分区文件**全部字节未变**；
新增内容只有一个新分区 `year=2026`（信号日 20260105–20261016）另加一份新版
manifest `manifest_v2_dav2165.json`。

## 1. 执行环境

| 项 | 值 |
|---|---|
| 解释器 | `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，`Python 3.10.20`（`env -u PYTHONPATH`） |
| 代码树 | `codex/dav-4-p2a-trunk` tip `cd319ef6ed2d6762e33da961757c3c91011b1d3b`，独立 worktree `~/ta-work/dav-2165/tree`，`git_dirty=false` |
| 构建脚本 | `scripts/phase2/build_phase2_chunks.py`，`script_sha256=c05073fc67d86f4a4800d2d8455de54a890d9eb8ede8f87ba719921ec6439a9d` |
| 校验脚本 | `scripts/phase2/validate_phase2_chunks.py`（同树） |
| 数据源 | Tushare 私有网关（`TUSHARE_API_URL`，只读查询），经项目内 `_query_tushare_api` 出口 |
| 生产库 | **未触碰**：全程无 SQLite 连接、无 `DATABASE_URL`、无模型/provider 调用、无服务重启 |
| peak | `ru_maxrss` 2.409 GB（`/usr/bin/time -l` maximum RSS 2,564,227,072 B；peak memory footprint 2,326,890,920 B = 2.17 GiB）≤ 6 GiB |

## 2. 只追加策略（为什么既有分区不可能被动到）

冻结的 P1 面板在**另一个 worktree 的磁盘目录**里
`…/dav-2080-fd1fed5c2507/workdir/out/base_t10/`。本轮按二选一中的**「另出新版
manifest」**执行：

1. 新分区只写到新目录 `~/ta-work/dav-2165/out_v2/chunks/year=2026/`，构建命令
   `--years 2026`，物理上不可能覆盖 2015–2025 任何分区。
2. 读取侧输入换成**叠加缓存**（overlay）`~/ta-work/dav-2165/overlay_cache/`：
   2015-10-08–2025-12-31 的 `daily_by_day` / `daily_basic_by_day` / `adj_factor` /
   `index_member_all` **全部是指向冻结缓存的符号链接**（313 个未涉及的 adj_factor
   文件同样是链接），只有 2026 新数据是真文件。
3. `build_provenance.git_sha=cd319ef6`、`git_dirty=false`。

**冻结区字节校验（构建前后各一次，`diff` 空）**：

| 校验对象 | 文件数 | 结果 |
|---|---:|---|
| P1 冻结面板（`base_t10` 下全部 parquet + stat.json） | 22 | 前后 sha256 清单完全一致 |
| P1 manifest `out/base_t10/stats.json` | 1 | `15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696`，**与 DAV-2142 §5 冻结表登记值一致** |
| 冻结 phase2 缓存 `~/Documents/TradingAgents-AShare-cache/phase2` 全量 | 39,571 | 汇总清单 sha256 `151734831573d907fb30e1bad2677d588243971875fb660473962d57a8e7b302`，运行前后一致（该缓存**只读挂载，从未写入**） |

## 3. 补了什么（Tushare 增量抓取）

188 个新交易日 = 20260105 … 20261016（SSE 开市日）。

| 数据 | 抓取量 | 落点（overlay 相对路径） |
|---|---:|---|
| `trade_cal` SSE 20151001–20261231 | 4,110 行，**在 20261016 截断** | `api_cache/trade_cal_00_dav2165.pkl` |
| `daily` 按日 | 188 天 × ~5,500 行 | `daily_by_day/2026-*.pkl`（新增） |
| `daily_basic` 按日 | 188 天 | `daily_basic_by_day/2026-*.pkl`（新增） |
| `adj_factor` 按日 → 按股合并 | 188 天 → 5,603 个 per-symbol pkl 重写 | `adj_factor/*.pkl`（其余 313 个符号链接） |
| `sw_daily` 31 个 SW2021 L1 | 77,221 → 83,049 行 | `sw_daily.pkl` |
| `index_weight` 000300/000905 月末快照 | 9 个月末 × 2 指数 | `index_weight_000300.SH.pkl` / `000905.SH.pkl` |
| `stock_basic` L/D 刷新 | 5,911 → 5,916 行，`max list_date=20261016` | `stock_basic.pkl` |
| `namechange` 2026 增量 | 新增 max `start_date=20261015` | `namechange.pkl` |
| `index_member_all` | 无增量源 → 镜像冻结缓存 | 符号链接（未改） |

网关 19 次 read-timeout，全部走同一 `api()` 的指数退避重试后成功，**无一次
fail-closed 触发**；逐调用记账见 `fetch_journal.jsonl`（620 行）。
`namechange` 与 `stock_basic` 是在冻结文件**之上合并**（先 drop-duplicates
keep=last 再 concat），不修改冻结源。

**日历截断是刻意的**：信号日 T+40 超过最后一个 bar 日时必须落 `pending_due`，
而不是因为出场窗掉出已加载帧而误判 `data_missing`（build 脚本对未截断年份的
同一处理靠 `fwd_end_idx = min(...)` 夹逼，这里在日历侧先收口，两条路径同解）。

## 4. 新分区结果

`out_v2/chunks/year=2026/data.parquet`，32 列 schema 与 DAV-1547 冻结口径完全一致
（vendor_qfq 主口径 + `r_stock_raw` 敏感性列 + 同窗 SW L1 T+1 开盘腿）。

| 项 | 值 |
|---|---|
| rows | 1,035,505 |
| symbols | 5,597 |
| signal_date_min / max | 20260105 / 20261016 |
| evaluated_ok | 811,595 |
| pending_due | 222,249（尾部 T+40 未到期） |
| data_missing | 1,265 |
| unexecutable_entry | 336 |
| suspension | 60 |
| y_rel_nonnull | 811,407 |
| universe_ok_rows | 911,107 |
| y_rel 正例占比（evaluated_ok 内） | 0.44625 |
| 重复 (ts_code, signal_date) | 0 |

## 5. 哈希与耗时

| 文件 | sha256 |
|---|---|
| `manifest_v2_dav2165.json` | `00754b9ab0e137b47a7de1bb8e7db0bbf199026a9919d30cf16f533d11be1ed0` |
| `out_v2/stats.json` | `e4c2cbe90d028dcc151541c6356e5bcb237393d7ac82ea6a94838699c31ff730` |
| `out_v2/chunks/year=2026/data.parquet`（62.4 MiB） | `97db09675179767fc1ebcd5147d4ff509a9c40029d965d300cec779da421312f` |
| `out_v2/validation_report.json` | `995c14eec4cd55d2a100e6ebea5c997c028f1d7af372b9b61d0014ea319daa0d` |
| 原 P1 manifest（回读） | `15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696` = 冻结表值 ✅ |
| 冻结缓存全量清单（39,571 文件） | `151734831573d907fb30e1bad2677d588243971875fb660473962d57a8e7b302`（前后一致） |
| `fetch_append_cache.py` | `a3395cffce20b3b8f38324528b4b04c0b4abde08a06e16b628474a1ad2b12504` |

新版 manifest 含 12 个分区（2015–2025 引用冻结件并逐个带 parquet/stat sha256 +
`origin: frozen_dav2080_p1_manifest_unchanged`；2026 带 `origin: dav2165_appended_2026-01-05..2026-10-16`），`total_rows=11,407,358`。

| 阶段 | 耗时 | peak |
|---|---:|---:|
| Tushare 增量抓取（376 次查询 + 19 次重试） | 51 min（20:41:45 → 21:33:00） | — |
| 构建 year=2026 | **30.8 s** | 2.409 GB |
| 校验（4,000 抽样） | ~35 s | <1 GB |

## 6. 验证证据

1. **独立朴素实现复核**：`validate_phase2_chunks.py --sample-per-partition 4000`
   → `chunks/year=2026/data.parquet: sampled=4000 checked=3133 mismatches=0`，
   `total mismatches: 0`（复核 `outcome_status/entry/exit/r_stock(qfq)/r_stock_raw/r_sw/y_rel`）。
2. **确定性复跑**：独立 out-dir `--force` 复跑一次，产出
   `data.parquet` sha256 与首跑**完全相同**（`97db0967…`），29.41 s / 2.39 GB。
3. **单测**：`tests/test_dav1547_qfq_label.py` 2 passed（qfq 除权不落地 + 指数
   T+1 开盘缺失不回落收盘口径）。**未跑全量 RT-FULL**：本卡不改产品代码，
   只新增 work/ 证据文件与数据产物，改动不触达任何被测模块。
4. **冻结区零改动**：见 §2 两张 hash 清单的 `diff` 为空。

## 7. 遗留与边界

- 2026 分区 21.5% 行是 `pending_due`（T+40 未到期），与 2025 分区同类尾部
  形态一致；**追加不是重算**，2025 及以前的成熟标签未受任何影响。
- 本轮只补 phase2 研究缓存，**未**触碰 DAV-2142 冻结表的任何口径字段；新版
  manifest 是 §5「未来变更必须记偏差及新 manifest 哈希」条款要求下的新增登记，
  是否替换 P1 来源需总控裁定后执行（本轮不擅自替换）。
- `index_member_all` 无 2026 增量源（`index_member_all` 接口按指数查得
  `out_date` 仍为空串在属），本轮按镜像处理，与 DAV-1546 §6.2 记载的
  「DAV-1446 查询写法问题」一致，不构成本轮阻塞。
- 产物落盘在本地 worktree/out 目录（未入 git，大文件不入库）；哈希与日志
  已随 `work/dav2165-phase2-append/` 提交。

## 8. 证据文件

`work/dav2165-phase2-append/`：`manifest_v2_dav2165.json`、`stats_v2_year2026.json`、
`stat_year2026.json`、`validation_report.json`、`fetch_append_cache.py`、
`fetch_journal.jsonl`、`fetch.log`、`build.log`、`base_cache_hashes_after.txt`、
`frozen_p1_hashes.txt`。