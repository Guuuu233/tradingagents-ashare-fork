# Phase-2 P1 子卡二：按年分块 parquet 构建报告（DAV-1547）

> 只含汇总统计与口径说明，不含行情原始数据行（D-040）。全部数字可由
> `scripts/phase2/build_phase2_chunks.py`（构建）与
> `scripts/phase2/validate_phase2_chunks.py`（校验）复跑。

## 1. 产出

- `data/phase2/chunks/year=YYYY/data.parquet` × 11 个分区（2015–2025），zstd 压缩，
  总 577MB，合计 **10,371,853 行**，覆盖 **5,704** 只股票、信号区间
  **2015-10-08 ~ 2025-12-31**（= 缓存交易日历 2,491 个开市日全集）。
- `data/phase2/chunks/year=YYYY/stat.json`：每分区行数/股票数/状态计数/峰值 RSS。
- `data/phase2/stats.json`：累计清单（断点续跑依据）。
- 分区粒度：全部年份按年单分区即可满足峰值 ≤6GB 约束（实测峰值 3.03GB，
  `/usr/bin/time -l` 4.37GB peak footprint），未触发按月细分；`--months` 与
  `--max-rows-per-partition`（默认 150 万）仍是现成开关。
- 总耗时 **287s**（≈4.8min，单进程，.venv310 Python 3.10.20 + pandas 2.3.0 +
  pyarrow 25.0.1）。

## 2. 样本行定义与标签口径

每行 = 一只池内股票 × 一个信号日（该股票当日有 bar 即视为潜在信号）。
标签镜像 `tradingagents/dataflows/return_labels.py` BUY 腿（P2 未合入，脚本内实现）：

- 入场：信号日次日（T+1）开盘价；T+1 无 bar 且双侧证据（信号日 close>0 且 T+2
  有 bar 且 close>0 且 vol>0）→ `unexecutable_entry`；无双侧证据 → `data_missing`；
  有 bar 但 vol=0 / open≤0 / 一字涨停（open≥limit_up 且 high=low=limit_up）→
  `unexecutable_entry`。
- 出场：目标日 T+40 收盘；当日不可交易则在至多 5 个顺延候选中取首个可交易收盘
  （vol>0、close>0、非一字跌停）；全部不可交易时看窗口后一日是否有 bar，有 →
  `suspension`，无 → `data_missing`。
- `r_stock = exit_close/entry_open − 1`（**未复权价格**，拆股在比值中抵消，现金分红
  未计入——见局限）；`r_sw = SW一级指数 close(signal) → close(actual_exit)`；
  `r_rel = r_stock − r_sw`；`y_rel = 1[r_rel>0]`。
- 涨跌停参考价 = `pre_close × (1 ± rate)`；rate：ST 期间 5%，主板 10%，创业板/科创板
  20%，北交所 30%（北交所行已被股票池剔除，rate 仅作兜底）。

## 3. 分区统计

| year | rows | symbols | evaluated_ok | unexecutable | suspension | data_missing | pending_due | 耗时量级 |
|---|---|---|---|---|---|---|---|---|
| 2015 | 150,732 | 2,784 | 143,221 | 248 | 167 | 7,096 | 0 | ~18s |
| 2016 | 652,864 | 3,160 | 623,177 | ~1k | ~0.2k | ~28k | 0 | ~20s |
| 2017 | 754,372 | 3,620 | 726,432 | — | — | — | 0 | ~25s |
| 2018 | 824,535 | 3,727 | 809,996 | — | — | — | 0 | ~24s |
| 2019 | 894,177 | 3,926 | 889,254 | — | — | — | 0 | ~24s |
| 2020 | 964,131 | 4,364 | 955,724 | — | — | — | 0 | ~28s |
| 2021 | 1,085,445 | 4,840 | 1,079,862 | — | — | — | 0 | ~29s |
| 2022 | 1,179,072 | 5,182 | 1,172,589 | — | — | — | 0 | ~31s |
| 2023 | 1,258,734 | 5,381 | 1,256,238 | — | — | — | 0 | ~33s |
| 2024 | 1,293,893 | 5,433 | 1,290,727 | — | — | — | 0 | ~31s |
| 2025 | 1,313,898 | 5,500 | 1,093,920 | — | — | — | 217,695 | ~30s |

（逐分区精确计数见 `data/phase2/stats.json` 与各 `stat.json`；2016 起每年 bar 加载量
≈当年信号日 + 前向 46 个日历日出場窗 + 50 日动量回看。）

总体状态分布：evaluated_ok 10,041,140（96.8%）、pending_due 217,695（2.1%，2025 年
尾部信号窗口未成熟）、data_missing 99,944（1.0%）、unexecutable_entry 10,201（0.10%）、
suspension 2,873（0.03%）。

## 4. 落盘 schema（列清单）

`ts_code, signal_date, year, month, in_hs300, in_zz500, sw_l1_code, sw_l1_name,
is_st, list_age_days, universe_ok, entry_date, target_exit_date, actual_exit_date,
roll_days_used, entry_open, exit_close, r_stock, r_sw, r_rel, y_rel,
outcome_status, pe_ttm, pb, total_mv, turnover_rate, mom_40, vol_40, ret_1d,
adj_factor_entry, adj_factor_exit`（31 列；`data/phase2/chunks` 在 .gitignore 的
`data/` 规则内，不入库）。

