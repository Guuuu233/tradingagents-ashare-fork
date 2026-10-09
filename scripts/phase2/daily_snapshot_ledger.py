#!/usr/bin/env python3
"""DAV-1480 P4: daily read-only snapshot -> append-only forward ledger (hash-chained).

Reads the production SQLite DB with ``file:...?mode=ro`` (no write path touched)
and appends one JSONL line per newly-seen completed report to an append-only
ledger under ``work/phase2-ledger/``. Each line carries:

* report metadata (report_id, user_id, symbol, signal/trade_date, created/updated)
* version fields (run_identity, served_models, input_snapshot hash, commit SHA)
* per-horizon manager judgement fields (direction, probability, decision,
  trade_action, risk_status, final action, forecast)
* ``record_sha256`` over the canonical JSON of the sealed judgement record —
  the hash chain is built on this field (per controller revision #1).
* ``result_data_sha256_at_seal`` — reference hash of result_data at seal time;
  NOT part of tamper detection because DAV-1458 / D-071 may legitimately
  extend result_data later.
* ``timing_class`` (mutually exclusive):
    F0: sealed_at < entry_open   (entry = T+1 trading-day open 09:30)
    F1: entry_open <= sealed_at < outcome_known (outcome = T+40 trading-day close)
    H : sealed_at >= outcome_known
  sealed_at = max(created_at, snapshot run time). Trade calendar comes from
  akshare.tool_trade_date_hist_sina via tradingagents.dataflows.trade_calendar;
  if the calendar is unavailable the run FAILS (no silent weekday fallback —
  F0/F1 boundary must not be approximated).
* ``input_pit_status`` (orthogonal to timing_class):
    VERIFIED  — every listed data source's actual_as_of <= requested_as_of
    FAILED    — any source's actual_as_of > requested_as_of (look-ahead), or a
                source provenance entry explicitly marked "future"
    UNVERIFIED— some source lacks a readable timestamp, or provenance info is
                absent for a source expected in input_snapshot.keys
  Source timestamps come from result_data.market_data_context[hz].source_provenance
  (written by data_collector._build_source_provenance); sources absent from
  provenance fall back to "no timestamp" -> UNVERIFIED. Never defaults to
  VERIFIED. requested/actual as_of values are compared as parsed timestamps
  (_parse_dt), not raw strings, so both "YYYY-MM-DD" and ISO datetimes work.

External head anchor (work/phase2-ledger/HEAD): a hash chain alone cannot
prove the tail of the file was not truncated — every surviving line still
verifies. "run" therefore registers the terminal chain_hash + line count in
a separate small JSON file after each successful append, and "verify"
re-checks the computed tail against that anchor. A ledger that predates
anchors gets one bootstrapped (bootstrapped: true) from the current tail —
truncations that happened BEFORE bootstrap are undetectable; only post-
bootstrap growth is covered. Missing anchor -> explicit UNVERIFIED exit;
corrupt anchor -> fail-close.

Usage:
  python scripts/phase2/daily_snapshot_ledger.py run        [--db PATH] [--ledger-dir DIR] [--date YYYY-MM-DD]
  python scripts/phase2/daily_snapshot_ledger.py verify     [--ledger-dir DIR]
  python scripts/phase2/daily_snapshot_ledger.py baseline   --snapshot-jsonl FILE [--db PATH] [--ledger-dir DIR]
  python scripts/phase2/daily_snapshot_ledger.py status     [--ledger-dir DIR]

"run" refuses to seal while reports are still status in (running, pending,
queued) unless --force is given; a deferred run logs and exits 2 so launchd
does not retry-storm.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DB = REPO_ROOT / "data" / "tradingagents.db"
DEFAULT_LEDGER_DIR = REPO_ROOT / "work" / "phase2-ledger"
LEDGER_FILE = "forward_ledger.jsonl"
STATE_FILE = "ledger_state.json"
HEAD_FILE = "HEAD"
LOG_FILE = "snapshot_ledger.log"

BLOCKING_STATUSES = ("running", "pending", "queued")
CHAIN_FIELD = "record_sha256"
GENESIS_PREV = "GENESIS"
# Fixed P4 account (controller order 2026-10-09): only this user_id is
# sealed; reports of other accounts are skipped (history append-only,
# never rewritten).
DEFAULT_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
# A seal this many days after report creation means the row was backfilled,
# not a fresh forward observation — flagged so H records from the backfill
# queue are not compared with future genuinely-forward H records.
BACKFILL_LAG_DAYS = 2.0

HORIZONS = ("short", "medium")
ENTRY_OFFSET_TRADING_DAYS = 1   # T+1 open 09:30
OUTCOME_OFFSET_TRADING_DAYS = 40  # T+40 close
MARKET_OPEN_HHMM = "09:30"
MARKET_CLOSE_HHMM = "15:00"

# ---------------------------------------------------------------------------
# small utils


def _log(ledger_dir: Path, msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        ledger_dir.mkdir(parents=True, exist_ok=True)
        with open(ledger_dir / LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _canonical(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, UTF-8, no extra whitespace."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _parse_dt(text: Any) -> Optional[datetime]:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    # Accept "YYYY-MM-DD HH:MM:SS[.ffffff]" and ISO-8601 with T / offset.
    try:
        if "T" in s or "+" in s[10:] or s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None


def _fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


# ---------------------------------------------------------------------------
# trade calendar (akshare via tradingagents.dataflows.trade_calendar; no fallback)


def _load_trade_dates() -> list[date]:
    """Return sorted CN trading dates; raise if unavailable (hard fail)."""
    from tradingagents.dataflows.trade_calendar import require_cn_trade_dates, _parse_date

    dates, _ = require_cn_trade_dates()
    return sorted(dates)


def _t_plus_n(dates: list[date], base: date, n: int) -> Optional[date]:
    """The trading day exactly n trading days after ``base`` (base must snap down)."""
    idx = bisect.bisect_right(dates, base) - 1
    target = idx + n
    if idx < 0 or target >= len(dates):
        return None
    return dates[target]


def _classify_timing(
    sealed_at: datetime,
    signal_date: date,
    trade_dates: list[date],
) -> tuple[str, dict[str, Any]]:
    """Return (timing_class, evidence dict).

    entry_open   = T+1 trading day 09:30 Asia/Shanghai clock time (naive date
                   comparison is enough: sealed_at is a UTC instant, entry and
                   outcome are market-clock instants; convert all to UTC).
    outcome_close= T+40 trading day 15:00 Asia/Shanghai.
    """
    cn_offset = timedelta(hours=8)

    def cn_dt(d: date, hhmm: str) -> datetime:
        h, m = int(hhmm[:2]), int(hhmm[3:])
        return datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc) - cn_offset

    entry_day = _t_plus_n(trade_dates, signal_date, ENTRY_OFFSET_TRADING_DAYS)
    outcome_day = _t_plus_n(trade_dates, signal_date, OUTCOME_OFFSET_TRADING_DAYS)

    evidence: dict[str, Any] = {
        "signal_date": signal_date.isoformat(),
        "entry_trading_day": entry_day.isoformat() if entry_day else None,
        "outcome_trading_day": outcome_day.isoformat() if outcome_day else None,
        "entry_open_utc": None,
        "outcome_close_utc": None,
    }
    if entry_day is None:
        # signal_date is earlier than the first calendar date (snap-down
        # index < 0): the T+1 entry day cannot be resolved, so conservatively
        # classify F0.
        evidence["note"] = "calendar does not reach T+1; entry day unresolved"
        return "F0", evidence

    entry_open = cn_dt(entry_day, MARKET_OPEN_HHMM)
    evidence["entry_open_utc"] = entry_open.isoformat()
    if sealed_at < entry_open:
        return "F0", evidence

    if outcome_day is None:
        evidence["note"] = "calendar ends before T+40; outcome not yet knowable"
        return "F1", evidence

    outcome_close = cn_dt(outcome_day, MARKET_CLOSE_HHMM)
    evidence["outcome_close_utc"] = outcome_close.isoformat()
    if sealed_at < outcome_close:
        return "F1", evidence
    return "H", evidence


# ---------------------------------------------------------------------------
# input_pit_status


def _iter_source_entries(mdc: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield (source, provenance entry) across horizons."""
    seen: set[str] = set()
    for hz, sub in mdc.items():
        if not isinstance(sub, dict):
            continue
        sp = sub.get("source_provenance")
        if not isinstance(sp, dict):
            continue
        for src, info in sp.items():
            if src in seen or not isinstance(info, dict):
                continue
            seen.add(src)
            yield src, info


