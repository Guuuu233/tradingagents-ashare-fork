#!/usr/bin/env python3
"""Retry 5 analyses that failed with 502 during network outage (2026-08-27)."""
from __future__ import annotations

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
QUEUE = [
    ("601668.SH", "建筑装饰", "2026-08-18"),
    ("603501.SH", "电子", "2026-06-09"),
    ("688256.SH", "电子", "2026-06-01"),
    ("688012.SH", "电子", "2026-05-22"),
    ("688036.SH", "电子", "2026-05-14"),
]
LOG = Path(__file__).resolve().parent / "h1b-sample-fill-retry502.log"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_until_idle(poll_s: int = 20, timeout_s: int = 7200) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "select symbol, trade_date, status from reports "
            "where status in ('pending','running') order by created_at asc limit 1"
        ).fetchone()
        con.close()
        if not row:
            return
        log(f"waiting inflight {row[0]}@{row[1]} ({row[2]})")
        time.sleep(poll_s)
    raise TimeoutError("inflight timeout")


def pair_done(con: sqlite3.Connection, symbol: str, trade_date: str) -> bool:
    row = con.execute(
        "select status from reports where symbol=? and trade_date=? "
        "and status='completed' and result_data like '%v2_structured_disagreement%' "
        "order by created_at desc limit 1",
        (symbol, trade_date),
    ).fetchone()
    return bool(row)


def login() -> str:
    r = requests.post(f"{BASE}/v1/auth/request-code", json={"email": EMAIL}, timeout=30)
    r.raise_for_status()
    code = r.json().get("dev_code")
    r = requests.post(
        f"{BASE}/v1/auth/verify-code",
        json={"email": EMAIL, "code": code},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def submit(headers: dict, symbol: str, trade_date: str) -> str:
    body = {
        "symbol": symbol,
        "trade_date": trade_date,
        "horizons": ["short"],
        "config_overrides": {"v2_debate_enabled": True},
    }
    r = requests.post(f"{BASE}/v1/analyze", headers=headers, json=body, timeout=60)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    log(f"submitted {symbol}@{trade_date} job={job_id}")
    return job_id


def poll(headers: dict, job_id: str, symbol: str, timeout_s: int = 7200) -> str:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        r = requests.get(f"{BASE}/v1/jobs/{job_id}", headers=headers, timeout=30)
        st = r.json().get("status")
        if st != last:
            log(f"  {symbol} {job_id[:8]} status={st} elapsed={time.time()-t0:.0f}s")
            last = st
        if st in ("completed", "failed"):
            return st
        time.sleep(20)
    raise TimeoutError(job_id)


def main() -> int:
    if "--fresh-log" in sys.argv:
        LOG.write_text("", encoding="utf-8")
    log("retry502 start; wait for idle")
    wait_until_idle()
    r = requests.get(f"{BASE}/healthz", timeout=30)
    r.raise_for_status()
    log(f"healthz ok commit={r.json().get('commit_sha')}")
    token = login()
    headers = {"Authorization": f"Bearer {token}"}
    ok = fail = skip = 0
    for symbol, industry, trade_date in QUEUE:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        if pair_done(con, symbol, trade_date):
            con.close()
            log(f"skip {symbol}@{trade_date} already completed v2")
            skip += 1
            continue
        con.close()
        job_id = submit(headers, symbol, trade_date)
        st = poll(headers, job_id, symbol)
        if st == "completed":
            ok += 1
        else:
            fail += 1
            log(f"WARN still failed {symbol}@{trade_date}")
        time.sleep(3)
    log(f"done retry502 ok={ok} fail={fail} skip={skip}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
