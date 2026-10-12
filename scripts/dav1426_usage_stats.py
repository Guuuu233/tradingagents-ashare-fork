"""DAV-1426: llm_call_logs usage statistics with extreme-row exclusion.

Read-only. Aggregates llm_call_logs per model/role while dropping rows whose
captured usage cannot be real, and labels the affected window instead of
silently merging it into the totals.

Exclusion rule (any match drops the row from token sums; call counts still
count it):
  - usage_unreliable = 1            (new writes flag themselves, DAV-1426)
  - prompt_tokens > 2,000,000       (same bound applied to legacy rows, which
                                     predate the flag and can never be legit)

Affected-window annotation:
  - deepseek-* rows created between 2026-10-01 00:00 and 2026-10-02 23:59
    (local DB time) are additionally labeled "usage unreliable" in the
    report: the provider sent cumulative per-chunk usage during that window
    and per-chunk summation inflated the captured values. History is NOT
    rewritten — the label is a reporting-layer annotation only.

Usage (repo root, locked interpreter):
    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
        scripts/dav1426_usage_stats.py [--db PATH] [--out PATH]

Defaults:
    --db   <repo>/data/tradingagents.db  (opened sqlite mode=ro; never written)
    --out  stdout only; with --out the JSON report is also written to PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DEFAULT_DB = os.path.join(REPO, "data", "tradingagents.db")

# Same bound as api.usage_logging.USAGE_UNRELIABLE_THRESHOLD. Legacy rows
# predate the flag column, so the numeric check must be applied here too.
UNRELIABLE_PROMPT_THRESHOLD = 2_000_000

# Window in which deepseek-* models emitted cumulative per-chunk usage
# (DAV-1426 diagnosis: 2026-10-01 21:22 → 10-02 06:21 observed; the day
# bounds cover the full binding window conservatively). Stored as inclusive
# date strings matching the DB's local-naive created_at format.
DEEPSEEK_UNRELIABLE_SINCE = "2026-10-01"
DEEPSEEK_UNRELIABLE_UNTIL = "2026-10-02"


def _connect(db_path: str) -> sqlite3.Connection:
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _is_deepseek(model: str | None) -> bool:
    return bool(model) and "deepseek" in model.lower()


def collect_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_call_logs)")}
    has_flag = "usage_unreliable" in cols

    rows: List[sqlite3.Row] = conn.execute(
        """
        SELECT id, model_name, agent_name, created_at,
               prompt_tokens, completion_tokens, total_tokens,
               cached_prompt_tokens, reasoning_tokens
        {flag}
        FROM llm_call_logs
        """.format(flag=", usage_unreliable" if has_flag else "")
    ).fetchall()

    per_model: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, Any]] = []
    unreliable_window_rows = 0
    unreliable_window_ids: List[str] = []

    for r in rows:
        model = r["model_name"] or "(unknown)"
        entry = per_model.setdefault(
            model,
            {
                "calls": 0,
                "excluded_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_prompt_tokens": 0,
                "reasoning_tokens": 0,
            },
        )
        entry["calls"] += 1

        flagged = has_flag and r["usage_unreliable"]
        over_threshold = (
            r["prompt_tokens"] is not None
            and r["prompt_tokens"] > UNRELIABLE_PROMPT_THRESHOLD
        )
        in_window = (
            _is_deepseek(r["model_name"])
            and r["created_at"] is not None
            and DEEPSEEK_UNRELIABLE_SINCE <= str(r["created_at"])[:10] <= DEEPSEEK_UNRELIABLE_UNTIL
        )

        if in_window:
            unreliable_window_rows += 1
            unreliable_window_ids.append(r["id"])

        reason = None
        if flagged:
            reason = "usage_unreliable_flag"
        elif over_threshold:
            reason = "prompt_tokens>2M"
        elif in_window:
            reason = "deepseek_cumulative_usage_window_10-01~10-02"

        if reason is not None:
            entry["excluded_calls"] += 1
            excluded.append(
                {
                    "id": r["id"],
                    "model": model,
                    "created_at": str(r["created_at"]),
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "reason": reason,
                }
            )
            continue

        for k in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cached_prompt_tokens",
            "reasoning_tokens",
        ):
            v = r[k]
            if v is not None:
                entry[k] += v

    return {
        "db": None,  # filled by caller
        "threshold": UNRELIABLE_PROMPT_THRESHOLD,
        "unreliable_window": {
            "models": "deepseek-*",
            "from": DEEPSEEK_UNRELIABLE_SINCE,
            "until": DEEPSEEK_UNRELIABLE_UNTIL,
            "rows_in_window": unreliable_window_rows,
            "row_ids_in_window": unreliable_window_ids,
            "label": (
                "usage unreliable — cumulative per-chunk usage was summed "
                "(DAV-1426); token sums for these rows are excluded from "
                "aggregates and must not be trusted"
            ),
        },
        "total_rows": len(rows),
        "excluded_rows": excluded,
        "excluded_count": len(excluded),
        "by_model": dict(sorted(per_model.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB, help="sqlite path (opened ro)")
    ap.add_argument("--out", default=None, help="optional JSON output path")
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        report = collect_stats(conn)
    finally:
        conn.close()
    report["db"] = os.path.abspath(args.db)

    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