# Sources that legitimately never carry a per-source as_of timestamp. They are
# either structural containers (contexts, guards) or sources the provenance
# builder deliberately skips (cninfo_* are tracked elsewhere). Their absence
# from provenance must NOT drag input_pit_status to UNVERIFIED.
_NO_TIMESTAMP_SOURCES = {
    "cninfo_announcements", "cninfo_records", "cninfo_ir_surveys",
    "event_coverage", "indicators", "market_data_context", "price_basis",
    "social_data_context", "vpa_context", "vpa_indicators", "vpa_structured",
    "fund_flow_consensus_guard",
}

# A source is "timeliness verified" when the pipeline's own provenance builder
# marked it verified (requested/actual as_of checked at write time), OR when it
# has a readable actual_as_of <= requested_as_of. Anything else with a readable
# timestamp is FAILED (look-ahead); anything without a timestamp is UNVERIFIED.


def _pit_as_of_cmp(actual: Any, requested: Any) -> Optional[int]:
    """Compare actual vs requested as_of; -1/0/+1, or None if unparseable.

    Values in the wild are "YYYY-MM-DD" today but may become ISO datetimes;
    parse both via _parse_dt instead of comparing raw strings (lexicographic
    order breaks the moment a time component appears).
    """
    a, r = _parse_dt(actual), _parse_dt(requested)
    if a is None or r is None:
        return None
    return -1 if a < r else (1 if a > r else 0)



