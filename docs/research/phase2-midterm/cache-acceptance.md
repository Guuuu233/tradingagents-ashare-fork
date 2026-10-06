# Phase2 缓存覆盖验收报告（DAV-1546）

> 只读验收：不重抓、不覆盖、不删除任何缓存文件。核对脚本
> `scripts/phase2/cache_acceptance.py` 一条命令可复跑全部数字；
> 明细 checkpoint `work/dav-1546-cache-acceptance.json`（不提交，
> 内含逐文件校验记录，支持中断续跑）。

- 核对对象：`~/Documents/TradingAgents-AShare-cache/phase2/`（5.7 GB）
- 核对窗口：2015-10-01 — 2025-12-31（SSE 交易日）
- 解释器：`.venv310` Python 3.10.20；`env -u PYTHONPATH`；pandas 2.3.0 / numpy 2.2.6
- 实测：单进程峰值 RSS 3.405 GB（`/usr/bin/time -l` maximum resident set size）；
  全程 27 s（manifest 0.7 s、adj_factor 8.0 s、consolidated_all 17.5 s，其余项 < 1 s）

## 1. manifest.json 一致性（全量 22 756 条）

核对方法：逐条比对 `files` 键 → `api_cache/<file>` 存在性、字节数、必备字段；
对确定样本复算 `sha256`（分块读取，单文件不占满内存）；按 api 分层抽样
重载 pkl 核对 `rows`。

| api | manifest 条数 | manifest rows 合计 | fetched_at 区间 |
|---|---:|---:|---|
| trade_cal | 3 | 18 946 | 10-05 06:48:20 → 19:06:44 |
| stock_basic | 3 | 5 911 | 06:48:32 → 06:48:34 |
| index_classify | 1 | 31 | 06:48:35 |
| namechange | 1 | 34 755 | 06:48:44 |
| sw_daily | 31 | 77 221 | 06:48:46 → 06:49:31 |
| index_weight | 2 | 120 614 | 06:49:54 → 06:50:12 |
| daily | 2 491 | 10 371 853 | 06:50:13 → 08:08:03 |
| daily_basic | 2 491 | 10 284 102 | 08:08:06 → 09:39:03 |
| index_member_all | 11 822 | 7 915 | 09:39:04 → 15:33:35 |
| adj_factor | 5 911 | 19 095 730 | 15:35:10 → 18:51:30 |

- 文件存在性 / 零字节 / 必备字段：**0 缺失、0 零字节、0 缺字段**（22 756/22 756）。
- `sha256` 抽查：5 个大文件（daily_all 702 M、daily_basic_all 618 M、
  adj_factor_all 565 M、sw_daily、index_weight 000300/000905 中的大者）
  + 5 个小文件，全部一致（`sha256_bad` 为空）。
- `rows` 抽查：每 api 抽 25 条重载 pkl 比对行数，共 135 条，全部一致。
- `fetched_at` 均在 2026-10-05 同一抓取窗口内，时序合理（daily →
  daily_basic → index_member_all → adj_factor 顺序推进）。

## 2. 覆盖范围核对

### 2.1 交易日历

`api_cache` 中三份 trade_cal（SSE）：

| 文件参数 | 行数 | 区间 | 窗口内开市日 |
|---|---:|---|---:|
| 20151001–20251231 | 3 745 | 2015-10-08 → 2025-12-31 | 2 491 |
| 20190603–20251231 | 2 404 | 2019-06-03 → 2025-12-31 | — |
| 19900101–20251231 | 12 797 | 1990-12-19 → 2025-12-31 | — |

窗口内开市日 **2 491 天**，首日 2015-10-08（10-01—10-07 为国庆假期，
非缺失）。以下按日核对均以该 2 491 日为基准。

### 2.2 daily / daily_basic 按日分块

| 检查 | daily_by_day | daily_basic_by_day |
|---|---:|---:|
| 磁盘文件数 | 2 491 | 2 491 |
| 对日历缺日 | 0 | 0 |
| 对日历多日 | 0 | 0 |
| 首日 / 末日 | 2015-10-08 / 2025-12-31 | 2015-10-08 / 2025-12-31 |
| 分块行数 vs manifest（抽样 30 天/表）| 0 不符 | 0 不符 |
| `*_all.pkl` 行数 = 分块合计 | 10 371 853 ✓ | 10 284 102 ✓ |
| `*_all.pkl` 覆盖日数 / 股票数 | 2 491 / 5 704 | 2 491 / 5 704 |

daily_basic 每天行数略少于 daily（60 天抽样差 0–98 行/天，均值 ≈32）：
daily_basic 不含停牌/无估值数据个股的正常差异，非缺日。

### 2.3 sw_daily（申万一级行业）

- `index_classify` L1 共 **31** 个行业码，`sw_daily.pkl` 覆盖 **31/31**，
  每个码恰 2 491 行（窗口内全部交易日），合计 77 221 行。
- 31 个 manifest 条目的 `rows` 与 `api_cache`、合并文件逐一相符。

### 2.4 adj_factor（复权因子）

- `adj_factor/` 目录 **5 911** 个文件，与 manifest `adj_factor` 条目、
  stock_basic 股票集合一一对应。
- `adj_factor_all.pkl` 19 095 730 行 = 目录分块行数合计 ✓；覆盖 5 910 个
  symbol（唯一例外：`T600018.SH` 上港集箱(退)，2006-10-20 已退市，目录
  文件为 0 行空表，manifest 亦记 `rows=0`——属真实无数据，非缺漏）。
