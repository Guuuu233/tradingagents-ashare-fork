#!/usr/bin/env python3
"""Fill DAV-421 sample gap: run v2 analyses until completed v2 count >= 10.

Uses request-level config_overrides only: v2_debate_enabled=true.
Does NOT change persistent max_debate_rounds / max_risk_discuss_rounds.
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
TARGET = 10
# Already have: 000858.SZ / 000063.SZ / 000651.SZ
QUEUE = [
    "000001.SZ",
    "000333.SZ",
    "000725.SZ",
    "002415.SZ",
    "600036.SH",
    "600519.SH",
    "601318.SH",
]
TRADE_DATE = "2026-08-25"
LOG = Path(__file__).resolve().parent / "v2-sample-fill.log"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def v2_completed_count(con: sqlite3.Connection) -> int:
    return int(
        con.execute(
            "select count(*) from reports where status='completed' "
            "and result_data like '%v2_structured_disagreement%'"
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


def submit(headers: dict, symbol: str) -> str:
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
    log(f"submitted {symbol} job={job_id}")
    return job_id


def wait_for_healthz(timeout_s: int = 120) -> dict:
    """Tolerate uvicorn restarts; do not die on a single 5s read timeout."""
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
        time.sleep(15)
    raise TimeoutError(job_id)


def existing_v2_or_inflight_symbols(con: sqlite3.Connection) -> set[str]:
    done = {
        r[0]
        for r in con.execute(
            "select symbol from reports where status='completed' "
            "and result_data like '%v2_structured_disagreement%'"
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
    # Append on resume; truncate only when explicitly requested.
    if "--fresh-log" in sys.argv:
        LOG.write_text("", encoding="utf-8")
    hz = wait_for_healthz()
    log(f"healthz commit={hz.get('commit_sha')}")
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    assert_31(headers, "pre")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = v2_completed_count(con)
    inflight = find_inflight_job(con)
    skip = existing_v2_or_inflight_symbols(con)
    con.close()
    log(f"start v2_completed={n} target={TARGET} skip={sorted(skip)}")

    if inflight:
        job_id, symbol = inflight
        log(f"resume poll existing {symbol} job={job_id}")
        st = poll(headers, job_id, symbol)
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "select status, length(result_data), "
            "instr(coalesce(result_data,''), 'v2_structured_disagreement') "
            "from reports where id=?",
            (job_id,),
        ).fetchone()
        n = v2_completed_count(con)
        con.close()
        log(f"finished {symbol} job_status={st} db_row={row} v2_completed={n}")

    for symbol in QUEUE:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = v2_completed_count(con)
        skip = existing_v2_or_inflight_symbols(con)
        con.close()
        if n >= TARGET:
            log(f"reached target {n}>={TARGET}, stop")
            break
        if symbol in skip:
            log(f"skip {symbol} already done/inflight")
            continue
        job_id = submit(headers, symbol)
        st = poll(headers, job_id, symbol)
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "select status, length(result_data), "
            "instr(coalesce(result_data,''), 'v2_structured_disagreement') "
            "from reports where id=?",
            (job_id,),
        ).fetchone()
        n = v2_completed_count(con)
        con.close()
        log(f"finished {symbol} job_status={st} db_row={row} v2_completed={n}")
        if st != "completed":
            log(f"WARN failed analysis {symbol}; continue")
        time.sleep(2)

    assert_31(headers, "post")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = v2_completed_count(con)
    con.close()
    log(f"DONE v2_completed={n}")
    return 0 if n >= TARGET else 2


if __name__ == "__main__":
    sys.exit(main())
