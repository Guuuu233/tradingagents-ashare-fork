# P4 — daily read-only snapshot & forward ledger (DAV-1480)

Append-only, hash-chained JSONL ledger of sealed judgement records for every
newly-completed report. Lives under `work/phase2-ledger/` (gitignored, D-072).

## Daily run

```bash
python scripts/phase2/daily_snapshot_ledger.py run
```

* DB is opened read-only (`file:…?mode=ro`); no write path is touched.
* Defers (exit 2, logged) while any report is `running`/`pending`/`queued`.
* Appends only reports whose `id` is not yet sealed; safe to re-run.
* Fails (exit 3) when the CN trade calendar can't be loaded — timing classes
  are never approximated with a weekday fallback.
* Fails (exit 4) when the ledger's tail line is unreadable — the run refuses
  to append on top of a corrupt tail because that would silently start a new
  genesis segment detached from history. Fix or restore the ledger first.
* On success it also refreshes the external head anchor (`HEAD` file, below).

## Verify

```bash
python scripts/phase2/daily_snapshot_ledger.py verify
```

Recomputes every `record_sha256` and the `prev_hash → chain_hash` link; any
modified, reordered or inserted line is reported by line number.

**What verify can and cannot prove.** A self-contained hash chain proves only
internal consistency — every line that is still present still links to its
predecessor. It **cannot** detect lines deleted from the *tail* (truncation):
a 50-line ledger cut to 48 still verifies "OK". To cover the tail, `verify`
compares the computed terminal `chain_hash` + line count against an external
**head anchor** at `work/phase2-ledger/HEAD` (small JSON file written after
each successful `run`).

Anchor semantics:

| anchor state | verify result |
|---|---|
| matches ledger tail | `OK: tail matches head anchor` (exit 0) |
| mismatch (hash or line count) | `FAIL: … tail truncation or rewrite detected` (exit 5) |
| corrupt / unparsable | `FAIL: … unreadable/corrupt (fail-close)` (exit 4) |
| absent + non-empty ledger | bootstrapped from current tail once (`bootstrapped: true`), logged, exit 0 |
| absent + empty/missing ledger | `UNVERIFIED: no anchor, tail integrity unverifiable` (exit 2) |

**Bootstrap boundary.** The existing ledger predates anchors, so the first
`verify`/`run` stamps `bootstrapped: true` with that day's tail. Any
truncation that happened *before* the bootstrap moment is undetectable —
the anchor only covers growth after it is first written. Keep `HEAD` and
`forward_ledger.jsonl` in the same backup set; an attacker who can rewrite
both can still defeat the anchor (it detects accidents and partial tamper,
not a fully privileged adversary).

## Baseline reconcile (one-off for the 2026-10-04 cohort snapshot)

```bash
python scripts/phase2/daily_snapshot_ledger.py baseline \
  --snapshot-jsonl ~/Documents/Codex/2026-09-22/ni/claude_review/forward_cohort_snapshot_20261004.jsonl
```

Re-hashes the live `result_data` for each snapshot id. Mismatches are listed
with a hint whether only post-seal measurement fields (DAV-1458 T+5, D-071
dedup) plausibly differ. Result is also written to
`work/phase2-ledger/baseline_check_20261004.json`.

## timing_class (mutually exclusive)

| class | rule |
|---|---|
| F0 | sealed_at < T+1 trading-day open 09:30 (Asia/Shanghai) |
| F1 | entry ≤ sealed_at < T+40 trading-day close 15:00 |
| H  | sealed_at ≥ T+40 close |

`sealed_at = max(created_at, snapshot run time)`. Calendar comes from
`tradingagents.dataflows.trade_calendar.require_cn_trade_dates` (akshare
`tool_trade_date_hist_sina`); if the calendar can't cover T+1 the entry is
known to be future → F0; if it can't cover T+40 the outcome is not yet
knowable → F1. When `signal_date` is *earlier* than the first calendar date
the T+1 entry cannot be resolved at all, which is also classified F0
(conservative).

**Backfill caveat — read before comparing H records.** Every record carries
`sealed_lag_days` (sealed_at − created_at, days) and `backfilled` (true when
lag > 2 days). The initial 1576-line population was sealed in one backfill
batch, so its ~742 `H` rows mostly have `backfilled: true` with multi-week
lags — they are *not* comparable to `H` rows produced by future genuinely
forward-looking runs (where sealing ≥ T+40 close happens naturally). When
aggregating `timing_class`, split on `backfilled` or restrict to
`backfilled == false`. The field exists only on records written after this
revision; older lines simply lack it (`verify` tolerates either shape).

## input_pit_status (orthogonal)

| status | rule |
|---|---|
| FAILED | any source has `actual_as_of > requested_as_of`, or provenance marks it `future` |
| VERIFIED | every input_snapshot source is provenance-`verified` (or has a readable actual_as_of ≤ cutoff), and no FAILED |
| UNVERIFIED | anything else — missing `market_data_context`/`source_provenance`, sources with no readable timestamp, or expected sources absent from provenance |

Structural/context keys that never carry a timestamp (`vpa_*`, `event_coverage`,
`cninfo_*`, `price_basis`, `fund_flow_consensus_guard`, …) are exempt — see
`_NO_TIMESTAMP_SOURCES` in the script. `requested/actual as_of` values are
compared as parsed timestamps, not raw strings, so both `YYYY-MM-DD` and ISO
datetimes are handled.