- `universe_ok` = 非北交所 & 信号日非 ST/*ST（PIT，namechange 区间）&
  list_age_days ≥ 60；全样本中 90.9% 为 True。
- `mom_40` = close/close.shift(40) − 1、`vol_40` = 日收益 40 日滚动 std、
  `ret_1d` = 日收益，均按 `groupby(ts_code)` 在 (ts_code, trade_date) 排序帧上
  滚动，未建“日期×全市场”宽表。
- `in_hs300`/`in_zz500` = 月末权重快照前向填充（最新 snapshot ≤ 信号日）。

## 5. 复杂度与内存分析

- 区间标记：每只股票把其 bar 日期向量与全局日历做 `searchsorted` 对齐，entry/T+2/
  出场候选均为 O(1) 索引 + 每行至多 6 次有界探查；复杂度 O(Σ_s n_s)，n_s 为该股票
  bar 数。未构造 信号×窗口 展开长表 merge（等效做法会产生 ~10.4M×46 行的中间表）。
- 内存：分块内 `daily` 帧 ≈ 当年信号日 + 回看 + 前向出場窗（~340 日×~5.5k 行 ≈
  170–190 万行×8 列 ≈ 250MB），行业 PIT 广播表 5,911×2,491 object ≈ 百 MB 级，
  均在年块结束后释放。实测 `ru_maxrss` 峰值 3.03GB，`/usr/bin/time -l` peak
  footprint 4.37GB，满足 ≤6GB 硬约束。
- 校验：`validate_phase2_chunks.py` 抽样 200 行/分区 ×11（2,170 行非 pending 样本），
  用独立逐行朴素实现复核 `outcome_status/entry/exit/r_stock/r_sw/r_rel/y_rel`，
  **0 mismatches**；同时核对 stats.json 行数与 parquet 实读行数一致。

## 6. 缺失与局限登记

1. **行业归属缺口**：1,544 只股票在部分/全部信号日无申万一级归属（index_member_all
   无区间命中），对应 evaluated_ok 行中 `r_sw` 缺失 153,885 行（1.5%），集中于
   2015–2022（早期 SW2021 分类未回溯覆盖的股票/退市股）。偏差方向：缺失非随机，
   早期年份与退市股占比偏高 → 分年/分存在期基率需注明。`y_rel` 在这些行记 NaN，
   不静默丢弃。
2. **PIT 归属实现**：采用 D-069 口径 A 的离线等价（`in_date ≤ D < out_date`，
   `index_member_all/*.pkl` 含 is_new=Y/N 两批合并）；DAV-1446（网关 con_code 字段名）
   已验收为查询写法问题、本卡用缓存文件不受影响；DAV-1453 若未来改裁定需重建归属列。
3. **现金分红未计入 r_stock**：未复权价比值会低估含权窗口的真实收益（分红率量级
   ~年化 1–3%，T+40 窗口约 0.2–0.5%），`y_rel` 在 |r_rel| 接近 0 的行可能翻转；
   `adj_factor_entry/exit` 已落盘，下游可改用复权口径重算（entry/exit 间 adj_factor
   变化即含分红再投资的全收益近似）。bias 方向：系统性略压低 r_stock。
4. **list_age_days 左端点**：list_date 早于日历起点（2015-10-08）时用
   `busday_count(list_date, 2015-10-08)` 近似前置交易日数，节假日使其略有高估 →
   ≥60 日过滤对窗口起点前 ~3 个月上市的股票偏松（只影响 2015Q4 边际样本）。
5. **停牌判定**：tushare 日线无 bar = 停牌/未上市；双侧证据区分 suspension 与
   data_missing，与 return_labels.py 一致。个别退市末日附近 data_missing 为真实缺口。
6. **每日指标缺失**：pe_ttm 缺失 18.7%（2015 早期 daily_basic 覆盖不全）、pb 1.5%、
   mom_40/vol_40 2.5%（窗口起点无 40 日回看）、adj_factor_exit 3.2%（pending/无出场）。
7. **B 股/优先股**：未单列剔除，universe_ok 已限 A 股普通口径（.BJ/北交所/ST/次新）。
8. 缓存缺口段：子卡一（DAV-1546）验收报告在写本卡时尚未落到本工作区；本卡按
   manifest 与实读文件自检：2,491 个开市日 daily/daily_basic 全日覆盖、sw_daily
   31 个一级行业齐全、adj_factor 5,911 股齐全（抽样 0 缺失），未发现阻塞性缺口；
   若子卡一后续登记额外缺口，按卡面如实跳过。

## 7. 复跑

```bash
env -u PYTHONPATH .venv310/bin/python scripts/phase2/build_phase2_chunks.py \
    --out-dir data/phase2            # 全量；已存在分区自动跳过
env -u PYTHONPATH .venv310/bin/python scripts/phase2/build_phase2_chunks.py \
    --years 2024 --force             # 重建指定年
env -u PYTHONPATH .venv310/bin/python scripts/phase2/validate_phase2_chunks.py \
    --out-dir data/phase2            # 抽样复核 + stats 一致性
```
