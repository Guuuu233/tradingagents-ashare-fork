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
    [--version-key v1] [--calendar trade_days.txt] \
    [--pairs repeat_pairs.csv] [--out-dir data/phase2/m1_eval]
```

- `--version-key`：主日序列是**单一版本队列**（签收④ + DAV-1682 🔴）。输入帧含
  多个 `version_key` 时必须显式指定其一，否则 **fail-close 报错**；跨版本比较
  只走配对通道（M3），绝不混入主 IC 序列。
- `--calendar`：交易日历文件（一行一个 YYYYMMDD；可选首行带 `date` 表头，仓库测试即按此写法）。给出后，输入 min..max 跨段内
  每个日历日都建槽——无信号日记 `no_data` NaN 槽，NW lag 不跨越缺口（签收③ +
  DAV-1682 🟡-1）；不给则以观测到的信号日为槽（输入须覆盖全部应计日期）。
  **输入帧带 `sealed_at` 列时为必填**：封存时点门要靠它解析 T+1，缺日历直接
  fail-close 报错，不做工作日回退近似（DAV-1999 复审 🟢 S-2）。
- **封存时点门（DAV-1991，总控 10-13 令）**：输入帧带 `sealed_at` 列时，正式
  队列另加一门——封存时间必须**严格早于**信号日次一交易日 09:30（Asia/Shanghai），
  晚封存/补录记录只进漏斗计数 `excl_late_seal`，不进正式队列。时区口径：
  `sealed_at` 为**无时区标记的 UTC**（末行 2026-10-12 11:45:05=北京 19:45），
  按 UTC 解析后转 Asia/Shanghai 比较，绝不当本地时间。
  该门需要 `--calendar` 解析 T+1，缺日历时 **fail-close 报错**（与账本
  `daily_snapshot_ledger.py` 同一纪律：不做工作日回退近似）；无 `sealed_at` 列的
  帧早于该门，`funnel.seal_gate_applied=false` 记明。
  解析不到 T+1 的两种情形一律**排除出正式队列**，单列 `excl_seal_t1_unresolved`
  （DAV-2007 复审 M-2 / 总控 10-13 令返修）：日历末端在 T+1 之前结束，**以及信号
  日早于日历首日**（DAV-2004 修 DAV-1999 复审 🟡 M-1：与账本 `_t_plus_n` 的
  `idx<0` 同案——不得回退成日历首日，否则会拿错的交易日推出一个真实 deadline）。
  判排除而非放行的理由：`_classify_timing` 的 F0 是**账本的分类口径**（账本看不到
  日历时就标 F0），不是正式成绩的准入口径——T+1 开盘时刻解析不出来的记录，无法
  证明它在开盘前封存，收入正式队列即前视泄漏（如 7 月底信号在 10-04 补录封存、
  日历又不覆盖 7 月的那批）。
  `sealed_at` 的取值形态与账本 `_parse_dt` 对齐（DAV-1999 复审 🟢 S-3）：只认以
  `YYYY-MM-DD` 开头的值（无时区 UTC 串，或带 T/偏移/Z 的 ISO），`"20260106"`、
  `"01/06/2026"` 这类形态一律记 `seal_unparseable` 一并排除，不被 `format="mixed"`
  猜成 UTC 午夜 instant。

## 输入契约（每行一条候选信号，主运行去重在内部完成）

| 列 | 说明 |
|---|---|
| `signal_date` | 信号交易日（YYYYMMDD/ISO；横截面分组键） |
| `version_key` | 版本队列键（代码SHA+提示词哈希+实际服务模型+契约版本）；缺省 `default` |
| `symbol` | 股票代码（与日期共同作去重单元） |
| `run_id` + `created_at` | 主运行去重：同股同日同版本取 `created_at` 最早的已完成运行（签收④，事前固定） |
| `timing_class` | 正式队列只收 `F0` |
| `input_pit_status` | 只收 `VERIFIED`；`FAILED` 一律不进 |
| `sealed_at` | 封存时点门（DAV-1991）：只收**严格早于**信号日次一交易日 09:30
（Asia/Shanghai）的记录；晚封存/补录记 `excl_late_seal` 只进漏斗。口径：
`sealed_at`/`seal_basis_at`/`created_at` 均为**无时区标记的 UTC**（末行
"2026-10-12 11:45:05"=北京 19:45），按 UTC 解析→转 Asia/Shanghai→与 T+1 09:30
比较，绝不当本地时间；生产账本 1651 行 `sealed_at` 与 `seal_basis_at` 同值，
二者等价，取 `sealed_at`。形态白名单见上（非 `YYYY-MM-DD` 开头的值记
`seal_unparseable`） |
| `p_rel_t40`/`prob`/`q` | 整数 1–99（或 0–1），列级归一到 q∈[0,1] |
| `r_rel`/`rel_return`/`r` | T+1 开盘→T+40 收盘的股票−申万一级指数收益（小数） |

不满足正式队列规则的行按原因逐项计入 `funnel` 报告（签收 §2.3：排除前后
分母都要报），不静默丢弃。

封存时点门（DAV-1991）新增三个诊断计数：`excl_late_seal`（晚于 T+1 开盘封存，
含补录，被门排除）、`seal_unparseable`（封存时间无法解析——无法证明前瞻封存，
一并计入 `excl_late_seal` 排除）、`excl_seal_t1_unresolved`（日历解析不到 T+1——
日历末端早于 T+1，或信号日早于日历首日（DAV-2004 / DAV-1999 🟡 M-1），或
`signal_date` 本身不可解析——**一律排除**，DAV-2007 复审 M-2 返修；此前按账本
`_classify_timing` 同案按 F0 收入，属放行缺陷）。门是否生效见 `seal_gate_applied`。
四个计数在 `m1_verdict.json → verdict.funnel` 落盘，其中 `seal_gate_applied` /
`seal_unparseable` / `excl_seal_t1_unresolved` / `excl_late_seal` 同时写进 `run.json`
（DAV-2004 / DAV-1999 🟢 S-1：`run.json` 是运维第一眼看的产物，门生效但 T+1 或
封存串大面积解析不到这类静默降级不能只藏在 funnel 里）。

## 实现口径（对照 DAV-1573 九项签收）

| 签收项 | 落实 |
|---|---|
| ①日级 Spearman IC 平均秩、并列等权 | `spearman_ic` 平均秩；`eval_days` 按日输出 |
| ③等权汇总、缺日不压缩、秩退化单列 | `grade` 用含 NaN 槽位的日序列入 NW；`insufficient`/`rank_degenerate` 单列不记 0 |
| ③五分位边界并列按 §3.2 分数权重 | `quintile_weights` 严格按 `w=min(max(h−L,0),E)/E` |
| ④主运行去重 | `admit` 取同股同日同版本 `created_at` 最早的完成运行 |
| 封存时点门（DAV-1991，总控 10-13 令） | `admit` 由 `sealed_at` 重新推导时序规则
（不信 `timing_class` 标签）：`sealed_at <` T+1 交易日 09:30 Asia/Shanghai 才进
正式队列，晚封存/补录记 `excl_late_seal` |
| 封存时点门 T+1/串解析（DAV-2004，DAV-1999 复审返修） | `_next_trading_day` 在
`bisect_right==0`（信号日早于日历首日）返 `None`，与账本 `_t_plus_n` 的 `idx<0`
同案，不回退成日历首日；`_seal_deadline_cn` 先把
`signal_date` 归一为 YYYYMMDD（公开入口 `admit` 可能收到 datetime 型，`astype(str)`
会让整批错落日历首日）；`sealed_at` 过 `_parse_dt` 同形白名单，非 `YYYY-MM-DD`
开头的值记 `seal_unparseable` |
| 封存时点门 T+1 不可解析排除（DAV-2007 复审 M-2 / 总控 10-13 令返修） | `entry`
为 NaT 的行（日历末端早于 T+1 / 信号日早于日历首日 / `signal_date` 不可解析）**一律
排除**出正式队列并计入 `excl_late_seal`，单列 `excl_seal_t1_unresolved`；账本
`_classify_timing` 的 F0 是分类口径、不适用于正式成绩准入 |
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
  （`input_rows`/`formal_rows`）+ 封存时点门四个计数（`seal_gate_applied` /
  `seal_unparseable` / `seal_t1_unresolved` / `excl_late_seal`）

## 配对实验执行器（DAV-2325 / DAV-2330）

`experiment_executor.py run` 的 `--concurrent-arms` 与
`--allow-nontrading-day` 均默认 **False**，必须显式传入才启用。

- 默认串行：`POST:O → POLL:O → RESULT:O → POST:N → …`。
  `--concurrent-arms` 只用于已授权的并发实验窗口：先提交所有臂，再按臂顺序
  收尾；服务端独立执行 job。提交相近不保证实际开跑重叠。
- 默认非交易日不跑；`--allow-nontrading-day` 只用于总控明确授权的休市执行
  窗口（DAV-2325 为 2026-10-24 周六窗口）。**一次开关 = 一次授权窗口**。
  `daily_batch_gate_open()` 在非交易日恒为 False，`seen_base_days` 只在本次
  进程内生效；同日另起进程并换 pair 名单仍能再跑一批，执行器不提供跨进程
  的「每日一批」授权控制。落盘记录仅供审计，不自动授权后续窗口。

非交易日放行立即写入 `state.json.nontrading_day_override`，包含 `date`
（Asia/Shanghai）、`flag`（`--allow-nontrading-day`）和 `reason`。该独立字段
续跑时保留，不借用 `stop_reason` / `stop_detail`，不触发停批。`reason` 只来自
`--nontrading-day-reason`，不传则为 `null`（未记录理由）：把某次授权文案写死，
下一个授权窗口就会落成「新日期 + 旧理由」的自相矛盾证据。读回时只认 dict
且只保留这三个已知键，外部手改成其它 JSON 一律当没有留痕。

该字段是**单槽**：换授权窗口就被覆盖，只表达「最近一次放行」。因此同一事件还会
向 `ledger.jsonl` **追加**一条独立事件行（只增不改，历史窗口靠它保留）：

```json
{"type": "event", "event": "nontrading_day_override", "recorded_at": "…",
 "date": "2026-10-31", "flag": "--allow-nontrading-day", "reason": null}