- 单股区间抽查：覆盖各自上市首日至 2026-09-30（如 000001.SZ
  1991-04-03 → 2026-09-30），合理。

### 2.5 index_member_all（行业归属）

- `index_member_all/` 目录 5 911 个文件 = stock_basic 全集；manifest
  Y/N 两批各 5 911 条，无缺失。
- `index_member_all_all.pkl` 7 915 行、5 909 个 symbol；`is_new`
  Y=5 909 / N=2 006；Y 批 `rows=0` 的两只（000765.SZ、T600018.SH）均为
  窗口前退市股，目录中为 0 行空表，一致。
- **已知局限 DAV-1446 仍存在**：`index_member_all_all.pkl` 无 `con_code`
  列，Y/N 两批的 `out_date` 全部为空（N 批 2 006 行 `out_date` 全空、
  Y 批亦全空），即无法从该表恢复「退出历史归属的时间区间」。当时归属
  （PIT）在 DAV-1453 裁定前只能取 `is_new=Y` 的静态现属，**前视偏差
  方向：把历史上属于其他行业的样本错误归入其 2026-10-05 时点的行业**
  ——对中途换行业的 2 006 个 N 记录所涉股票，历史 T+40 行业基准收益
  会用其"未来"行业指数。该偏差不影响板块覆盖完整性，但影响历史
  行业相对收益的 PIT 正确性，须登记为研究局限。

### 2.6 index_weight（指数权重）

| 指数 | 行数 | 快照日数 | 区间 |
|---|---:|---:|---|
| 000300.SH | 59 100 | 197 | 2015-10-30 → 2025-12-31 |
| 000905.SH | 61 514 | 123 | 2015-10-30 → 2025-12-31 |

000905.SH 恰为 123 个月末快照（2015-10 — 2025-12 逐月齐全）；
000300.SH 在月末快照外含额外调仓日快照（2020-01 起每月 2–3 个），
覆盖同样窗口。两指数均满足月度成分回溯需求。

### 2.7 stock_basic / namechange

- `stock_basic.pkl` 5 911 行：L=5 572、D=339、P=0，与 manifest 三批
  一致；含 BSE（北交所）353 只，研究中需按 V-03a 剔除。
- 窗口内 daily 实际交易 5 704 只；3 只（000022.SZ、000043.SZ、
  300114.SZ）在窗口内有行情但不在 stock_basic——均系更名/借壳致
  `ts_code` 复用（招商港口/深赤湾、招商积余/中航善达、中航成飞/中航
  电测），属 Tushare `stock_basic` 只回当前/退市挂牌口径的正常现象；
  相应历史可通过 `namechange.pkl`（34 755 行、6 122 个 symbol、
  区间 1990-12-01 → 2026-09-30）衔接。

## 3. 缺口清单

按「阻塞 P1 后续研究」vs「可容忍局限」区分：

| # | 项 | 判定 | 说明 |
|---|---|---|---|
| 1 | `index_member_all_all.pkl` 无 `con_code`、N 批 `out_date` 全空（DAV-1446）| **阻塞 P1** | 无法用该表还原 PIT 行业归属；DAV-1453 未裁定前，P1 只能以现属 Y 批近似，前视偏差方向见 §2.5。补抓建议：`index_member_all` 需按 `is_new=N` 取 `in_date/out_date` 非空的历史区间（若上游仍返空，改用 `index_member` 按月成分 + `index_classify` 逐月重建），量级 ≈5 911 只 × 1–2 次调用。|
| 2 | `T600018.SH` `adj_factor` 0 行、`index_member_all` Y 批 0 行 | 可容忍 | 2006 年退市股，窗口内无交易日；P1 股票池（上市满 60 交易日且窗口内可交易）本就不含它。 |
| 3 | `000765.SZ` `index_member_all` Y 批 0 行 | 可容忍 | 2005 年退市，同 #2。 |
| 4 | 000022.SZ / 000043.SZ / 300114.SZ 不在 `stock_basic` | 可容忍 | 更名/借壳复用 ts_code；可由 `namechange` 衔接，P1 股票池规则已按 `ts_code`+可交易日过滤。 |
| 5 | `daily_basic` 每日行数 < `daily` | 可容忍 | 停牌/新股当日无估值的正常差异，非缺日。 |

未发现缺文件、缺日期、缺行业、行数对不上的硬缺口（除 #1 的结构性
字段缺失外，manifest—文件—合并表三层均一致）。

## 4. 验收结论

**缓存满足 P1 分块构建与 T+40 研究的最低需求**：交易日历、按日分块
daily/daily_basic 连续无缺（2 491/2 491 日），sw_daily 31 行业齐全，
adj_factor 与 index_member_all 覆盖全部 5 911 只标的并与 manifest
一致，指数权重月度快照齐全。唯一结构性缺陷是 #1（DAV-1446 遗留）：
行业归属表缺 `out_date`/无 `con_code`，PIT 归属不可还原——这不阻塞
分块构建本身，但 P1 报告的行业相对收益必须在"现属近似 + 前视偏差
方向"的局限声明下产出，直至 DAV-1453 裁定与补抓完成。

## 5. 复跑方式

```bash
cd <repo>
env -u PYTHONPATH .venv310/bin/python \
    scripts/phase2/cache_acceptance.py \
    --cache ~/Documents/TradingAgents-AShare-cache/phase2 \
    --details work/dav-1546-cache-acceptance.json
# 跳过三个 500M+ 合并大文件的全量载入（仍校验目录级行数）：
#   ... --skip-big-concat
```
