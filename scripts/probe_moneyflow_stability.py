#!/usr/bin/env python3
"""Read-only stability probe for Tushare moneyflow_dc / moneyflow_ths.

DAV-1138 extension of the DAV-1097 gateway sampling: each probe call goes
through the real ``CnAkshareProvider._fetch_tushare_api_records`` path so the
recorded ``failure_category`` / ``attempts`` / ``retry_exhausted`` values are
exactly what a production report would persist.

Guarantees:
- read-only: never writes reports, the DB, or any provider/user config;
- never prints or logs the token;
- a single probe success says nothing about historical availability — the
  report only describes what was observed at probe time.

Usage:
    python scripts/probe_moneyflow_stability.py \
        --pair 600905:2026-08-14 --pair 300760:2026-08-14 --rounds 5 \
        [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

APIS = ("moneyflow_dc", "moneyflow_ths")
CN_TZ = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _sina_retrieved_at(provider) -> str:
    try:
        return provider._sina_retrieved_at()
    except Exception:
        return _now_iso()


def _probe_once(provider, api_name, token, symbol, trade_date, retrieved_at, now_fn):
    started = time.monotonic()
    try:
        records, error, category, attempts = provider._fetch_tushare_api_records(
            api_name, token, symbol, trade_date, retrieved_at
        )
    except Exception as exc:  # probe must never crash the sampler
        return {
            "probe_time": now_fn(),
            "symbol": symbol,
            "trade_date": trade_date,
            "api": api_name,
            "success": False,
            "failure_category": "provider_error",
            "error": f"probe_exception:{type(exc).__name__}",
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "attempts": None,
            "retry_exhausted": None,
        }
    latency_ms = round((time.monotonic() - started) * 1000, 1)
    retry_exhausted = (
        bool(error)
        and attempts is not None
        and attempts >= getattr(
            provider, "_TUSHARE_MAX_ATTEMPTS", _max_attempts()
        )
        and _max_attempts() > 1
    )
    return {
        "probe_time": now_fn(),
        "symbol": symbol,
        "trade_date": trade_date,
        "api": api_name,
        "success": bool(records) and not error,
        "failure_category": category if error else None,
        "error": error,
        "latency_ms": latency_ms,
        "attempts": attempts,
        "retry_exhausted": retry_exhausted if error else False,
        "record_count": len(records or []),
    }


def _max_attempts() -> int:
    from tradingagents.dataflows.providers import cn_akshare_provider

    return cn_akshare_provider._TUSHARE_FUND_FLOW_MAX_ATTEMPTS


def run_probe(provider, pairs, *, rounds=1, now_fn=None) -> dict:
    """Run ``rounds`` probe rounds over ``[(symbol, trade_date), ...]``.

    Pure read-only sampling; returns a JSON-serializable report dict.
    """
    now_fn = now_fn or _now_iso
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    records: list[dict] = []

    if not token:
        for round_index in range(rounds):
            for symbol, trade_date in pairs:
                for api_name in APIS:
                    records.append(
                        {
                            "probe_time": now_fn(),
                            "symbol": symbol,
                            "trade_date": trade_date,
                            "api": api_name,
                            "success": False,
                            "failure_category": "token_missing",
                            "error": "tushare.%s:token_missing" % api_name,
                            "latency_ms": 0.0,
                            "attempts": 0,
                            "retry_exhausted": False,
                            "round": round_index,
                        }
                    )
    else:
        for round_index in range(rounds):
            for symbol, trade_date in pairs:
                retrieved_at = _sina_retrieved_at(provider)
                for api_name in APIS:
                    record = _probe_once(
                        provider,
                        api_name,
                        token,
                        symbol,
                        trade_date,
                        retrieved_at,
                        now_fn,
                    )
                    record["round"] = round_index
                    records.append(record)

    summary = _summarize(records)
    return {
        "probe": "moneyflow_stability",
        "read_only": True,
        "generated_at": now_fn(),
        "rounds": rounds,
        "pairs": [{"symbol": s, "trade_date": d} for s, d in pairs],
        "records": records,
        "summary": summary,
    }


def _summarize(records: list[dict]) -> dict:
    per_api: dict[str, dict] = {
        api: {"attempted": 0, "successes": 0, "failures": 0}
        for api in APIS
    }
    transient_categories: dict[str, int] = {}
    round_keyed: dict[tuple, set] = {}
    for record in records:
        api = record["api"]
        per_api[api]["attempted"] += 1
        if record["success"]:
            per_api[api]["successes"] += 1
        else:
            per_api[api]["failures"] += 1
            cat = record.get("failure_category") or "unknown"
            transient_categories[cat] = transient_categories.get(cat, 0) + 1
        key = (record["symbol"], record["trade_date"], record.get("round"))
        round_keyed.setdefault(key, set()).add(record["success"])

    dual_failure = sum(1 for v in round_keyed.values() if v == {False})
    single_failure = sum(1 for v in round_keyed.values() if v == {True, False})
    all_ok = sum(1 for v in round_keyed.values() if v == {True})
    for api, stats in per_api.items():
        attempted = stats["attempted"]
        stats["success_rate"] = (
            round(stats["successes"] / attempted, 4) if attempted else None
        )
    return {
        **per_api,
        "dual_source_failure_rounds": dual_failure,
        "single_source_failure_rounds": single_failure,
        "both_ok_rounds": all_ok,
        "transient_failure_categories": transient_categories,
    }


def _parse_pair(text: str) -> tuple[str, str]:
    symbol, _, date = text.partition(":")
    if not symbol or not date:
        raise argparse.ArgumentTypeError(
            f"--pair must be SYMBOL:TRADE_DATE, got {text!r}"
        )
    return symbol, date


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair",
        action="append",
        type=_parse_pair,
        help="SYMBOL:TRADE_DATE to probe (repeatable)",
    )
    parser.add_argument(
        "--symbols",
        default="600905,300760,600585",
        help="comma-separated symbols used with --trade-date when --pair absent",
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="trade date applied to --symbols (default: latest CN date)",
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", default=None, help="write JSON report here")
    args = parser.parse_args(argv)

    pairs = args.pair
    if not pairs:
        trade_date = args.trade_date
        if not trade_date:
            from tradingagents.dataflows.trade_calendar import cn_today_str

            trade_date = cn_today_str()
        pairs = [(s.strip(), trade_date) for s in args.symbols.split(",") if s.strip()]

    from tradingagents.dataflows.providers.cn_akshare_provider import (
        CnAkshareProvider,
    )

    report = run_probe(CnAkshareProvider(), pairs, rounds=args.rounds)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
