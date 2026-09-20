#!/usr/bin/env python3
"""H1b gate sample fill: run diversified v2 analyses (bear-rebalance pool).

Request-level only: v2_debate_enabled=true.
Does NOT change persistent max_debate_rounds / max_risk_discuss_rounds (must stay 3/1).
Does NOT enable credit_weighting_enabled.
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
# One batch toward H1b N>=60 / side balance; pool is bear_compensate.
TARGET_NEW = 10
TRADE_DATE = "2026-08-25"
# From generate_weekly_target_pool --bull-samples 20 --bear-samples 3 (ex 茅台/宁德)
QUEUE = [
    ("601398.SH", "银行"),
    ("600941.SH", "通信"),
    ("601857.SH", "石油石化"),
    ("601318.SH", "非银金融"),
    ("601088.SH", "煤炭"),
    ("601985.SH", "公用事业"),
    ("600048.SH", "房地产"),
    ("601919.SH", "交通运输"),
    ("600019.SH", "钢铁"),
    ("600309.SH", "基础化工"),
]
LOG = Path(__file__).resolve().parent / "h1b-sample-fill.log"
POOL_META = Path(__file__).resolve().parent / "evaluations" / "week_sample_fill_pool_20260825.json"


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
        "config_overrides": {
            "v2_debate_enabled": True,
            # Soft hint for downstream; core path still fail-closed without industry column fill.
            "sample_fill_industry": industry,
        },
    }
    r = requests.post(f"{BASE}/v1/analyze", headers=headers, json=body, timeout=60)
    if r.status_code >= 400:
        # Retry without unknown override if allowlist rejects it
        if "sample_fill_industry" in r.text or r.status_code == 422:
            body["config_overrides"] = {"v2_debate_enabled": True}
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
                "rebalance": "bear_compensate",
                "historical_bull_bear": [20, 3],
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
        n = v2_on_date_count(con, TRADE_DATE)
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
