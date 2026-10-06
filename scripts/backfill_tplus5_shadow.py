#!/usr/bin/env python3
"""Bounded-memory T+5 measurement backfill; never migrates schema.

Only copies may be written until the signed first-production handoff. Production
requires --production-authorized (after 2026-10-09), healthy/idle runtime evidence,
and a durable rollback journal. A timeout is busy/unknown, never proof of death.

Before any write transaction, every candidate row's pre-image (report_id plus the
exact result_data bytes that would be replaced) is exported to an independent
zstd-compressed file outside the database directory. If the export or its sha256
check fails, no UPDATE is ever attempted. The same file drives the 'restore'
subcommand, which writes the exported bytes back and compares row-by-row.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import closing
from datetime import date, datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import quote

import zstandard

PRE_EXPORT_VERSION = 1

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Independent pre-image exports live outside the data directory (库外固定目录).
PRE_EXPORT_DIR = ROOT / "work/tplus5-pre-export"

from tradingagents.agents.utils.shadow_credit import (
    is_qualifying_v2_report, detect_tplus5_suspension,
)
from tradingagents.dataflows.trade_calendar import now_cn, cn_market_phase, trading_days_forward

MEASUREMENT_KEYS = frozenset({
    "t_plus_5_date", "t_plus_5_price", "t_plus_5_status", "is_t_plus_5_due",
    "t_plus_5_evaluated", "t_plus_5_direction_hit", "t_plus_5_return_pct",
    "t_plus_5_provenance", "t_plus_5_missing_reason",
})
COUNTERS = ("total_scanned", "qualifying_v2_count", "skipped_non_qualifying",
            "due_count", "evaluated_count", "hit_count", "miss_count",
            "data_missing_count", "suspension_count", "pending_due_count")


def without_tplus5(value):
    """Structural invariant; no cohort, entry, verdict or feature key is exempt."""
    if isinstance(value, dict):
        result = {k: without_tplus5(v) for k, v in value.items() if k not in MEASUREMENT_KEYS}
        if result.get("shadow_credit_metrics") == {}:
            result.pop("shadow_credit_metrics")
        return result
    if isinstance(value, list):
        return [without_tplus5(v) for v in value]
    return value


def _json_node(text, start=0):
    """Token spans retain opaque JSON spellings (e.g. 1e+02) and whitespace."""
    while text[start].isspace():
        start += 1
    if text[start] != "{":
        value, end = json.JSONDecoder().raw_decode(text, start)
        return {"start": start, "end": end, "value": value}
    pos = start + 1
    members = {}
    while True:
        while text[pos].isspace():
            pos += 1
        if text[pos] == "}":
            break
        key_start = pos
        key, pos = json.JSONDecoder().raw_decode(text, pos)
        if key in members:
            raise ValueError("duplicate JSON key; unsafe to patch")
        while text[pos].isspace():
            pos += 1
        if text[pos] != ":":
            raise ValueError("invalid JSON object")
        node = _json_node(text, pos + 1)
        members[key] = (key_start, node)
        pos = node["end"]
        while text[pos].isspace():
            pos += 1
        if text[pos] == "}":
            break
        if text[pos] != ",":
            raise ValueError("invalid JSON separator")
        pos += 1
    return {"start": start, "end": pos + 1, "close": pos, "members": members,
            "value": {k: v[1]["value"] for k, v in members.items()}}


def _nonmeasurement_tokens(text, node):
    if "members" not in node:
        return text[node["start"]:node["end"]]
    fields = []
    for key, (start, child) in node["members"].items():
        if key in MEASUREMENT_KEYS:
            continue
        projected = _nonmeasurement_tokens(text, child)
        if key == "shadow_credit_metrics" and projected == "{}":
            continue
        fields.append(text[start:child["start"]] + projected)
    return "{" + ",".join(fields) + "}"


def non_tplus5_bytes(text):
    """Byte witness after removing only measurement members/separators.

    Original key/colon whitespace and all opaque value tokens remain byte-exact.
    Container separators are normalized because inserting/removing a member
    necessarily introduces/removes a comma. This does not reserialize values.
    """
    return _nonmeasurement_tokens(text, _json_node(text)).encode("utf-8")


def _plan_json_patch(node, desired, edits):
    current = node["value"]
    if "members" not in node:
        if current != desired:
            raise ValueError("non-T+5 value changed")
        return
    if not isinstance(desired, dict):
        raise ValueError("non-T+5 container changed")
    added = []
    for key, value in desired.items():
        if key not in node["members"]:
            if key not in MEASUREMENT_KEYS and not (
                key == "shadow_credit_metrics" and isinstance(value, dict)
                and set(value) <= MEASUREMENT_KEYS
            ):
                raise ValueError("non-T+5 field added")
            added.append(json.dumps(key) + ":" + json.dumps(value, ensure_ascii=False, allow_nan=False))
            continue
        child = node["members"][key][1]
        if key in MEASUREMENT_KEYS:
            if child["value"] != value:
                edits.append((child["start"], child["end"], json.dumps(value, ensure_ascii=False, allow_nan=False)))
        else:
            _plan_json_patch(child, value, edits)
    if set(current) - set(desired):
        raise ValueError("non-T+5 field removed")
    if added:
        prefix = "," if current else ""
        edits.append((node["close"], node["close"], prefix + ",".join(added)))


def patch_tplus5_json(original, desired):
    if without_tplus5(json.loads(original)) != without_tplus5(desired):
        raise ValueError("non-T+5 structural mismatch")
    edits = []
    _plan_json_patch(_json_node(original), desired, edits)
    updated = original
    for start, end, replacement in sorted(edits, reverse=True):
        updated = updated[:start] + replacement + updated[end:]
    if non_tplus5_bytes(original) != non_tplus5_bytes(updated):
        raise ValueError("non-T+5 byte mismatch")
    return updated


def load_calendar():
    # Offline package resource, not a Mon-Fri substitute. Reject expired range.
    import akshare
    values = json.loads((Path(akshare.__file__).parent / "file_fold/calendar.json").read_text())
    return sorted(date.fromisoformat(f"{x[:4]}-{x[4:6]}-{x[6:8]}") for x in values)


def _positive(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


class PriceSeriesFetcher:
    """One daily+factor pair per symbol/window, shared by both horizons.

    Factors are joined to every bar, using one common anchor from this pair.
    Absolute normalization cancels for T+1 Open / T+5 Close direction/return.
    No report entry or old stored T+5 price is ever mixed into this series.
    """
    def __init__(self, min_interval=0.5):
        self.cache = {}
        self.request_count = 0
        self.last_request = 0.0
        self.min_interval = min_interval

    def _query(self, api, symbol, start, end):
        import requests
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token:
            raise ValueError("credentials_missing")
        wait = self.min_interval - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        self.request_count += 1
        url = os.getenv("TUSHARE_API_URL") or os.getenv("TUSHARE_BASE_URL") or "https://api.tushare.pro"
        fields = "ts_code,trade_date,open,close" if api == "daily" else "ts_code,trade_date,adj_factor"
        payload = {"api_name": api, "token": token, "params": {
            "ts_code": symbol, "start_date": start.replace("-", ""),
            "end_date": end.replace("-", "")}, "fields": fields}
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.post(url, json=payload, timeout=(5, 15))
            response.raise_for_status()
            body = response.json()
        finally:
            session.close()
        # Never log provider message/payload: private gateway may echo credentials.
        if not isinstance(body, dict) or body.get("code") != 0:
            raise ValueError("provider_rejected")
        data = body.get("data") or {}
        keys, rows = data.get("fields"), data.get("items")
        if not isinstance(keys, list) or not isinstance(rows, list):
            raise ValueError("provider_shape")
        if not set(fields.split(",")) <= set(keys):
            raise ValueError("provider_fields_missing")
        if any(not isinstance(row, list) or len(row) != len(keys) for row in rows):
            raise ValueError("provider_row_shape")
        return [dict(zip(keys, row)) for row in rows]

    def __call__(self, symbol, start, end):
        key = (symbol, start, end)
        if key in self.cache:
            return self.cache[key]
        fetched_on = now_cn().date().isoformat()
        try:
            daily = self._query("daily", symbol, start, end)
            factors = self._query("adj_factor", symbol, start, end)
            factor_by_date = self._index(factors, symbol, start, end)
            daily_by_date = self._index(daily, symbol, start, end)
            anchor = _positive(factor_by_date.get(max(factor_by_date), {}).get("adj_factor")) if factor_by_date else None
            if not anchor:
                raise ValueError("adjustment_factors_missing")
            bars = {}
            for day, row in daily_by_date.items():
                factor = _positive(factor_by_date.get(day, {}).get("adj_factor"))
                op, cl = _positive(row.get("open")), _positive(row.get("close"))
                if not factor or not op or not cl:
                    raise ValueError("bar_or_factor_missing")
                bars[day] = {"open": op * factor / anchor, "close": cl * factor / anchor}
            result = {"source": "tushare.daily+adj_factor", "adjustment": "qfq_ratio_common_anchor",
                      "fetched_on": fetched_on, "bars": bars}
        except Exception as exc:
            # Only controlled categories/class names are retained, never str(exc).
            reason = str(exc) if type(exc) is ValueError and str(exc) in {
                "credentials_missing", "provider_rejected", "provider_shape", "provider_fields_missing",
                "provider_row_shape", "adjustment_factors_missing", "bar_or_factor_missing",
                "duplicate_bar_conflict", "provider_symbol_mismatch", "provider_date_invalid"
            } else type(exc).__name__
            result = {"bars": {}, "missing_reason": reason}
        self.cache[key] = result
        return result

    @staticmethod
    def _index(rows, symbol, start, end):
        indexed = {}
        for row in rows:
            if row.get("ts_code") != symbol:
                raise ValueError("provider_symbol_mismatch")
            raw = str(row.get("trade_date", ""))
            if len(raw) != 8 or not raw.isdigit():
                raise ValueError("provider_date_invalid")
            day = date.fromisoformat(f"{raw[:4]}-{raw[4:6]}-{raw[6:]}").isoformat()
            if not start <= day <= end:
                continue
            if day in indexed and indexed[day] != row:
                raise ValueError("duplicate_bar_conflict")
            indexed[day] = row
        return indexed


fetch_price_series = PriceSeriesFetcher()


def _measurement(unit, symbol, trade_date, as_of, calendar, fetch_series):
    fields = {"t_plus_5_price": None, "t_plus_5_direction_hit": None,
              "t_plus_5_return_pct": None, "t_plus_5_provenance": None,
              "t_plus_5_evaluated": False, "t_plus_5_missing_reason": None,
              "is_t_plus_5_due": False, "t_plus_5_status": "data_missing"}
    days = trading_days_forward(trade_date, 6, calendar_dates=calendar)
    if len(days) < 5:
        fields["t_plus_5_date"] = None
        fields["t_plus_5_missing_reason"] = "calendar_out_of_range"
        return fields
    t1, t5 = days[0], days[4]
    fields["t_plus_5_date"] = t5
    if t5 > as_of or (t5 == now_cn().date().isoformat() and cn_market_phase() != "post_close"):
        fields["t_plus_5_status"] = "pending_due"
        return fields
    fields["is_t_plus_5_due"] = True
    end = days[5] if len(days) > 5 and days[5] <= as_of else t5
    try:
        data = fetch_series(symbol, trade_date, end)
    except Exception as exc:
        data = {"missing_reason": type(exc).__name__}
    if not isinstance(data, dict) or not all(data.get(k) for k in ("source", "adjustment", "fetched_on")):
        fields["t_plus_5_missing_reason"] = (data or {}).get("missing_reason", "provenance_incomplete") if isinstance(data, dict) else "provenance_incomplete"
        return fields
    try:
        date.fromisoformat(data["fetched_on"])
    except (TypeError, ValueError):
        fields["t_plus_5_missing_reason"] = "provenance_date_invalid"
        return fields
    bars = data.get("bars") or {}
    entry = _positive((bars.get(t1) or {}).get("open"))
    exit_price = _positive((bars.get(t5) or {}).get("close"))
    quotes = {d: b.get("close") for d, b in bars.items() if isinstance(b, dict)}
    if not exit_price and detect_tplus5_suspension(symbol, trade_date, t5, quotes, trading_calendar=calendar):
        fields["t_plus_5_status"] = "suspension"
        fields["is_t_plus_5_due"] = False
        return fields
    if entry is None or exit_price is None:
        fields["t_plus_5_missing_reason"] = "entry_open_missing" if entry is None else "exit_close_missing"
        return fields
    verdict = unit.get("manager_verdict") or (unit.get("investment_debate_state") or {}).get("manager_verdict") or {}
    winner = str(verdict.get("winner") or unit.get("debate_winner") or "").lower().strip()
    change = exit_price - entry
    hit = change > 0 if winner == "bull" else change < 0 if winner == "bear" else abs(change / entry) <= 0.03
    fields.update(t_plus_5_status="due_and_evaluated", t_plus_5_evaluated=True,
                  t_plus_5_price=round(exit_price, 4), t_plus_5_direction_hit=hit,
                  t_plus_5_return_pct=round(change / entry * 100, 2),
                  t_plus_5_provenance={"source": data["source"], "adjustment": data["adjustment"],
                                      "fetched_on": data["fetched_on"], "entry_date": t1,
                                      "exit_date": t5, "entry_price": entry, "exit_price": exit_price})
    return fields


def _stamp_measurement(target, fields):
    target.update(copy.deepcopy(fields))
    metrics = target.setdefault("shadow_credit_metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("invalid shadow_credit_metrics container")
    metrics.update(copy.deepcopy(fields))
    state = target.get("investment_debate_state")
    if isinstance(state, dict):
        state.update(copy.deepcopy(fields))
        sm = state.setdefault("shadow_credit_metrics", {})
        if not isinstance(sm, dict):
            raise ValueError("invalid state metrics container")
        sm.update(copy.deepcopy(fields))


def _count(stats, fields):
    status = fields["t_plus_5_status"]
    name = {"pending_due": "pending_due_count", "suspension": "suspension_count",
            "data_missing": "data_missing_count", "due_and_evaluated": "evaluated_count"}[status]
    stats[name] += 1
    stats["due_count"] += int(fields["is_t_plus_5_due"])
    if fields["t_plus_5_direction_hit"] is not None:
        stats["hit_count" if fields["t_plus_5_direction_hit"] else "miss_count"] += 1


def backfill_report(report, *, as_of, calendar, fetch_series=None):
    """Only T+5 leaves are changed; per-horizon qualification precedes fetch."""
    fetch_series = fetch_series or fetch_price_series
    result = copy.deepcopy(report)
    target = result.get("result_data")
    target = target if isinstance(target, dict) else result
    slots = [(h, target[h + "_term"]) for h in ("short", "medium") if isinstance(target.get(h + "_term"), dict)]
    if not slots:
        slots = [(target.get("horizon") or report.get("horizon") or "unspecified", target)]
    all_stats = {}
    for horizon, unit in slots:
        stats = dict.fromkeys(COUNTERS, 0)
        all_stats[horizon] = stats
        stats["total_scanned"] = 1
        identity = {k: report[k] for k in ("status", "symbol", "trade_date") if report.get(k) is not None}
        candidate = {**identity, **unit}
        if report.get("status") not in (None, "completed") or not is_qualifying_v2_report(candidate):
            stats["skipped_non_qualifying"] = 1
            continue
        stats["qualifying_v2_count"] = 1
        symbol = candidate.get("symbol") or candidate.get("ticker") or candidate.get("company_of_interest")
        td = candidate.get("analysis_baseline_date") or candidate.get("trade_date") or candidate.get("date")
        fields = _measurement(unit, symbol, td, as_of, calendar, fetch_series)
        _stamp_measurement(unit, fields)
        mirror = (target.get("horizons") or {}).get(horizon)
        if isinstance(mirror, dict):
            # Mirror may contain slightly different non-T+5 content; preserve it.
            _stamp_measurement(mirror, fields)
        _count(stats, fields)
    return result, all_stats


def _open_db(path, writable=False):
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{quote(str(p))}?mode={'rw' if writable else 'ro'}", uri=True, timeout=0)
    conn.row_factory = sqlite3.Row
    return conn


def _validate_db(path):
    try:
        with closing(_open_db(path)) as conn:
            count = conn.execute("SELECT count(*) FROM reports WHERE status='completed'").fetchone()[0]
            columns = {row[1] for row in conn.execute('PRAGMA table_info(reports)')}
            if not {'id', 'symbol', 'trade_date', 'status', 'result_data'} <= columns:
                raise RuntimeError('reports columns missing; migration forbidden')
            if not count:
                raise RuntimeError('explicit database has no completed reports')
    except sqlite3.Error as exc:
        raise RuntimeError('cannot read explicit reports database') from exc


def _iter_db(path):
    _validate_db(path)
    # Keyset paging closes each read transaction before any write transaction.
    # A live SELECT cursor across yield would block DELETE-mode SQLite commits.
    last_id = ''
    while True:
        with closing(_open_db(path)) as conn:
            row = conn.execute("SELECT * FROM reports WHERE status='completed' AND id>? ORDER BY id LIMIT 1", (last_id,)).fetchone()
        if row is None:
            return
        last_id = row['id']
        result = dict(row)
        result['result_data'] = json.loads(result['result_data'] or 'null')
        yield result


def _iter_db_raw(path):
    """Same paging as _iter_db; yields full columns plus raw result_data text.

    rowid is aliased to __rowid__ so a hypothetical user column literally named
    "rowid" cannot shadow SQLite's internal row id."""
    _validate_db(path)
    last_id = ''
    while True:
        with closing(_open_db(path)) as conn:
            row = conn.execute(
                "SELECT rowid AS __rowid__, * FROM reports "
                "WHERE status='completed' AND id>? ORDER BY id LIMIT 1",
                (last_id,)).fetchone()
        if row is None:
            return
        last_id = row['id']
        result = dict(row)
        result["rowid"] = row["__rowid__"]
        result["raw"] = result["result_data"]
        result["result_data"] = json.loads(result["result_data"] or 'null')
        yield result


