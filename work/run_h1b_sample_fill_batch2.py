#!/usr/bin/env python3
"""H1b sample fill batch 2: bear_tilt-only queue on 2026-08-24.

Batch 1 picked mostly defensive (高股息) names under bear_compensate; all 9
completed runs still got manager winner=bull. This batch uses structural
bear_tilt sectors (光伏/锂电/CXO/白酒/地产) on the trade date that already
produced bear winners (000858, 000063).

Request-level only: v2_debate_enabled=true. Does NOT enable credit_weighting.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DB = Path(__file__).resolve().parents[1] / "data" / "tradingagents.db"
EMAIL = "davidliu022305@gmail.com"
UID = "429163f7-50b6-4982-8bdf-96ae99506843"
TARGET_NEW = 10
TRADE_DATE = "2026-08-24"
# bear_tilt only; diverse industries; excludes blacklist and prior v2 symbols
QUEUE = [
    ("603288.SH", "食品饮料"),
    ("300015.SZ", "医药生物"),
    ("600585.SH", "建筑材料"),
    ("601012.SH", "电力设备"),
    ("000002.SZ", "房地产"),
    ("002460.SZ", "有色金属"),
    ("603259.SH", "医药生物"),
    ("002304.SZ", "食品饮料"),
    ("600438.SH", "电力设备"),
    ("300122.SZ", "医药生物"),
]
LOG = Path(__file__).resolve().parent / "h1b-sample-fill-batch2.log"
POOL_META = Path(__file__).resolve().parent / "evaluations" / "week_sample_fill_pool_20260824_bear_tilt.json"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def v2_on_date_count(con: sqlite3.Connection, trade_date: str) -> int:
    return int(
        con.execute(
            "select count(*) from reports where status='completed' "
            "and trade_date=? "
            "and result_data like '%v2_structured_disagreement%'",
            (trade_date,),
        ).fetchone()[0]
    )


def login() -> str:
    r = requests.post(f"{BASE}/v1/auth/request-code", json={"email": EMAIL}, timeout=30)
    r.raise_for_status()
    code = r.json().get("dev_code")
    if not code:
        raise RuntimeError("no dev_code from request-code")
    r = requests.post(
        f"{BASE}/v1/auth/verify-code",
        json={"email": EMAIL, "code": code},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    assert data["user"]["id"] == UID, data["user"]
    return data["access_token"]


def assert_31(headers: dict, label: str) -> None:
    r = requests.get(f"{BASE}/v1/config", headers=headers, timeout=30)
    r.raise_for_status()
    cfg = r.json()
    d, risk = cfg.get("max_debate_rounds"), cfg.get("max_risk_discuss_rounds")
    log(f"{label} persistent rounds debate={d} risk={risk}")
    if d != 3 or risk != 1:
        raise RuntimeError(f"3/1 broken at {label}: {d}/{risk}")


def submit(headers: dict, symbol: str, industry: str) -> str:
    body = {
        "symbol": symbol,
        "trade_date": TRADE_DATE,
        "horizons": ["short"],
        "config_overrides": {"v2_debate_enabled": True},
    }
    r = requests.post(f"{BASE}/v1/analyze", headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"analyze {symbol}: HTTP {r.status_code} {r.text[:500]}")
    job_id = r.json()["job_id"]
    log(f"submitted {symbol} industry={industry} job={job_id}")
    return job_id


def wait_for_healthz(timeout_s: int = 120) -> dict:
    t0 = time.time()
    last_err: Exception | None = None
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(f"{BASE}/healthz", timeout=30)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last_err = exc
            log(f"healthz not ready: {exc}; retry")
            time.sleep(3)
    raise RuntimeError(f"healthz unavailable after {timeout_s}s: {last_err}")


def poll(headers: dict, job_id: str, symbol: str, timeout_s: int = 7200) -> str:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(f"{BASE}/v1/jobs/{job_id}", headers=headers, timeout=30)
        except requests.RequestException as exc:
            log(f"poll transport error {job_id}: {exc}; retry")
            time.sleep(10)
            continue
        if r.status_code >= 400:
            log(f"poll error {job_id}: HTTP {r.status_code}")
            time.sleep(10)
            continue
        st = r.json().get("status")
        if st != last:
            log(f"  {symbol} {job_id[:8]} status={st} elapsed={time.time()-t0:.0f}s")
            last = st
        if st in ("completed", "failed"):
            return st
        time.sleep(20)
    raise TimeoutError(job_id)


def skip_symbols(con: sqlite3.Connection) -> set[str]:
    done = {
        r[0]
        for r in con.execute(
            "select symbol from reports where status='completed' "
            "and trade_date=? "
            "and result_data like '%v2_structured_disagreement%'",
            (TRADE_DATE,),
        )
    }
    inflight = {
        r[0]
        for r in con.execute(
            "select symbol from reports where status in ('pending','running')"
        )
    }
    return done | inflight


def find_inflight_job(con: sqlite3.Connection) -> tuple[str, str] | None:
    row = con.execute(
        "select id, symbol from reports where status in ('pending','running') "
        "order by created_at asc limit 1"
    ).fetchone()
    return (row[0], row[1]) if row else None


def main() -> int:
    if "--fresh-log" in sys.argv:
        LOG.write_text("", encoding="utf-8")
    POOL_META.parent.mkdir(parents=True, exist_ok=True)
    POOL_META.write_text(
        json.dumps(
            {
                "trade_date": TRADE_DATE,
                "target_new": TARGET_NEW,
                "queue": [{"symbol": s, "industry": i} for s, i in QUEUE],
                "strategy": "bear_tilt_only",
                "rationale": "Batch1 defensive names all bull; use structural bear_tilt on date with prior bear winners",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    hz = wait_for_healthz()
    log(f"healthz commit={hz.get('commit_sha')}")
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    assert_31(headers, "pre")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n0 = v2_on_date_count(con, TRADE_DATE)
    inflight = find_inflight_job(con)
    skip = skip_symbols(con)
    con.close()
    log(f"start v2_on_{TRADE_DATE}={n0} target_new={TARGET_NEW} skip={sorted(skip)}")

    if inflight:
        job_id, symbol = inflight
        log(f"resume poll existing {symbol} job={job_id}")
        st = poll(headers, job_id, symbol)
        log(f"resumed {symbol} status={st}")

    submitted = 0
    for symbol, industry in QUEUE:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        skip = skip_symbols(con)
        con.close()
        if submitted >= TARGET_NEW:
            log(f"batch target reached submitted={submitted}")
            break
        if symbol in skip:
            log(f"skip {symbol} already done/inflight for {TRADE_DATE}")
            continue
        job_id = submit(headers, symbol, industry)
        submitted += 1
        st = poll(headers, job_id, symbol)
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = v2_on_date_count(con, TRADE_DATE)
        row = con.execute(
            "select status, length(result_data), "
            "instr(coalesce(result_data,''), 'v2_structured_disagreement') "
            "from reports where id=?",
            (job_id,),
        ).fetchone()
        con.close()
        log(f"finished {symbol} job_status={st} db_row={row} v2_on_date={n}")
        if st != "completed":
            log(f"WARN failed analysis {symbol}; continue")
        time.sleep(2)

    assert_31(headers, "post")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = v2_on_date_count(con, TRADE_DATE)
    con.close()
    log(f"done batch submitted={submitted} v2_on_{TRADE_DATE}={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
