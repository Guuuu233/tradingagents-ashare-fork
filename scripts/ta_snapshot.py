#!/usr/bin/env python3
"""DAV-1528: daily post-batch compressed snapshot of the production SQLite DB.

Creates one zstd-compressed snapshot per CN trading day under a fixed
directory (default ``/private/tmp/ta-snapshot/``). Intended to run once per
trading day after the analysis batch finishes; non-trading days exit early
without writing anything. Cards that need production data decompress a
snapshot on demand and delete the copy when done — they no longer copy the
live DB themselves.

Discipline (D-073 / issue body):
- The production DB is only ever read through ``sqlite3.Connection.backup``
  (same API the VACUUM INTO path uses); the script never writes to it.
- Snapshots accumulate; the script reports how many exist but NEVER deletes
  any — when the count exceeds ``RETENTION_KEEP`` it only flags
  ``over_retention`` in the JSON report; retention is fixed in the first
  report and overflow is cleaned manually by a human.
- Nothing here is installed or scheduled; see
  ``docs/ta-snapshot-usage.md`` for the proposed launchd template.

Usage:
    python scripts/ta_snapshot.py [--db data/tradingagents.db]
                                  [--out-dir /private/tmp/ta-snapshot]
                                  [--date YYYY-MM-DD]   # override, testing
                                  [--force-weekday]     # skip calendar check
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

DEFAULT_OUT_DIR = "/private/tmp/ta-snapshot"
ZSTD_LEVEL = 19  # max regular level; ~5 GB live DB compresses well below 1 GB
# Retention fixed in the first report: keep the newest 14 daily snapshots
# (~3 trading weeks). The script NEVER deletes; counts above this are only
# flagged as over_retention for manual cleanup.
RETENTION_KEEP = 14


def _is_trading_day(d: date, force_weekday: bool) -> bool:
    """CN trading-day check via the project's own trade calendar.

    Fail-closed by default (``is_cn_trading_day`` raises
    TradeCalendarUnavailableError when the calendar cannot be loaded).
    ``--force-weekday`` degrades to the Mon–Fri rule — visible in the log and
    meant for manual runs, not for the scheduled job.
    """
    from tradingagents.dataflows.trade_calendar import is_cn_trading_day

    return is_cn_trading_day(d.isoformat(), allow_weekday_fallback=force_weekday)


def _find_zstd() -> str:
    """Locate a zstd binary; PATH first, then the Homebrew cellar path."""
    from shutil import which

    zstd = which("zstd") or (
        "/opt/homebrew/bin/zstd" if os.path.exists("/opt/homebrew/bin/zstd") else None
    )
    if not zstd:
        raise RuntimeError(
            "zstd not found on PATH or /opt/homebrew/bin — install with `brew install zstd`"
        )
    return zstd


def make_snapshot(db_path: str, out_dir: str, day: date) -> dict:
    """Backup ``db_path`` to a temp file, zstd it into ``out_dir``, return stats."""
    os.makedirs(out_dir, exist_ok=True)
    name = f"tradingagents-{day.isoformat()}.db.zst"
    final = os.path.join(out_dir, name)
    if os.path.exists(final):
        # Same trading day re-run: keep the first snapshot, do not overwrite.
        return {"snapshot": final, "skipped": "already_exists",
                "bytes": os.path.getsize(final)}

    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # Temp file on the same filesystem as out_dir so os.replace is atomic.
    fd, tmp = tempfile.mkstemp(prefix=".ta-snap-", suffix=".db", dir=out_dir)
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)      # consistent read of a live WAL database
        finally:
            dst.close()
            src.close()
        subprocess.run(
            [_find_zstd(), f"-{ZSTD_LEVEL}", "-T0", "-f", "--rm", tmp],
            check=True, capture_output=True, text=True,
        )
        compressed = tmp + ".zst"
        os.replace(compressed, final)
    except Exception:
        for leftover in (tmp, tmp + ".zst"):
            if os.path.exists(leftover):
                os.remove(leftover)
        raise
    return {"snapshot": final, "skipped": None, "bytes": os.path.getsize(final)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.path.join(_REPO_ROOT, "data", "tradingagents.db"),
                    help="path to the live SQLite DB (read-only)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    ap.add_argument("--date", default=None,
                    help="YYYY-MM-DD override (default: today, CN)")
    ap.add_argument("--force-weekday", action="store_true",
                    help="weekday fallback when the trade calendar is unavailable")
    args = ap.parse_args()

    if args.date:
        day = date.fromisoformat(args.date)
    else:
        from tradingagents.dataflows.trade_calendar import now_cn
        day = now_cn().date()

    if not _is_trading_day(day, args.force_weekday):
        print(json.dumps({"date": day.isoformat(), "trading_day": False,
                          "action": "no_snapshot"}))
        return 0

    info = make_snapshot(args.db, args.out_dir, day)
    existing = sorted(
        f for f in os.listdir(args.out_dir)
        if f.startswith("tradingagents-") and f.endswith(".db.zst")
    )
    over = existing[:-RETENTION_KEEP] if len(existing) > RETENTION_KEEP else []
    report = {
        "date": day.isoformat(),
        "trading_day": True,
        "snapshot": info["snapshot"],
        "skipped": info["skipped"],
        "snapshot_bytes": info["bytes"],
        "total_snapshots": len(existing),
        "retention_keep": RETENTION_KEEP,
        "over_retention": over,  # listed for manual cleanup; script never deletes
        "snapshots": existing,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