**Existing-data caveat.** On the backfilled ledger this dimension is 1576/1576
`UNVERIFIED` — the stored `result_data` of historical rows predates
`source_provenance`/`market_data_context` coverage, so there is no
discrimination signal. Do **not** read `UNVERIFIED` on old rows as a data
quality finding; only rows sealed after the pipeline writes provenance can
ever reach `VERIFIED`/`FAILED`.

## versions — known gap in existing rows

`versions.run_identity`/`served_models`/`input_snapshot`, `generated_by_commit_sha`
and `protocol_version` are null on most of the 1576 backfilled rows because
the historical `result_data` never stored them — a pre-existing data gap,
not a ledger defect. Rows sealed after this revision auto-fill them: trunk
`api/main.py` already writes `input_snapshot`/`run_identity`/`served_models`
into every `result_data` save path (`api/main.py:2667`, `:2707`, `:2746`).

## launchd

`com.davidliu.ta-p4-ledger.plist` is a template (per controller revision #2 it
is *not* installed by this change). Mon–Fri 19:45, after the 16:37 measurement
batch. Logs land in `work/phase2-ledger/launchd.{out,err}.log`; a non-zero exit
is visible there and in `snapshot_ledger.log`.

**Known gaps before install:**

* **No active failure-alerting channel.** A non-zero exit is only visible in
  the log files — nothing pushes a notification. If "failure alerts" are
  required, an explicit notifier (e.g. a wrapper that mails/messages on
  non-zero exit, or a watchdog on `snapshot_ledger.log`) still has to be
  added; until then launchd failure is passive-only.
* **Path coupling.** `ProgramArguments` points at the trunk checkout
  (`/Users/davidliu/Documents/TradingAgents-AShare`). Installing the plist
  before this change is merged to trunk means every weekday run fails on a
  missing script — install only after merge, per the plist header comment.

---

# M1 评估脚本（DAV-1678）

`m1_eval.py` — 中线概率契约 `b2.v1` / `p_rel_t40` 的日级评价层。按 DAV-1573
总控签收口径（2026-10-07，九项细则）实现；先用构造数据验证，账本到期样本
后再接真数据。

```bash
# 构造数据自测（交付前必跑）
python scripts/phase2/m1_eval.py --selftest

# 真实信号帧 → 日序列 + 校准 + 结论分档
python scripts/phase2/m1_eval.py --input signals.parquet \
    [--pairs repeat_pairs.csv] [--out-dir data/phase2/m1_eval]
```

## 输入契约（每行一条候选信号，主运行去重在内部完成）

| 列 | 说明 |
|---|---|
| `signal_date` | 信号交易日（YYYYMMDD/ISO；横截面分组键） |
| `version_key` | 版本队列键（代码SHA+提示词哈希+实际服务模型+契约版本）；缺省 `default` |
| `symbol` | 股票代码（与日期共同作去重单元） |
| `run_id` + `created_at` | 主运行去重：同股同日同版本取 `created_at` 最早的已完成运行（签收④，事前固定） |
| `timing_class` | 正式队列只收 `F0` |
| `input_pit_status` | 只收 `VERIFIED`；`FAILED` 一律不进 |
| `p_rel_t40`/`prob`/`q` | 整数 1–99（或 0–1），列级归一到 q∈[0,1] |
| `r_rel`/`rel_return`/`r` | T+1 开盘→T+40 收盘的股票−申万一级指数收益（小数） |

不满足正式队列规则的行按原因逐项计入 `funnel` 报告（签收 §2.3：排除前后
分母都要报），不静默丢弃。

## 实现口径（对照 DAV-1573 九项签收）

| 签收项 | 落实 |
|---|---|
| ①日级 Spearman IC 平均秩、并列等权 | `spearman_ic` 平均秩；`eval_days` 按日输出 |
| ③等权汇总、缺日不压缩、秩退化单列 | `grade` 用含 NaN 槽位的日序列入 NW；`insufficient`/`rank_degenerate` 单列不记 0 |
| ③五分位边界并列按 §3.2 分数权重 | `quintile_weights` 严格按 `w=min(max(h−L,0),E)/E` |
| ④主运行去重 | `admit` 取同股同日同版本 `created_at` 最早的完成运行 |
| ⑤NW60 主 / NW120 稳健、95% 双侧 | `newey_west_ci`（缺口不压缩） |
| ⑥C 档门槛 | `grade`：`valid_days>=60` 且 `nw60_lo>0` 且 `nw120_lo>0`；否则 A/B |
| ⑦可靠性固定 10 箱 [1–10]…[91–99] | `reliability_table`；`RELIABILITY_BINS` 结果前冻结 |
| 重复测量 C 与 A | `repeat_measures`（`--pairs` 输入 q1,q2 配对帧） |
| ⑨结论强度 A–D | `grade`；D 档需 M2 配对+成本证据，本脚本不自动授予 |

## 产物（`--out-dir`）

- `m1_day_series.parquet` — 每日 `date,n_t,status,ic,quintile_diff,quintile_tied_n,brier`
- `m1_reliability.json` — 10 箱 `bin,n,n_days,pred_mean,empirical`
- `m1_verdict.json` — 分档结论 + NW60/120 区间 + C 档门槛明细 + funnel
- `run.json` — 耗时、峰值内存（`/usr/bin/time -l` 口径由外部采样）、行数
