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
* Fails (exit 4) when the ledger's tail line is unreadable — checked
  **before** the DB is even opened, so a corrupt tail never costs a
  full-table SELECT or a calendar network round-trip. The run refuses
  to append on top of a corrupt tail because that would silently start a new
  genesis segment detached from history. Fix or restore the ledger first.
* On success it also refreshes the external head anchor (`HEAD` file, below).

## Verify

```bash
python scripts/phase2/daily_snapshot_ledger.py verify
```

Recomputes every `record_sha256` and the `prev_hash → chain_hash` link; any
modified, reordered or inserted line is reported by line number.

Exit codes:

| exit | meaning |
|---|---|
| 0 | chain intact AND tail matches head anchor |
| 1 | in-chain verification failed (non-anchor): a `prev_hash`/`record_sha256`/`chain_hash` mismatch — see the per-line report |
| 2 | ledger missing/empty, or head anchor absent — see the anchor table below |
| 4 | head anchor corrupt/unparsable (fail-close) |
| 5 | head anchor mismatch — tail truncation or rewrite detected |

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
| absent + non-empty ledger | `UNVERIFIED: head anchor missing` (exit 2) — **verify never anchors implicitly**; stamp it once with `bootstrap` (below) |
| absent + empty/missing ledger | `UNVERIFIED` (exit 2) |

**Bootstrap boundary.** The existing ledger predates anchors, so stamp its
first anchor once, by hand, via `bootstrap` — it is written with
`bootstrapped: true` and that day's tail. Any truncation that happened
*before* the bootstrap moment is undetectable — the anchor only covers
growth after it is first written. Keep `HEAD` and `forward_ledger.jsonl`
in the same backup set; an attacker who can rewrite both can still defeat
the anchor (it detects accidents and partial tamper, not a fully
privileged adversary).

## Bootstrap (one-time ops action)

```bash
python scripts/phase2/daily_snapshot_ledger.py bootstrap [--force]
```

Anchoring is a deliberate ops action, not a `verify` side effect — that way
a *deleted* anchor surfaces as `UNVERIFIED` (exit 2) instead of being
silently re-stamped. `bootstrap` re-verifies the chain first and refuses on
a broken chain (exit 1), an empty/missing ledger (exit 2), or an existing
anchor (exit 1, pass `--force` to deliberately re-anchor). Re-anchoring
discards evidence of a possible tail mismatch, so it is explicit too.

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
