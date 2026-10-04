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

## Verify

```bash
python scripts/phase2/daily_snapshot_ledger.py verify
```

Recomputes every `record_sha256` and the `prev_hash → chain_hash` link.
Any modified, reordered, inserted or deleted line is reported by line number.

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
knowable → F1.

## input_pit_status (orthogonal)

| status | rule |
|---|---|
| FAILED | any source has `actual_as_of > requested_as_of`, or provenance marks it `future` |
| VERIFIED | every input_snapshot source is provenance-`verified` (or has a readable actual_as_of ≤ cutoff), and no FAILED |
| UNVERIFIED | anything else — missing `market_data_context`/`source_provenance`, sources with no readable timestamp, or expected sources absent from provenance |

Structural/context keys that never carry a timestamp (`vpa_*`, `event_coverage`,
`cninfo_*`, `price_basis`, `fund_flow_consensus_guard`, …) are exempt — see
`_NO_TIMESTAMP_SOURCES` in the script.

## launchd

`com.davidliu.ta-p4-ledger.plist` is a template (per controller revision #2 it
is *not* installed by this change). Mon–Fri 19:45, after the 16:37 measurement
batch. Logs land in `work/phase2-ledger/launchd.{out,err}.log`; a non-zero exit
is visible there and in `snapshot_ledger.log`.