def _classify_input_pit(
    result_data: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """VERIFIED / FAILED / UNVERIFIED from market_data_context provenance."""
    mdc = result_data.get("market_data_context")
    snap = result_data.get("input_snapshot") or {}
    expected = set(snap.get("keys") or [])

    evidence: dict[str, Any] = {"expected_sources": sorted(expected),
                              "unverified_sources": [], "failed_sources": [],
                              "covered_sources": [], "exempt_sources": []}
    if not isinstance(mdc, dict) or not mdc:
        evidence["reason"] = "market_data_context missing"
        return "UNVERIFIED", evidence

    entries = dict(_iter_source_entries(mdc))

    failed, unverified, covered = [], [], []
    for src, info in entries.items():
        ps = info.get("provenance_status")
        req = info.get("requested_as_of")
        act = info.get("actual_as_of") or info.get("as_of")
        cmp_asof = _pit_as_of_cmp(act, req) if (req and act) else None
        if ps == "future" or cmp_asof == 1:
            failed.append({"source": src, "requested_as_of": req,
                           "actual_as_of": act, "status": info.get("status")})
        elif ps == "verified":
            covered.append(src)
        elif cmp_asof is not None and cmp_asof <= 0:
            # Timestamp present and inside the cutoff even though the pipeline
            # didn't mark it verified (e.g. legacy rows) -> still timeliness-ok.
            covered.append(src)
        else:
            unverified.append({"source": src,
                               "provenance_status": ps,
                               "status": info.get("status")})

    if not entries:
        evidence["reason"] = "source_provenance missing"

    # Sources declared in input_snapshot but absent from provenance: exempt the
    # structural/no-timestamp set; flag the rest as unverified (no readable
    # timestamp to judge).
    missing = sorted(s for s in expected
                     if s not in entries and s not in _NO_TIMESTAMP_SOURCES)
    for src in missing:
        unverified.append({"source": src, "provenance_status": "absent"})
    evidence["exempt_sources"] = sorted(
        s for s in expected if s in _NO_TIMESTAMP_SOURCES and s not in entries)

    evidence["failed_sources"] = failed
    evidence["unverified_sources"] = unverified
    evidence["covered_sources"] = sorted(covered)

    if failed:
        return "FAILED", evidence
    if unverified:
        return "UNVERIFIED", evidence
    if not covered and not entries:
        return "UNVERIFIED", evidence
    return "VERIFIED", evidence


# ---------------------------------------------------------------------------
# record extraction

_JUDGEMENT_FIELDS = (
    "direction", "probability", "decision", "trade_action",
    "risk_status", "final_trade_decision", "forecast", "confidence",
    "decision_status", "extraction_warning",
)


def _horizon_judgement(result_data: dict[str, Any], hz: str) -> dict[str, Any]:
    sub = (result_data.get("horizons") or {}).get(hz)
    if not isinstance(sub, dict):
        sub = result_data.get(f"{hz}_term") or {}
    out = {k: sub.get(k) for k in _JUDGEMENT_FIELDS if k in sub}
    if not out:
        # very old rows: fall back to top-level fields once
        for k in ("direction", "decision", "trade_action", "risk_status",
                  "probability", "confidence", "final_trade_decision", "forecast"):
            if k in result_data:
                out[k] = result_data.get(k)
    return out


def _version_fields(result_data: dict[str, Any]) -> dict[str, Any]:
    snap = result_data.get("input_snapshot") or {}
    hrm = result_data.get("horizon_run_metadata") or {}
    return {
        "run_identity": result_data.get("run_identity"),
        "served_models": result_data.get("served_models"),
        "input_snapshot_algo": snap.get("algo"),
        "input_snapshot_hash": snap.get("hash"),
        "generated_by_commit_sha": result_data.get("generated_by_commit_sha"),
        "protocol_version": result_data.get("protocol_version"),
        "evidence_contract_version": result_data.get("evidence_contract_version"),
        "decision_model_version": result_data.get("decision_model_version"),
        "requested_horizons": hrm.get("requested") or result_data.get("requested_horizons"),
        "resolved_horizons": hrm.get("resolved"),
        "primary_eval_offsets": hrm.get("primary_eval_offsets"),
    }


def build_record(
    row: sqlite3.Row,
    result_data_raw: str,
    sealed_at: datetime,
    snapshot_date: str,
    trade_dates: list[date],
) -> dict[str, Any]:
    result_data = json.loads(result_data_raw) if result_data_raw else {}
    if not isinstance(result_data, dict):
        result_data = {}
    pit_status, pit_evidence = _classify_input_pit(result_data)

    created = _parse_dt(row["created_at"])
    seal_basis = sealed_at
    if created and created > sealed_at:
        seal_basis = created
    sealed_lag_days: Optional[float] = None
    if created:
        sealed_lag_days = round((sealed_at - created).total_seconds() / 86400.0, 4)
    backfilled = sealed_lag_days is not None and sealed_lag_days > BACKFILL_LAG_DAYS

    signal = _parse_dt(row["trade_date"])
    if signal is None:
        timing_class, timing_ev = "UNCLASSIFIED", {"reason": "missing trade_date"}
    else:
        timing_class, timing_ev = _classify_timing(
            seal_basis, signal.date(), trade_dates)

    horizons = {hz: _horizon_judgement(result_data, hz) for hz in HORIZONS}
    versions = _version_fields(result_data)

    record = {
        "ledger_version": 1,
        "snapshot_date": snapshot_date,
        "sealed_at": _fmt_dt(sealed_at),
        "seal_basis_at": _fmt_dt(seal_basis),
        "report_id": row["id"],
        "user_id": row["user_id"],
        "symbol": row["symbol"],
        "industry": row["industry"],
        "signal_date": row["trade_date"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "status": row["status"],
        "analysis_status": row["analysis_status"],
        "versions": versions,
        "horizons": horizons,
        "final_trade_decision_top": row["final_trade_decision"],
        "trade_action_top": row["trade_action"],
        "risk_status_top": row["risk_status"],
        "decision_top": row["decision"],
        "direction_top": row["direction"],
        "probability_top": row["probability"],
        "timing_class": timing_class,
        "sealed_lag_days": sealed_lag_days,
        "backfilled": backfilled,
        "timing_evidence": timing_ev,
        "input_pit_status": pit_status,
        "input_pit_evidence": pit_evidence,
        "result_data_sha256_at_seal": _sha256_bytes(
            result_data_raw.encode("utf-8") if isinstance(result_data_raw, str)
            else _canonical(result_data)),
    }
    record[CHAIN_FIELD] = _sha256_bytes(_canonical(record))
    return record


# ---------------------------------------------------------------------------
# ledger IO


def _read_last_line(ledger_path: Path) -> Optional[dict[str, Any]]:
    """Parsed last non-empty ledger line, or None (missing/empty file).

    Raises ValueError when the tail line exists but cannot be parsed — the
    caller must NOT treat an unreadable tail as an empty ledger.
    """
    if not ledger_path.exists():
        return None
    last = b""
    with open(ledger_path, "rb") as fh:
        for line in fh:
            if line.strip():
                last = line
    if not last:
        return None
    try:
        obj = json.loads(last)
    except Exception as exc:
        raise ValueError(f"unparseable ledger tail line: {exc}") from exc
    if not isinstance(obj, dict) or not obj.get("chain_hash"):
        raise ValueError("ledger tail line lacks chain_hash")
    return obj


def _count_ledger_lines(ledger_path: Path) -> int:
    if not ledger_path.exists():
        return 0
    with open(ledger_path, "rb") as fh:
        return sum(1 for line in fh if line.strip())


# ---------------------------------------------------------------------------
# external head anchor
#
# A self-contained hash chain cannot detect TRUNCATION at the tail: every
# surviving line still verifies. The anchor file (JSON, next to the ledger)
# pins the terminal chain_hash + line count after each successful run, and
# verify re-checks the computed tail against it.
#
# Ledgers written before anchors existed get one bootstrap entry stamped
# from the current tail ("bootstrapped": true). Truncation that already
# happened before bootstrap is undetectable — coverage starts the day the
# anchor is first written and grows from there.


def _write_head_anchor(ledger_dir: Path, chain_hash: str, n_lines: int,
                       bootstrapped: bool, reason: str) -> Path:
    tmp = ledger_dir / (HEAD_FILE + ".tmp")
    tmp.write_text(json.dumps({
        "chain_hash": chain_hash,
        "lines": n_lines,
        "anchored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bootstrapped": bootstrapped,
        "reason": reason,
    }, ensure_ascii=False, indent=1) + "\n", "utf-8")
    tmp.replace(ledger_dir / HEAD_FILE)
    return ledger_dir / HEAD_FILE


def _check_head_anchor(ledger_path: Path, tail_hash: str, n_lines: int,
                       log_dir: Optional[Path] = None) -> int:
    """Compare the verified tail against the external anchor.

    Returns 0 (anchor present and matches, or bootstrapped now), 2 (no
    anchor yet and nothing to bootstrap from, or ledger empty), 4 (anchor
    corrupt — fail-close), 5 (anchor mismatch — tail truncated/rewritten).
    """
    ledger_dir = ledger_path.parent
    log = (lambda m: _log(log_dir, m)) if log_dir else (lambda m: print(m))
    anchor_path = ledger_dir / HEAD_FILE
    if tail_hash is None or n_lines == 0:
        print("UNVERIFIED: ledger missing or empty; tail integrity "
              "cannot be anchored", file=sys.stderr)
        return 2
    if not anchor_path.exists():
        _write_head_anchor(ledger_dir, tail_hash, n_lines,
                           bootstrapped=True,
                           reason="ledger predates head anchor")
        log(f"HEAD anchor bootstrapped from current tail ({n_lines} lines); "
            "truncations before this point are undetectable")
        return 0
    try:
        anchor = json.loads(anchor_path.read_text("utf-8"))
        if not isinstance(anchor, dict) or not isinstance(anchor.get("chain_hash"), str):
            raise ValueError("missing chain_hash")
    except Exception as exc:
        print(f"FAIL: head anchor {anchor_path} unreadable/corrupt: {exc} "
              "(fail-close)", file=sys.stderr)
        return 4
    ok = (anchor.get("chain_hash") == tail_hash
          and anchor.get("lines") == n_lines)
    if ok:
        print(f"OK: tail matches head anchor ({n_lines} lines, "
              f"chain_hash={tail_hash[:16]}...)")
        return 0
    print("FAIL: ledger tail does not match head anchor — tail truncation "
          "or rewrite detected", file=sys.stderr)
    print(f"  anchor: lines={anchor.get('lines')} "
          f"chain_hash={str(anchor.get('chain_hash'))[:16]}... "
          f"anchored_at={anchor.get('anchored_at')}", file=sys.stderr)
    print(f"  actual: lines={n_lines} chain_hash={tail_hash[:16]}...",
          file=sys.stderr)
    return 5


def _load_state(ledger_dir: Path) -> dict[str, Any]:
    p = ledger_dir / STATE_FILE
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    return {"sealed_report_ids": []}


def _save_state(ledger_dir: Path, state: dict[str, Any]) -> None:
    p = ledger_dir / STATE_FILE
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), "utf-8")


