#!/usr/bin/env python3
"""H1b sample fill batch 4: TMT/tech on historical trade dates (May–Jul 2026).

Historical dates chosen so T+5 has already elapsed by late Aug 2026, enabling
later calibration / accuracy review. All runs use v2_debate_enabled=true.

Waits for any in-flight analysis (e.g. batch3) before submitting new jobs.
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
TARGET_NEW = 12
# TMT_GROWTH cluster; trade dates are CN trading days in May–Jul 2026
QUEUE = [
    ("601138.SH", "计算机", "2026-07-30"),
    ("688981.SH", "电子", "2026-07-22"),
    ("002475.SZ", "电子", "2026-07-14"),
    ("002371.SZ", "电子", "2026-07-06"),
    ("000063.SZ", "通信", "2026-06-26"),
    ("300308.SZ", "通信", "2026-06-17"),
    ("603501.SH", "电子", "2026-06-09"),
    ("688256.SH", "电子", "2026-06-01"),
    ("688012.SH", "电子", "2026-05-22"),
    ("688036.SH", "电子", "2026-05-14"),
    ("300433.SZ", "电子", "2026-05-06"),
    ("002241.SZ", "电子", "2026-05-28"),
]
LOG = Path(__file__).resolve().parent / "h1b-sample-fill-batch4.log"
POOL_META = Path(
    __file__
).resolve().parent / "evaluations" / "week_sample_fill_pool_batch4_tmt_hist.json"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def v2_count(con: sqlite3.Connection) -> int:
    return int(
        con.execute(
            "select count(*) from reports where status='completed' "
            "and result_data like '%v2_structured_disagreement%'"
        ).fetchone()[0]
    )


def wait_until_idle(poll_s: int = 30, timeout_s: int = 86400) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = con.execute(
            "select id, symbol, trade_date, status from reports "
            "where status in ('pending','running') order by created_at asc limit 1"
        ).fetchone()
        con.close()
        if not row:
            return
        log(f"waiting for inflight {row[1]}@{row[2]} status={row[3]}")
        time.sleep(poll_s)
    raise TimeoutError("inflight analysis did not finish within timeout")


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


def submit(headers: dict, symbol: str, industry: str, trade_date: str) -> str:
    body = {
        "symbol": symbol,
        "trade_date": trade_date,
        "horizons": ["short"],
        "config_overrides": {"v2_debate_enabled": True},
    }
    r = requests.post(f"{BASE}/v1/analyze", headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"analyze {symbol}@{trade_date}: HTTP {r.status_code} {r.text[:500]}")
    job_id = r.json()["job_id"]
    log(f"submitted {symbol} date={trade_date} industry={industry} job={job_id}")
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


def skip_pairs(con: sqlite3.Connection) -> set[str]:
    done = {
        f"{r[0]}|{r[1]}"
        for r in con.execute(
            "select symbol, trade_date from reports where status='completed' "
            "and result_data like '%v2_structured_disagreement%'"
        )
    }
    inflight = {
        f"{r[0]}|{r[1]}"
        for r in con.execute(
            "select symbol, trade_date from reports where status in ('pending','running')"
        )
    }
    return done | inflight


def main() -> int:
    if "--fresh-log" in sys.argv:
        LOG.write_text("", encoding="utf-8")
    POOL_META.parent.mkdir(parents=True, exist_ok=True)
    POOL_META.write_text(
        json.dumps(
            {
                "target_new": TARGET_NEW,
                "strategy": "tmt_historical_may_jul_2026",
                "queue": [
                    {"symbol": s, "industry": i, "trade_date": d} for s, i, d in QUEUE
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("waiting for any in-flight job before batch4")
    wait_until_idle()

    hz = wait_for_healthz()
    log(f"healthz commit={hz.get('commit_sha')}")
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    assert_31(headers, "pre")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n0 = v2_count(con)
    skip = skip_pairs(con)
    con.close()
    log(f"start v2_total={n0} target_new={TARGET_NEW}")

    submitted = 0
    for symbol, industry, trade_date in QUEUE:
        pair = f"{symbol}|{trade_date}"
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        skip = skip_pairs(con)
        con.close()
        if submitted >= TARGET_NEW:
            log(f"batch target reached submitted={submitted}")
            break
        if pair in skip:
            log(f"skip {pair} already done/inflight")
            continue
        job_id = submit(headers, symbol, industry, trade_date)
        submitted += 1
        st = poll(headers, job_id, symbol)
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = v2_count(con)
        row = con.execute(
            "select status, length(result_data), "
            "instr(coalesce(result_data,''), 'v2_structured_disagreement') "
            "from reports where id=?",
            (job_id,),
        ).fetchone()
        con.close()
        log(f"finished {pair} job_status={st} db_row={row} v2_total={n}")
        if st != "completed":
            log(f"WARN failed {pair}; continue")
        time.sleep(3)

    assert_31(headers, "post")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = v2_count(con)
    con.close()
    log(f"done batch4 submitted={submitted} v2_total={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
