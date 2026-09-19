"""Tushare Gateway Availability Sampling Runner (DAV-1097 Card 2).

Performs repeated sampling across 5 Tushare API endpoints:
- income
- balancesheet
- cashflow
- daily_basic
- daily

Records latency, success/failure status, row count, and failure categorization.
Ensures zero token exposure: tokens are loaded from .env and never printed/logged.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path("/Users/davidliu/Documents/TradingAgents-AShare")
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from tradingagents.dataflows.providers.industry_linkage_provider import _query_tushare_api

TARGET_APIS = ["income", "balancesheet", "cashflow", "daily_basic", "daily"]
TEST_TS_CODE = "600036.SH"
ROUNDS = 25
DELAY_BETWEEN_CALLS = 0.3
DELAY_BETWEEN_ROUNDS = 1.5

OUTPUT_JSON = PROJECT_ROOT / "work" / "2026-09-19-tushare-sampling-raw.json"


def classify_failure(err_cat: str, err_note: str) -> str:
    """Classify failure according to card requirements:

    timeout / TLS EOF / 连接重置 / 限流 / 其他
    """
    if not err_cat and not err_note:
        return "None"

    note_lower = (err_note or "").lower()
    cat_lower = (err_cat or "").lower()

    if "timeout" in cat_lower or "timeout" in note_lower or "超时" in note_lower:
        return "timeout"
    if "eof" in note_lower or "ssl" in note_lower:
        return "TLS EOF"
    if "reset" in note_lower or "connection reset" in note_lower or "重置" in note_lower:
        return "连接重置"
    if "rate" in cat_lower or "429" in cat_lower or "rate" in note_lower or "限频" in note_lower:
        return "限流"
    if "403" in cat_lower or "permission" in note_lower or "权限" in note_lower:
        return "权限不足"
    return "其他"


def main():
    print(f"[{datetime.now().isoformat()}] Starting Tushare gateway sampling: {ROUNDS} rounds x {len(TARGET_APIS)} APIs...")
    print(f"Target stock: {TEST_TS_CODE}")
    print(f"Endpoints: {TARGET_APIS}")

    results = []

    for round_idx in range(1, ROUNDS + 1):
        round_start_time = time.time()
        print(f"\n--- Round {round_idx}/{ROUNDS} (Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")

        for api in TARGET_APIS:
            t0 = time.perf_counter()
            call_ts = datetime.now(timezone.utc).isoformat()
            success = False
            row_count = 0
            err_cat = None
            err_note = None

            try:
                df, err_cat, err_note = _query_tushare_api(api, ts_code=TEST_TS_CODE)
                elapsed = time.perf_counter() - t0
                if df is not None:
                    success = True
                    row_count = len(df)
                    err_cat = None
                    err_note = None
                else:
                    success = False
                    row_count = 0
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                success = False
                err_cat = "exception"
                err_note = str(exc)

            failure_type = "None" if success else classify_failure(err_cat, err_note)

            record = {
                "round": round_idx,
                "api": api,
                "ts_code": TEST_TS_CODE,
                "timestamp_utc": call_ts,
                "elapsed_sec": round(elapsed, 4),
                "success": success,
                "row_count": row_count,
                "error_category": err_cat,
                "failure_type": failure_type,
                # Sanitize error note so no token or private URL query can leak
                "error_note": (err_note[:200] if err_note else None),
            }
            results.append(record)

            status_str = f"SUCCESS ({row_count} rows)" if success else f"FAIL ({failure_type}: {err_cat})"
            print(f"  [{api:13s}] {status_str} in {elapsed:.3f}s")

            time.sleep(DELAY_BETWEEN_CALLS)

        # Save checkpoint after each round
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
            json.dump({"total_rounds": ROUNDS, "records": results}, f, indent=2, ensure_ascii=False)

        if round_idx < ROUNDS:
            time.sleep(DELAY_BETWEEN_ROUNDS)

    print(f"\n[{datetime.now().isoformat()}] Sampling completed successfully. Saved to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