def load_raw_reports(db_path=None, input_file=None, input_dir=None):
    """Compatibility loader for small callers; CLI DB loop streams full rows."""
    if db_path:
        return list(_iter_db(db_path)), (_ReadOnlyContext(), None, None)
    paths = [Path(input_file)] if input_file else sorted(Path(input_dir).glob("*.json")) if input_dir else []
    if not paths:
        raise RuntimeError("explicit --db-path or JSON input required; no golden fallback")
    reports = []
    for p in paths:
        data = json.loads(p.read_text(encoding="utf-8"))
        values = data.get("samples", data.get("reports", [data])) if isinstance(data, dict) else data
        reports.extend(values)
    return reports, None


class _ReadOnlyContext:
    def __exit__(self, *args):
        return None


def check_runtime_guard(db_path, health_url):
    """Fail closed on unknown runtime state. Never restart or wait for idle."""
    import requests
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(health_url.rstrip("/") + "/healthz", timeout=(2, 7))
        payload = response.json()
    except requests.Timeout:
        return {"allowed": False, "reason": "health_busy_timeout"}
    except (requests.RequestException, ValueError):
        return {"allowed": False, "reason": "health_unavailable"}
    finally:
        session.close()
    if response.status_code != 200 or payload.get("status") != "ok":
        return {"allowed": False, "reason": "health_not_ok"}
    # healthz currently does NOT expose the in-memory job store. Absence of DB
    # rows is not proof of zero active jobs. An explicit zero runtime count is
    # required; missing instrumentation must skip rather than race an analysis.
    count = payload.get("active_analysis_count")
    if type(count) is not int or count < 0:
        return {"allowed": False, "reason": "active_analysis_count_unknown"}
    if count:
        return {"allowed": False, "reason": "busy", "running_count": count}
    try:
        with closing(_open_db(db_path)) as conn:
            n = conn.execute("SELECT count(*) FROM reports WHERE status IN ('running','pending','queued')").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "scheduled_analyses" in tables:
                n += conn.execute("SELECT count(*) FROM scheduled_analyses WHERE last_run_status IN ('running','pending','queued')").fetchone()[0]
    except sqlite3.Error:
        return {"allowed": False, "reason": "analysis_count_unavailable"}
    return {"allowed": n == 0, "reason": "idle" if n == 0 else "busy", "running_count": n}