def _load_sealed_ids(ledger_dir: Path, state: dict[str, Any]) -> set[str]:
    """Sealed IDs = state file ∪ ledger scan (state file is a cache only).

    Unreadable ledger lines are counted and logged — never silently treated
    as 'not sealed', which would re-append duplicates.
    """
    ids = set(state.get("sealed_report_ids") or [])
    lp = ledger_dir / LEDGER_FILE
    bad = 0
    if lp.exists():
        with open(lp, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ids.add(json.loads(line)["record"]["report_id"])
                except Exception:
                    bad += 1
    if bad:
        _log(ledger_dir,
             f"WARNING: {bad} unreadable ledger line(s) while loading sealed "
             "ids; affected report_ids are treated as STILL SEALED (skipped, "
             "not re-appended). Fix the ledger before relying on them.")
    return ids


# ---------------------------------------------------------------------------
# commands


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def cmd_run(args: argparse.Namespace) -> int:
    ledger_dir = Path(args.ledger_dir)
    ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = ledger_dir / LEDGER_FILE
    snapshot_date = args.date or date.today().isoformat()
    sealed_at = datetime.now(timezone.utc)
    user_id = getattr(args, "user_id", None) or DEFAULT_USER_ID

    con = _connect_ro(Path(args.db))
    try:
        n_block = con.execute(
            f"SELECT COUNT(*) FROM reports WHERE status IN ({','.join('?'*len(BLOCKING_STATUSES))}) AND user_id = ?",
            (*BLOCKING_STATUSES, user_id),
        ).fetchone()[0]
        if n_block and not args.force:
            _log(ledger_dir,
                 f"DEFERRED: {n_block} report(s) still in {BLOCKING_STATUSES}; "
                 "snapshot postponed, will retry on next launchd tick")
            return 2

        state = _load_state(ledger_dir)
        sealed_ids = _load_sealed_ids(ledger_dir, state)

        # New reports = completed since the latest sealed created_at we know,
        # or simply any completed id not yet sealed (robust to backfills).
        rows = con.execute(
            "SELECT id, user_id, symbol, industry, trade_date, status,"
            "       analysis_status, decision, direction, probability,"
            "       trade_action, risk_status, final_trade_decision,"
            "       created_at, updated_at, result_data"
            "  FROM reports WHERE status='completed' ORDER BY created_at"
        ).fetchall()
        new_rows = [r for r in rows if r["id"] not in sealed_ids]
        n_skip_user = 0
        if user_id:
            before = len(new_rows)
            new_rows = [r for r in new_rows if r["user_id"] == user_id]
            n_skip_user = before - len(new_rows)
            if n_skip_user:
                _log(ledger_dir,
                     f"SKIP: {n_skip_user} report(s) filtered by user_id "
                     f"(kept user_id={user_id})")
        if not new_rows:
            _log(ledger_dir, f"OK: no new completed reports ({len(rows)} total already sealed)")
            return 0

        try:
            trade_dates = _load_trade_dates()
        except Exception as exc:
            _log(ledger_dir, f"FAILED: trade calendar unavailable: {exc}")
            return 3

        # Fail-close: never append on top of an unreadable tail — that would
        # silently start a new genesis segment detached from history.
        try:
            tail_obj = _read_last_line(ledger_path)
        except ValueError as exc:
            _log(ledger_dir,
                 f"FAILED: ledger tail unreadable: {exc}; refusing to append "
                 f"(would detach a new genesis segment from history). "
                 f"ledger={ledger_path}")
            return 4
        prev_hash = tail_obj["chain_hash"] if tail_obj else GENESIS_PREV
        appended = 0
        with open(ledger_path, "a", encoding="utf-8") as out:
            for row in new_rows:
                rec = build_record(row, row["result_data"], sealed_at,
                                   snapshot_date, trade_dates)
                chain_hash = _sha256_bytes(
                    (prev_hash + rec[CHAIN_FIELD]).encode("utf-8"))
                line = {"record": rec, "prev_hash": prev_hash,
                        "chain_hash": chain_hash}
                out.write(json.dumps(line, ensure_ascii=False,
                                     separators=(",", ":")) + "\n")
                prev_hash = chain_hash
                sealed_ids.add(rec["report_id"])
                appended += 1

        state["sealed_report_ids"] = sorted(sealed_ids)
        state["last_run_at"] = sealed_at.isoformat()
        state["last_appended"] = appended
        _save_state(ledger_dir, state)
        _write_head_anchor(ledger_dir, prev_hash,
                           _count_ledger_lines(ledger_path),
                           bootstrapped=False, reason="run append")
        _log(ledger_dir, f"OK: appended {appended} record(s); ledger={ledger_path}")
        _log(ledger_dir, f"OK: head anchor updated ({prev_hash[:16]}...)")
        return 0
    finally:
        con.close()


def cmd_verify(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger_dir) / LEDGER_FILE
    if not ledger_path.exists():
        print(f"ledger not found: {ledger_path}", file=sys.stderr)
        return 2
    prev = GENESIS_PREV
    n = 0
    bad = []
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            n += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append((lineno, f"json decode: {exc}"))
                continue
            rec = obj.get("record")
            if not isinstance(rec, dict):
                bad.append((lineno, "record missing"))
                continue
            if obj.get("prev_hash") != prev:
                bad.append((lineno, "prev_hash mismatch (chain broken)"))
            stored = rec.get(CHAIN_FIELD)
            payload = {k: v for k, v in rec.items() if k != CHAIN_FIELD}
            expect_rec = _sha256_bytes(_canonical(payload))
            if stored != expect_rec:
                bad.append((lineno, "record_sha256 mismatch (record modified)"))
            expect_chain = _sha256_bytes(
                (prev + (stored or "")).encode("utf-8"))
            if obj.get("chain_hash") != expect_chain:
                bad.append((lineno, "chain_hash mismatch"))
            prev = obj.get("chain_hash", prev)
    if bad:
        print(f"FAIL: {len(bad)} problem(s) in {n} line(s)")
        for lineno, msg in bad[:20]:
            print(f"  line {lineno}: {msg}")
        return 1
    print(f"OK: {n} line(s) verified, chain intact")
    # The chain alone cannot detect tail truncation — compare the terminal
    # chain_hash against the external head anchor (bootstraps on first use).
    tail_hash = prev if n else None
    return _check_head_anchor(ledger_path, tail_hash, n)


def cmd_baseline(args: argparse.Namespace) -> int:
    """Reconcile a prior snapshot JSONL against current DB result_data hashes.

    Only report_id + result_data_sha256 are compared; mismatches are listed
    with a note whether the live row still exists / whether only post-seal
    measurement fields (e.g. t_plus_5) plausibly differ.
    """
    snap_path = Path(args.snapshot_jsonl)
    rows = [json.loads(l) for l in snap_path.read_text("utf-8").splitlines()
            if l.strip()]
    con = _connect_ro(Path(args.db))
    mismatched, missing, matched, absent_in_db = [], [], 0, []
    try:
        for r in rows:
            rid = r.get("id") or r.get("report_id")
            want = r.get("result_data_sha256")
            row = con.execute("SELECT result_data FROM reports WHERE id=?",
                              (rid,)).fetchone()
            if not row:
                absent_in_db.append(rid)
                continue
            got = _sha256_bytes(row[0].encode("utf-8"))
            if got == want:
                matched += 1
            else:
                note = _diff_hint(row[0])
                mismatched.append({"id": rid,
                                   "snapshot_sha256": want,
                                   "db_sha256": got,
                                   "note": note})
    finally:
        con.close()

    report = {
        "snapshot": str(snap_path),
        "checked": len(rows),
        "matched": matched,
        "mismatched": mismatched,
        "absent_in_db": absent_in_db,
    }
    out = json.dumps(report, ensure_ascii=False, indent=1)
    print(out)
    if args.ledger_dir:
        ld = Path(args.ledger_dir)
        ld.mkdir(parents=True, exist_ok=True)
        (ld / "baseline_check_20261004.json").write_text(out, "utf-8")
    return 0 if not mismatched and not absent_in_db else 1


def _diff_hint(result_data_raw: str) -> str:
    try:
        d = json.loads(result_data_raw)
    except Exception:
        return "result_data not decodable now"
    hints = []
    for key in ("t_plus_5", "tplus5", "measurement", "shadow_credit_metrics",
                "data_utilization_metrics"):
        if key in d:
            hints.append(f"has '{key}'")
    return "; ".join(hints) or "unknown post-seal change"


def cmd_status(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger_dir) / LEDGER_FILE
    n = 0
    first = last = None
    if ledger_path.exists():
        with open(ledger_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                n += 1
                obj = json.loads(line)
                first = first or obj["record"]["sealed_at"]
                last = obj["record"]["sealed_at"]
    print(json.dumps({"ledger": str(ledger_path), "records": n,
                      "first_sealed_at": first, "last_sealed_at": last},
                     indent=1))
    return 0


# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--db", default=str(DEFAULT_DB))
        p.add_argument("--ledger-dir", default=str(DEFAULT_LEDGER_DIR))

    p_run = sub.add_parser("run", help="append today's new reports")
    common(p_run)
    p_run.add_argument("--date", default=None,
                       help="snapshot_date label (default: today UTC)")
    p_run.add_argument("--force", action="store_true",
                       help="seal even if blocking statuses exist")
    p_run.add_argument("--user-id", default=DEFAULT_USER_ID,
                       help="only seal reports of this user_id "
                            "(default: fixed P4 account)")
    p_run.set_defaults(func=cmd_run)

    p_v = sub.add_parser("verify", help="verify hash chain integrity")
    common(p_v)
    p_v.set_defaults(func=cmd_verify)

    p_b = sub.add_parser("baseline", help="reconcile a prior snapshot JSONL")
    common(p_b)
    p_b.add_argument("--snapshot-jsonl", required=True)
    p_b.set_defaults(func=cmd_baseline)

    p_s = sub.add_parser("status", help="ledger summary")
    common(p_s)
    p_s.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
