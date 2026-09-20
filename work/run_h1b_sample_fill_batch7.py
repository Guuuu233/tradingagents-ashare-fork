#!/usr/bin/env python3
"""H1b sample fill batch 7: fill free slots on proven bear-winner trade dates.

Batch6 ran prior bear symbols on neutral late-Aug dates → mostly tie/bull,
bear count stuck ~14–16. Batch7 concentrates on dates that already produced
multiple bear winners (esp. 2026-08-24), with unused liquid symbols.

Request-level only: v2_debate_enabled=true. No credit_weighting. Keep 3/1.
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
if not DB.exists():
    DB = Path("/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db")
EMAIL = "davidliu022305@gmail.com"
UID = "429163f7-50b6-4982-8bdf-96ae99506843"
TARGET_NEW = 20
# Free slots on 2026-08-24 (6 prior bears that day) + overflow to 08-19/08-21.
QUEUE = [
    ("601138.SH", "计算机", "2026-08-24"),
    ("688981.SH", "电子", "2026-08-24"),
    ("002371.SZ", "电子", "2026-08-24"),
    ("603501.SH", "电子", "2026-08-24"),
    ("600011.SH", "公用事业", "2026-08-24"),
    ("688012.SH", "电子", "2026-08-24"),
    ("300308.SZ", "通信", "2026-08-24"),
    ("002475.SZ", "电子", "2026-08-24"),
    ("000725.SZ", "电子", "2026-08-24"),
    ("002415.SZ", "电子", "2026-08-24"),
    ("300750.SZ", "电力设备", "2026-08-24"),
    ("601088.SH", "煤炭", "2026-08-24"),
    ("600036.SH", "银行", "2026-08-24"),
    ("601318.SH", "非银金融", "2026-08-24"),
    ("000001.SZ", "银行", "2026-08-24"),
    ("000333.SZ", "家用电器", "2026-08-24"),
    ("600519.SH", "食品饮料", "2026-08-24"),
    ("002241.SZ", "电子", "2026-08-24"),
    ("300015.SZ", "医药生物", "2026-08-19"),
    ("601012.SH", "电力设备", "2026-08-19"),
]
LOG = Path(__file__).resolve().parent / "h1b-sample-fill-batch7.log"
POOL_META = Path(__file__).resolve().parent / "evaluations" / "week_sample_fill_pool_batch7_bear_dates.json"


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
        raise RuntimeError(
            f"analyze {symbol}@{trade_date}: HTTP {r.status_code} {r.text[:500]}"
        )
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


def _db_job_status(job_id: str) -> str | None:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("select status from reports where id=?", (job_id,)).fetchone()
    con.close()
    return row[0] if row else None


def poll(headers: dict, job_id: str, symbol: str, timeout_s: int = 7200) -> str:
    t0 = time.time()
    last = None
    consecutive_404 = 0
    while time.time() - t0 < timeout_s:
        try:
            r = requests.get(f"{BASE}/v1/jobs/{job_id}", headers=headers, timeout=30)
        except requests.RequestException as exc:
            log(f"poll transport error {job_id}: {exc}; retry")
            time.sleep(10)
            continue
        if r.status_code == 404:
            consecutive_404 += 1
            db_st = _db_job_status(job_id)
            log(f"poll 404 {job_id} db_status={db_st} n404={consecutive_404}")
            if db_st in ("completed", "failed"):
                return db_st
            if consecutive_404 >= 6:
                return db_st or "failed"
            time.sleep(10)
            continue
        consecutive_404 = 0
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
    db_st = _db_job_status(job_id)
    log(f"poll timeout {job_id} db_status={db_st}; treating as failed")
    return db_st if db_st in ("completed", "failed") else "failed"


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


def count_winners(con: sqlite3.Connection) -> tuple[int, int, int]:
    bull = bear = tie = 0
    for (rd,) in con.execute(
        "select result_data from reports where status='completed' "
        "and result_data like '%v2_structured_disagreement%'"
    ):
        try:
            data = json.loads(rd) if isinstance(rd, str) else rd
        except Exception:
            continue
        w = str((data.get("manager_verdict") or {}).get("winner") or "").lower()
        if w == "bull":
            bull += 1
        elif w == "bear":
            bear += 1
        elif w == "tie":
            tie += 1
    return bull, bear, tie


def main() -> int:
    if "--fresh-log" in sys.argv:
        LOG.write_text("", encoding="utf-8")
    target = TARGET_NEW
    for i, arg in enumerate(sys.argv):
        if arg == "--target-new" and i + 1 < len(sys.argv):
            target = int(sys.argv[i + 1])

    POOL_META.parent.mkdir(parents=True, exist_ok=True)
    POOL_META.write_text(
        json.dumps(
            {
                "target_new": target,
                "strategy": "fill_free_slots_on_proven_bear_dates",
                "queue": [
                    {"symbol": s, "industry": i, "trade_date": d} for s, i, d in QUEUE
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log(f"waiting for idle before batch7 target_new={target}")
    wait_until_idle()
    hz = wait_for_healthz()
    log(f"healthz commit={hz.get('commit_sha')} threads={hz.get('executor_threads')}")
    token = login()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    assert_31(headers, "pre")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n0 = v2_count(con)
    bull0, bear0, tie0 = count_winners(con)
    con.close()
    log(f"start v2_total={n0} winners bull/bear/tie={bull0}/{bear0}/{tie0}")

    submitted = 0
    for symbol, industry, trade_date in QUEUE:
        pair = f"{symbol}|{trade_date}"
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        skip = skip_pairs(con)
        con.close()
        if submitted >= target:
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
        bull, bear, tie = count_winners(con)
        win = None
        row = con.execute("select result_data from reports where id=?", (job_id,)).fetchone()
        if row and row[0]:
            try:
                win = str(
                    (json.loads(row[0]).get("manager_verdict") or {}).get("winner") or ""
                ).lower()
            except Exception:
                win = "?"
        con.close()
        log(
            f"finished {pair} status={st} winner={win} "
            f"v2_total={n} bull/bear/tie={bull}/{bear}/{tie}"
        )
        if st != "completed":
            log(f"WARN failed {pair}; continue")
        time.sleep(3)

    assert_31(headers, "post")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n = v2_count(con)
    bull, bear, tie = count_winners(con)
    con.close()
    log(f"done batch7 submitted={submitted} v2_total={n} bull/bear/tie={bull}/{bear}/{tie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