def _write_row(conn, report_id, desired, audit, exported_sha=None):
    """Journal prepared delta before commit; compare exact stored original.

    If `exported_sha` is given (sha256 of the pre-exported result_data for
    this row), the row's *current* stored bytes are hashed inside the
    BEGIN IMMEDIATE transaction; a mismatch means the row changed between
    pre-export and write, so it is skipped as a pre_export_mismatch rather
    than overwritten with a stale backup.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT result_data FROM reports WHERE id=? AND status='completed'", (report_id,)).fetchone()
        if row is None:
            raise ValueError("row disappeared")
        original = row[0]
        if exported_sha is not None:
            current_sha = hashlib.sha256(original.encode("utf-8")).hexdigest()
            if current_sha != exported_sha:
                conn.rollback()
                return "pre_export_mismatch"
        updated = patch_tplus5_json(original, desired)
        if original == updated:
            conn.rollback()
            return False
        old_values = _measurement_delta(json.loads(original), desired)
        record = {"phase": "prepared", "report_id": report_id, "old_values": old_values,
                  "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
                  "after_sha256": hashlib.sha256(updated.encode()).hexdigest()}
        audit.write(json.dumps(record, ensure_ascii=False) + "\n")
        audit.flush(); os.fsync(audit.fileno())
        conn.execute("UPDATE reports SET result_data=? WHERE id=?", (updated, report_id))
        stored = conn.execute("SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()[0]
        if non_tplus5_bytes(original) != non_tplus5_bytes(stored):
            raise ValueError("non-T+5 post-write byte mismatch")
        conn.commit()
        audit.write(json.dumps({"phase": "committed", "report_id": report_id,
                                "after_sha256": record["after_sha256"]}) + "\n")
        audit.flush(); os.fsync(audit.fileno())
        return True
    except Exception:
        conn.rollback()
        raise


def _measurement_delta(original, desired, path=()):
    result = []
    if isinstance(desired, dict):
        before = original if isinstance(original, dict) else {}
        for key, child in desired.items():
            if key in MEASUREMENT_KEYS:
                if key not in before or before[key] != child:
                    result.append({'path': list(path + (key,)), 'existed': key in before,
                                   'value': before.get(key), 'new_value': child})
            else:
                result.extend(_measurement_delta(before.get(key), child, path + (key,)))
    return result


def _is_production(path):
    production = Path("/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db")
    return Path(path).resolve() == production.resolve() or (production.exists() and os.path.samefile(path, production))


# ---------------------------------------------------------------------------
# Independent pre-image export (DAV-1544)
# ---------------------------------------------------------------------------

def _pre_export_stream(path):
    """Return (compressor_stream, raw_file, sha256_of_compressed_bytes) writing zstd frames."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    raw = os.fdopen(fd, "wb")

    class _HashingWriter:
        def __init__(self, sink):
            self._sink = sink
            self.sha = hashlib.sha256()

        def write(self, data):
            self.sha.update(data)
            return self._sink.write(data)

        def flush(self):
            self._sink.flush()

    hashing = _HashingWriter(raw)
    stream = zstandard.ZstdCompressor(level=3).stream_writer(hashing, closefd=False)
    return stream, raw, hashing