```

解析 `ledger.jsonl` 时按 `type == "event"` 跳过事件行（其余行是尝试记录）。
本次运行的授权同时写进交付产物 `run_report.json.nontrading_day_override`：
取值来自**本次进程**的放行，未发生则为 `null`——`state.json` 里上一窗口的残留
单槽不会被当成本次授权。

每臂 `elapsed_seconds` 是**客户端收尾轮询墙钟**：从该臂开始轮询到拿到终态，
不含提交、结果读取或账户补读，**不等于 job 运行时长**。并发口径下第二臂先
等待前臂收尾，该等待发生在自身轮询计时前，**不计入该数值**；第二臂读到已
完成 job 时可为 0。每臂按每次成功状态回读原样记录服务端 `created_at` /
`started_at` / `finished_at`；缺失值为 null，不用客户端时刻补造。这三个键在
**失败路径**（`submit_failed` / `status_failed` / `unknown_instance`）上也存在，
值为 null 而不是整个键缺席，下游按 `run["started_at"]` 取时刻不会 KeyError。
归属回读三字段 `report_account` / `report_account_source` /
`report_account_http_status` 同样始终存在（未发生时分别为 null / null / null）。
`ledger.totals["elapsed_seconds"]` 累加的是 `run_pair` 的 pair 级墙钟，因此并发
口径下约等于两臂中的较大值，而不是两臂之和。两臂「同窗」
以 `started_at` 到 `finished_at` 的运行区间重叠为判据（先统一时区再比较：
`max(started_at) < min(finished_at)`），缺任一必要时刻不能认定同窗。
`--job-timeout` 仍按每臂收尾分别计时，并发两臂可能累计等待约 2 倍 timeout；
没有另设 pair 级 deadline。

`/result` 未回显账户时，补读本臂实例 `GET /v1/reports/{job_id}`。这是
**owner-scoped 可读性回读**：返回的 `user_id` 恒等于令牌账户，别人的报告行
表现为 404。取不到或与 `--expect-account` 不符仍 fail-closed；`/result`
已回显错账户时不补读覆盖。审计字段 `report_account_source` 只在取得有效账户
时为 `result_endpoint` / `reports_endpoint`，否则为 null；补读的 HTTP 状态另记
`report_account_http_status`（传输异常为 0）。补读计入 `http_requests`，不计入
`analysis_requests` / 分析请求预算；即便未设置 `--expect-account`，仍补读留痕。
