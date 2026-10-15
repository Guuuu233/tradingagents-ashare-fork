#!/usr/bin/env python3
"""Read-only, zero-provider B1 monitoring; select daily batches explicitly.

Example (controller runs after deployment, preferably on a SQLite backup):
  env -u PYTHONPATH <locked-python> b1_daily_monitor.py --db snapshot.db \
      --start 2026-10-08 --end 2026-10-09 --ids first-two-batches.json \
      --csv per-horizon.csv

--ids is a JSON array of report ids. No report prose, credentials or personal
configuration is queried. Thresholds are advisory; this script never rolls back.

--input-json records must be self-describing: each record needs ``id``,
``trade_date`` and ``user_id``, and ``user_id`` must equal the fixed monitoring
account (records are filtered on it exactly as --db rows are). This mirrors the
--db path so both entry points enforce the same account boundary.
"""
# (DAV-1904 B-6d)「压缩存储前适用」：read_database 直读明文 reports.result_data，
# 且用 json_extract 拆字段。经 work/_guard.require_plaintext_complete 在连库后
# 守卫：若存在仅剩压缩副本（result_data IS NULL AND result_data_zst IS NOT NULL）
# 的行，则以状态 2 报错退出，避免静默读出 NULL。
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys

# Self-locating bootstrap: work/_guard.py lives at the repo root's work/, so
# walk up from this file until it is found and put that root on sys.path.
# Must happen BEFORE importing work._guard, and must stay after
# `from __future__ import annotations` (DAV-1909 🔴-1: a guard block ahead of
# __future__ turns the module into a SyntaxError and breaks pytest collection).
for _candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
    if (_candidate / "work" / "_guard.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from work._guard import require_plaintext_complete as _b6e_guard  # noqa: E402

ACCOUNT = "429163f7-50b6-4982-8bdf-96ae99506843"
FAILURES = {"violation", "missing_required", "unverifiable"}


def read_database(path, start=None, end=None, ids=None):
    database = Path(path).resolve()
    if not database.is_file():
        raise ValueError("Database does not exist; refusing to create it")
    # Read back user_id so the account filter below is enforced in Python too,
    # not only by the WHERE clause (a future edit to `where` cannot silently
    # widen the read to other accounts).
    columns = ["id", "trade_date", "status", "user_id"]
    projections = []
    for prefix in ("short_term", "medium_term", ""):
        key = f"$.{prefix}." if prefix else "$."
        for field in ("status", "forecast", "price_plan_check"):
            projections.append((prefix, field))
            columns.append(f"json_extract(result_data, '{key}{field}')")
    where, params = ["user_id=?", "json_valid(result_data)"], [ACCOUNT]
    for value, operator in ((start, ">="), (end, "<=")):
        if value:
            date.fromisoformat(value)
            where.append(f"trade_date {operator} ?")
            params.append(value)
    if ids is not None:
        if not ids:
            return []
        where.append("id IN (" + ",".join("?" for _ in ids) + ")")
        params.extend(ids)
    sql = "SELECT " + ",".join(columns) + " FROM reports WHERE " + " AND ".join(where) + " ORDER BY created_at,id"
    records = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        _b6e_guard(connection)
        for row in connection.execute(sql, params):
            if row[3] != ACCOUNT:
                continue  # defence in depth; the WHERE clause already filters
            result = {}
            for (prefix, field), value in zip(projections, row[4:]):
                if value is None:
                    continue
                if field != "status":
                    value = json.loads(value)
                target = result.setdefault(prefix, {}) if prefix else result
                target[field] = value
            records.append({"id": row[0], "trade_date": row[1], "status": row[2], "result_data": result})
    return records


def horizon_units(record):
    result = record.get("result_data") or {}
    nested = [(h, result.get(key)) for h, key in (("short", "short_term"), ("medium", "medium_term"))]
    present = [(h, unit) for h, unit in nested if isinstance(unit, dict) and unit.get("forecast")]
    if present:
        return present
    return [(result.get("horizon", "single"), result)]


def percentage(numerator, denominator):
    return numerator / denominator * 100 if denominator else None


def summarize(records):
    rows = []
    legacy = 0
    for record in records:
        if record.get("user_id") not in (None, ACCOUNT):
            continue
        for horizon, unit in horizon_units(record):
            if unit.get("status") == "not_requested":
                continue
            forecast, check = unit.get("forecast") or {}, unit.get("price_plan_check") or {}
            if forecast.get("contract") != "b1.v1":
                legacy += 1
                continue
            failed = record.get("status") == "failed" or unit.get("status") == "failed"
            original = check.get("original_executable") is True and check.get("original_action") in {"BUY", "SELL"}
            blocked = original and check.get("status") in FAILURES
            invalid = forecast.get("status") != "valid" or blocked
            rows.append({"report_id": record["id"], "analysis_date": record.get("trade_date"),
                         "horizon": horizon, "failed": failed,
                         "forecast_status": forecast.get("status"), "p_rel_t10": forecast.get("p_rel_t10"),
                         "price_plan_status": check.get("status"), "original_action": check.get("original_action"),
                         "original_executable": original, "price_blocked": blocked,
                         "b2_invalid": invalid if not failed else None})
    complete = [row for row in rows if not row["failed"]]
    original = [row for row in complete if row["original_executable"]]
    blocked = sum(row["price_blocked"] for row in original)
    invalid = sum(row["b2_invalid"] for row in complete)
    blocked_rate = percentage(blocked, len(original))
    invalid_rate = percentage(invalid, len(complete))
    summary = {"b1_slices": len(rows), "completed_slices": len(complete),
               "failed_slices": len(rows) - len(complete), "legacy_units_excluded": legacy,
               "price_plan_status_distribution": dict(Counter(row["price_plan_status"] for row in rows)),
               "completed_price_plan_status_distribution": dict(Counter(row["price_plan_status"] for row in complete)),
               "forecast_status_distribution": dict(Counter(row["forecast_status"] for row in complete)),
               "original_executable_buy_sell": len(original), "price_blocked": blocked,
               "price_blocked_pct": blocked_rate, "b2_invalid": invalid, "b2_invalid_pct": invalid_rate,
               "threshold_review": {
                   "price_blocked_over_20pct": blocked_rate > 20 if blocked_rate is not None else None,
                   "b2_invalid_over_5pct": invalid_rate > 5 if invalid_rate is not None else None,
                   "b2_at_least_100_completed_slices": len(complete) >= 100,
               },
               "decision": "controller_decision_required; no automatic rollback"}
    return summary, rows


def self_test():
    valid = {"contract": "b1.v1", "status": "valid", "p_rel_t10": 60}
    bad = {"contract": "b1.v1", "status": "malformed", "p_rel_t10": None}
    failed = {"contract": "b1.v1", "status": "node_failed", "p_rel_t10": None}
    records = [
        {"id": "synthetic-dual", "trade_date": "2026-10-08", "status": "completed", "result_data": {
            "forecast": valid,
            "short_term": {"status": "completed", "forecast": valid, "price_plan_check": {"status": "ok", "original_action": "BUY", "original_executable": True}},
            "medium_term": {"status": "completed", "forecast": bad, "price_plan_check": {"status": "violation", "original_action": "SELL", "original_executable": True}},
        }},
        {"id": "synthetic-single", "trade_date": "2026-10-09", "status": "completed", "result_data": {"forecast": bad, "price_plan_check": {"status": "not_applicable", "original_action": "WAIT", "original_executable": False}}},
        {"id": "synthetic-failed", "trade_date": "2026-10-09", "status": "failed", "result_data": {
            "short_term": {"status": "failed", "forecast": failed, "price_plan_check": {"status": "not_applicable"}},
            "medium_term": {"status": "failed", "forecast": failed, "price_plan_check": {"status": "not_applicable"}},
        }},
    ]
    summary, rows = summarize(records)
    assert len(rows) == 5 and summary["completed_slices"] == 3 and summary["failed_slices"] == 2
    assert summary["price_blocked_pct"] == 50 and summary["b2_invalid"] == 2  # union, not double-counting
    assert summary["threshold_review"]["price_blocked_over_20pct"] is True
    assert summary["threshold_review"]["b2_invalid_over_5pct"] is True
    assert summary["threshold_review"]["b2_at_least_100_completed_slices"] is False
    assert summarize([])[0]["b2_invalid_pct"] is None
    return {"synthetic_checks": "passed", "model_calls": 0, "provider_calls": 0, "database_reads": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db")
    parser.add_argument("--input-json")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--ids")
    parser.add_argument("--csv")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    if bool(args.db) == bool(args.input_json):
        parser.error("choose exactly one of --db / --input-json")
    ids = json.loads(Path(args.ids).read_text()) if args.ids else None
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(value, str) for value in ids)):
        parser.error("--ids must contain a JSON array of report id strings")
    records = read_database(args.db, args.start, args.end, ids) if args.db else json.loads(Path(args.input_json).read_text())
    if not isinstance(records, list):
        parser.error("--input-json must contain a JSON array of report records")
    for row in records:
        if not isinstance(row, dict):
            parser.error("--input-json must contain a JSON array of report objects")
    if not args.db:
        records = [row for row in records if (not ids or row.get("id") in ids)
                   and (not args.start or row.get("trade_date", "") >= args.start)
                   and (not args.end or row.get("trade_date", "") <= args.end)
                   and row.get("user_id") == ACCOUNT]
    summary, rows = summarize(records)
    if args.csv and rows:
        with Path(args.csv).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
