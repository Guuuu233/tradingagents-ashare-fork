#!/usr/bin/env python3
"""DAV-1528 (rework v2): daily post-batch compressed snapshot of the production SQLite DB.

Creates one zstd-compressed snapshot per CN trading day under a fixed
directory (default ``/private/tmp/ta-snapshot/``). Intended to run once per
trading day after the analysis batch finishes; non-trading days exit early
without writing anything.

Read-only cards open ``<out_dir>/current.db`` directly
(``file:<path>?mode=ro&immutable=1``) — no copying. Only write/drill cards
decompress a dated ``.zst`` into their own temp path and delete it when done.

Discipline (D-073 / rework order):
- The production DB is only ever read through ``sqlite3.Connection.backup``;
  the script never writes to it.
- Retention: keep the newest ``RETENTION_KEEP`` (=3) ``tradingagents-*.db.zst``
  files; older matching files are deleted automatically and every deletion is
  recorded in the output JSON.
- After each snapshot the script refreshes ``<out_dir>/current.db``: writes a
  temp file, verifies ``PRAGMA quick_check``, then atomically renames it into
  place with mode ``0444``. ``current.db`` counts toward the D-073 limit of 3
  uncompressed full-DB copies on the machine.
- The last snapshot of each trading week doubles as the weekly baseline that
  DAV-1508 uploads to Quark (2 remote copies kept there); DAV-1508 no longer
  makes its own full-DB backup.
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
CURRENT_DB_NAME = "current.db"
ZSTD_LEVEL = 19  # max regular level; ~5 GB live DB compresses well below 1 GB
# Keep the newest 3 dated snapshots; older tradingagents-*.db.zst files in
# out_dir are deleted automatically (deletions are reported, never silent).
RETENTION_KEEP = 3


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


def refresh_current_db(out_dir: str) -> dict:
    """Decompress the newest ``tradingagents-*.db.zst`` into ``current.db``.

    Writes to a temp file, verifies ``PRAGMA quick_check``, then atomically
    renames over ``current.db`` and chmods it ``0444`` so readers cannot
    accidentally mutate the shared artifact.
    """
    snaps = sorted(
        f for f in os.listdir(out_dir)
        if f.startswith("tradingagents-") and f.endswith(".db.zst")
    )
    if not snaps:
        raise RuntimeError(f"no tradingagents-*.db.zst found in {out_dir}")
    newest = os.path.join(out_dir, snaps[-1])
    final = os.path.join(out_dir, CURRENT_DB_NAME)

    fd, tmp = tempfile.mkstemp(prefix=".ta-current-", suffix=".db", dir=out_dir)
    os.close(fd)
    try:
        subprocess.run(
            [_find_zstd(), "-d", "-f", newest, "-o", tmp],
            check=True, capture_output=True, text=True,
        )
        # Static decompressed file -> immutable=1 (project rule: no WAL
        # sidecars; mode=ro would create stray -shm/-wal on the temp file).
        conn = sqlite3.connect(f"file:{tmp}?immutable=1", uri=True)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"quick_check failed on decompressed {newest}: {result}")
        os.chmod(tmp, 0o444)
        os.replace(tmp, final)   # atomic swap; readers never see a partial file
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return {"current_db": final, "source": snaps[-1],
            "bytes": os.path.getsize(final)}


def apply_retention(out_dir: str) -> list[str]:
    """Delete all but the newest ``RETENTION_KEEP`` dated snapshots.

    Only files matching ``tradingagents-*.db.zst`` inside ``out_dir`` are ever
    removed. Returns the deleted filenames for the JSON report.
    """
    snaps = sorted(
        f for f in os.listdir(out_dir)
        if f.startswith("tradingagents-") and f.endswith(".db.zst")
    )
    excess = snaps[:-RETENTION_KEEP] if len(snaps) > RETENTION_KEEP else []
    deleted = []
    for name in excess:
        os.remove(os.path.join(out_dir, name))
        deleted.append(name)
    return deleted


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
    current = refresh_current_db(args.out_dir)
    deleted = apply_retention(args.out_dir)
    remaining = sorted(
        f for f in os.listdir(args.out_dir)
        if f.startswith("tradingagents-") and f.endswith(".db.zst")
    )
    report = {
        "date": day.isoformat(),
        "trading_day": True,
        "snapshot": info["snapshot"],
        "skipped": info["skipped"],
        "snapshot_bytes": info["bytes"],
        "current_db": current["current_db"],
        "current_db_source": current["source"],
        "current_db_bytes": current["bytes"],
        "retention_keep": RETENTION_KEEP,
        "deleted": deleted,
        "total_snapshots": len(remaining),
        "snapshots": remaining,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