def export_pre_images(db_path, planned, *, export_dir=None):
    """Export the exact stored bytes of the rows in `planned`.

    `planned` is the authoritative write list computed by run_backfill's
    read-only pass. This function performs NO measurement recomputation and
    NO price fetches: for each planned report id it re-reads the row's
    current raw result_data bytes and appends them to the export stream.

    Returns {path, sha256, records} where records maps report_id -> exported
    sha256. The compressed file is re-read and its sha256 verified against
    the in-stream hash; any failure raises before the caller may open a
    write transaction, so a failed export means zero writes.
    """
    export_dir = Path(export_dir or PRE_EXPORT_DIR)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
    path = export_dir / (stamp + ".jsonl.zst")
    stream = raw = None
    records = {}
    try:
        stream, raw, hashing = _pre_export_stream(path)

        def emit(obj):
            stream.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

        emit({
            "version": PRE_EXPORT_VERSION, "kind": "header", "db_path": str(db_path),
            "created_at": datetime.now().isoformat(),
        })
        with closing(_open_db(db_path)) as conn:
            for report_id, _desired in planned:
                row = conn.execute(
                    "SELECT rowid AS __rowid__, result_data FROM reports WHERE id=?",
                    (report_id,)).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"planned row {report_id} vanished before pre-export")
                raw_text = row["result_data"]
                raw_bytes = raw_text.encode("utf-8")
                sha = hashlib.sha256(raw_bytes).hexdigest()
                records[report_id] = sha
                emit({
                    "kind": "row", "report_id": report_id,
                    "rowid": row["__rowid__"],
                    "result_data_sha256": sha,
                    "result_data": raw_text,
                })
        emit({"kind": "trailer", "row_count": len(records)})
        stream.close(); stream = None
        raw.flush(); os.fsync(raw.fileno()); raw.close(); raw = None
        file_sha = hashing.sha.hexdigest()
    except Exception:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if raw is not None:
            raw.close()
        try:
            path.unlink()
        except OSError:
            pass
        raise
    # Self-check: the bytes we just fsynced must hash to the same sha256.
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if actual != file_sha:
        try:
            path.unlink()
        except OSError:
            pass
        raise RuntimeError("pre-export sha256 mismatch; refusing to write")
    return {"path": str(path), "sha256": file_sha, "records": records}


def _load_pre_export(path, expected_sha256=None):
    """Decompress a pre-export file, verifying sha256 and the row trailer."""
    p = Path(path)
    blob = p.read_bytes()
    sha = hashlib.sha256(blob).hexdigest()
    if expected_sha256 is not None and sha != expected_sha256:
        raise RuntimeError("pre-export sha256 mismatch; refusing to restore")
    records, trailer = [], None
    with p.open("rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        for line in io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8"):
            rec = json.loads(line)
            if rec.get("kind") == "row":
                records.append(rec)
            elif rec.get("kind") == "trailer":
                trailer = rec
    if trailer is None or trailer.get("row_count") != len(records):
        raise RuntimeError("pre-export trailer missing or row_count mismatch")
    return {"sha256": sha, "records": records}


def _plan_restore_patch(text, node, desired, edits):
    """Like _plan_json_patch but also allows removing T+5 members that a
    backfill added (e.g. keys inside a now-populated shadow_credit_metrics).
    Non-T+5 additions/removals/changes still raise.

    `text` is the raw JSON source; node spans index into it so commas can be
    located exactly without touching string-literal contents."""
    current = node["value"]
    if "members" not in node:
        if current != desired:
            raise ValueError("non-T+5 value changed")
        return
    if not isinstance(desired, dict):
        raise ValueError("non-T+5 container changed")
    members = node["members"]
    added = []
    for key, value in desired.items():
        if key not in members:
            if key not in MEASUREMENT_KEYS and not (
                key == "shadow_credit_metrics" and isinstance(value, dict)
                and set(value) <= MEASUREMENT_KEYS
            ):
                raise ValueError("non-T+5 field added")
            added.append(json.dumps(key) + ":" + json.dumps(value, ensure_ascii=False, allow_nan=False))
            continue
        child = members[key][1]
        if key in MEASUREMENT_KEYS:
            if child["value"] != value:
                edits.append((child["start"], child["end"], json.dumps(value, ensure_ascii=False, allow_nan=False)))
        else:
            _plan_restore_patch(text, child, value, edits)
    # Removals: only T+5 measurement members may disappear (backfill-added).
    # Desired-key iteration order decides which comma to delete: a removed
    # member takes its following comma only when a *surviving* member comes
    # after it; otherwise it takes the preceding comma (trailing-member case).
    order = list(members)
    removed = [k for k in order if k not in desired]
    for key in removed:
        if key not in MEASUREMENT_KEYS:
            raise ValueError("non-T+5 field removed")
    removed_set = set(removed)
    for key in removed:
        idx = order.index(key)
        key_start, child = members[key]
        nxt = next((k for k in order[idx + 1:] if k not in removed_set), None)
        if nxt is not None:
            next_key_start = members[nxt][0]
            comma = text.index(",", child["end"], next_key_start)
            edits.append((key_start, comma + 1, ""))
        else:
            comma = text.rindex(",", node["start"] + 1, key_start)
            edits.append((comma, child["end"], ""))
    if added:
        surviving = len(order) - len(removed)
        prefix = "," if surviving > 0 else ""
        edits.append((node["close"], node["close"], prefix + ",".join(added)))


def _restore_pre_image(original, record):
    """Rewrite measurement members so the whole row equals the exported bytes."""
    record_sha = hashlib.sha256(record["result_data"].encode("utf-8")).hexdigest()
    if record["result_data_sha256"] != record_sha:
        raise ValueError("export record sha256 mismatch")
    pre = json.loads(record["result_data"])
    desired = json.loads(original)
    if without_tplus5(desired) != without_tplus5(pre):
        raise ValueError("non-T+5 content differs from exported pre-image; refusing restore")
    edits = []
    _plan_restore_patch(original, _json_node(original), pre, edits)
    restored = original
    for start, end, replacement in sorted(edits, reverse=True):
        restored = restored[:start] + replacement + restored[end:]
    if restored != record["result_data"]:
        raise ValueError("restored bytes differ from exported pre-image")
    if non_tplus5_bytes(original) != non_tplus5_bytes(restored):
        raise ValueError("non-T+5 byte mismatch during restore")
    return restored


def restore_from_export(db_path, export_file, *, sha256=None,
                        health_url="http://127.0.0.1:8000",
                        production_authorized=False):
    """Rollback subcommand: write exported pre-images back, row-by-row verified.

    Every row is compared before and after: the stored current bytes must still
    differ from (or already equal) the export, the rewritten bytes must be
    byte-identical to the export, and non-T+5 bytes must be unchanged.
    """
    _validate_db(db_path)
    if _is_production(db_path) and (not production_authorized or now_cn().date() <= date(2026, 10, 9)):
        raise RuntimeError("production restore forbidden before signed post-10-09 handoff")
    export = _load_pre_export(export_file, expected_sha256=sha256)
    guard = check_runtime_guard(db_path, health_url)
    if not guard["allowed"]:
        result = {"skipped": True, "guard": guard, "restored_rows": 0}
        print(json.dumps(result, ensure_ascii=False))
        return result
    result = {"restored_rows": 0, "already_current": 0, "missing": 0,
              "mismatched": 0, "conflicts": 0, "guard": guard}
    conn = _open_db(db_path, writable=True)
    try:
        for record in export["records"]:
            report_id = record["report_id"]
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT result_data FROM reports WHERE id=? AND status='completed'",
                    (report_id,)).fetchone()
                if row is None:
                    conn.rollback()
                    result["missing"] += 1
                    continue
                current = row[0]
                if current == record["result_data"]:
                    conn.rollback()
                    result["already_current"] += 1
                    continue
                restored = _restore_pre_image(current, record)
                if restored != record["result_data"]:
                    conn.rollback()
                    result["mismatched"] += 1
                    continue
                conn.execute("UPDATE reports SET result_data=? WHERE id=?",
                             (restored, report_id))
                stored = conn.execute(
                    "SELECT result_data FROM reports WHERE id=?", (report_id,)).fetchone()[0]
                if stored != record["result_data"] or \
                        non_tplus5_bytes(current) != non_tplus5_bytes(stored):
                    raise ValueError("post-restore verification failed")
                conn.commit()
                result["restored_rows"] += 1
            except sqlite3.OperationalError:
                conn.rollback()
                result["conflicts"] += 1
            except Exception:
                conn.rollback()
                result["mismatched"] += 1
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_backfill(*, db_path=None, input_file=None, input_dir=None, output_file=None,
                 as_of=None, dry_run=False, verify_gates=False, audit_log=None,
                 health_url="http://127.0.0.1:8000", production_authorized=False,
                 copy_rehearsal=False, pre_export_dir=None):
    if verify_gates and db_path:
        raise RuntimeError('DB gate verification requires scripts/verify_h1b_gates.py with an explicit cohort')
    as_of = as_of or now_cn().date().isoformat()
    date.fromisoformat(as_of)
    if db_path:
        _validate_db(db_path)
    if db_path and not dry_run:
        production = _is_production(db_path)
        if production and (not production_authorized or now_cn().date() <= date(2026, 10, 9)):
            raise RuntimeError("production write forbidden before signed post-10-09 handoff")
        if production or not copy_rehearsal:
            guard = check_runtime_guard(db_path, health_url)
            if not guard["allowed"]:
                result = {'skipped': True, 'guard': guard, 'changed_rows': 0}
                print(json.dumps(result, ensure_ascii=False))
                return result
    calendar = load_calendar()
    result = {"dry_run": dry_run, "sample_count": 0, "qualifying_count": 0,
              "changed_rows": 0, "guard_mismatches": 0, "write_conflicts": 0,
              "by_horizon": {}, "missing_units": []}
    stats = dict.fromkeys(COUNTERS, 0)
    saved = []

    # Pass 1 (read-only): compute every desired row and all statistics. For the
    # JSON-file path this also materializes `saved` for the output file.
    planned = []
    if db_path:
        source = _iter_db_raw(db_path)
    else:
        source = iter(load_raw_reports(input_file=input_file, input_dir=input_dir)[0])
    for report in source:
        result["sample_count"] += 1
        updated, per_horizon = backfill_report(report, as_of=as_of, calendar=calendar)
        for h, counts in per_horizon.items():
            bucket = result["by_horizon"].setdefault(h, dict.fromkeys(COUNTERS, 0))
            for key in COUNTERS:
                bucket[key] += counts[key]
                stats[key] += counts[key]
            if counts["data_missing_count"]:
                rd = updated.get("result_data", updated)
                u = rd.get(h + "_term", rd)
                result["missing_units"].append({"report_id": report.get("id"), "horizon": h,
                    "reason": u.get("t_plus_5_missing_reason")})
        if db_path and not dry_run:
            if without_tplus5(report["result_data"]) != without_tplus5(updated["result_data"]):
                # Same condition that makes patch_tplus5_json raise inside
                # _write_row; count it once here and skip the write attempt.
                result["guard_mismatches"] += 1
            elif _measurement_delta(report["result_data"], updated["result_data"]):
                planned.append((report["id"], updated["result_data"]))
        elif not db_path:
            saved.append(updated)

    pre_export = None
    journal_path = Path(audit_log or ROOT / "work/tplus5-rollback" / (datetime.now().strftime("%Y%m%dT%H%M%S-%f") + ".jsonl"))
    if db_path and not dry_run:
        # Pass 1.5: the independent pre-image export MUST succeed and self-verify
        # before any write transaction is opened. A failure raises -> zero writes.
        # The export is driven strictly by `planned` (no re-computation).
        pre_export = export_pre_images(db_path, planned, export_dir=pre_export_dir)
        # Requirement 2: the exported report-id set must equal `planned`'s.
        if set(pre_export["records"]) != {rid for rid, _ in planned}:
            raise RuntimeError("pre-export report set mismatch; refusing to write")
        result["pre_export"] = {"path": pre_export["path"], "sha256": pre_export["sha256"],
                                "rows": len(pre_export["records"])}
        result["pre_export_mismatch"] = 0
        writer = _open_db(db_path, writable=True)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        audit = os.fdopen(fd, "a", encoding="utf-8")
        try:
            for report_id, desired in planned:
                try:
                    outcome = _write_row(writer, report_id, desired, audit,
                                         exported_sha=pre_export["records"].get(report_id))
                    if outcome == "pre_export_mismatch":
                        result["pre_export_mismatch"] += 1
                    else:
                        result["changed_rows"] += int(outcome)
                except ValueError:
                    result["guard_mismatches"] += 1
                except sqlite3.OperationalError:
                    result["write_conflicts"] += 1
        finally:
            audit.close()
            writer.close()

    for bucket in [stats, *result["by_horizon"].values()]:
        bucket["completeness_rate"] = bucket["evaluated_count"] / bucket["due_count"] if bucket["due_count"] else 0.0
        bucket["hit_rate"] = bucket["hit_count"] / bucket["evaluated_count"] if bucket["evaluated_count"] else None
    result.update(stats=stats, qualifying_count=stats["qualifying_v2_count"],
                  provider_request_count=getattr(fetch_price_series, "request_count", None))
    if db_path and not dry_run:
        result["audit_log"] = str(journal_path)
    if verify_gates:
        from tradingagents.agents.utils.shadow_credit import (
            evaluate_h1b_system_gates, filter_v2_completed_reports, extract_sample_cohort,
        )
        units = filter_v2_completed_reports(saved)
        cohorts = {}
        for unit in units:
            cohort = extract_sample_cohort(unit)
            key = ':'.join(str(cohort[k] or 'unspecified') for k in (
                'decision_model_version', 'evidence_contract_version', 'price_basis_version', 'horizon'))
            cohorts.setdefault(key, []).append(unit)
        result['gate_evaluations'] = {
            key: evaluate_h1b_system_gates(values, as_of=as_of, trading_calendar=calendar)
            for key, values in cohorts.items()
        }
    if not dry_run and not db_path and (output_file or input_file):
        Path(output_file or input_file).write_text(json.dumps({"samples": saved, "backfill_stats": stats}, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db-path", "input-file", "input-dir", "output-file", "as-of",
                 "audit-log", "pre-export-dir"):
        parser.add_argument("--" + name)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    for name in ("dry-run", "verify-gates", "production-authorized", "copy-rehearsal"):
        parser.add_argument("--" + name, action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=False)
    restore_parser = sub.add_parser(
        "restore", help="restore rows from an independent pre-export file")
    restore_parser.add_argument("--db-path", required=True)
    restore_parser.add_argument("--export-file", required=True)
    restore_parser.add_argument("--export-sha256")
    restore_parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    restore_parser.add_argument("--production-authorized", action="store_true")
    args = vars(parser.parse_args())
    command = args.pop("command")
    args.pop("verbose")
    # Only the signed production invocation may load this machine's .env.
    # Copy rehearsal callers inject explicitly; tests never inherit live keys.
    if args.get('production_authorized'):
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=False)
    try:
        if command == "restore":
            result = restore_from_export(
                args["db_path"], args["export_file"],
                sha256=args.get("export_sha256"), health_url=args["health_url"],
                production_authorized=args.get("production_authorized", False))
            if result.get("mismatched") or result.get("conflicts") or result.get("missing"):
                return 1
            return 0
        args.pop("export_file", None)
        args.pop("export_sha256", None)
        result = run_backfill(**args)
        if result.get('guard_mismatches') or result.get('write_conflicts'):
            return 1
    except Exception as exc:
        print("Backfill refused: " + type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
