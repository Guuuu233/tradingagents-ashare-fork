"""DAV-1346 零 LLM 重放：R2 整段连坐收窄（raw 侧坐标触发词）的验收重放。

语料（验收 1）：
- 46 档价格门拦截语料：work/hist-bench-20260926/audit-corpus-pricegate.txt
  （生产库只读 result_data，pool=None——两侧实现同口径重放，与 DAV-1312
  审计的 violation 基线做翻转对比）；
- DAV-1222 冻结 50 份 state：/Users/davidliu/Documents/TradingAgents-AShare/
  work/dav1223-b0-audit/ext/（pool 由 snapshots + manifest 重建，沿用
  DAV-1225 corrected pool 口径，与 work/dav1246/recompute.py 一致）；
- 09-25 以后日常报告：reports.created_at >= '2026-09-25'（pool=None 两侧同口径）。

用法（仓库根目录，锁定解释器）：
    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
        work/dav1346/recompute.py --impl baseline|candidate

baseline = `git show 46db65499177c5db2eea99cbfb26ac2f6e3b90b5:tradingagents/agents/utils/price_ref_registry.py`
（派工基线 tip）；candidate = 工作区实现。gate 两侧共用主干 evaluate_price_basis_gate
（本卡不改 gate 判定规则）。

输出：work/dav1346/out/{impl}.json；两侧跑完后由 compare.py 生成 diff
汇总（翻转为通过/新增拦截档位、逐 violation kind 计数）。

本卡只改 registry Pass 4 的 R2 触发池（raw 侧需自身句子带坐标语境），
gate 判定不动，两侧共用工作区 evaluate_price_basis_gate。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pickle
import sqlite3
import subprocess
import sys
import types
from typing import Any, Dict, List, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "work", "dav1223-b0-audit"))

from corrected.pool import build_pool  # noqa: E402
from tradingagents.agents.utils.price_basis_gate import (  # noqa: E402
    evaluate_price_basis_gate,
)

PROD_REPO = "/Users/davidliu/Documents/TradingAgents-AShare"
AUDIT_ROOT = os.path.join(PROD_REPO, "work", "dav1223-b0-audit")
EXT = os.path.join(AUDIT_ROOT, "ext")
MANIFEST = os.path.join(AUDIT_ROOT, "snapshot_manifest.json")
PROD_DB = os.path.join(PROD_REPO, "data", "tradingagents.db")
CORPUS_46 = os.path.join(REPO, "work", "hist-bench-20260926", "audit-corpus-pricegate.txt")
BASELINE_GIT_PATH = (
    "46db65499177c5db2eea99cbfb26ac2f6e3b90b5"
    ":tradingagents/agents/utils/price_ref_registry.py"
)
OUT = os.path.join(HERE, "out")

REPORT_FIELDS = (
    "market_report", "volume_price_report",
    "news_report", "fundamentals_report", "sentiment_report", "macro_report",
    "smart_money_report", "game_theory_report",
    "investment_plan", "trader_investment_plan", "final_trade_decision",
)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_impl(name: str):
    if name == "candidate":
        import tradingagents.agents.utils.price_ref_registry as m
        return m
    src = subprocess.check_output(
        ["git", "show", BASELINE_GIT_PATH], cwd=REPO, text=True)
    m = types.ModuleType("registry_baseline")
    sys.modules["registry_baseline"] = m
    exec(compile(src, BASELINE_GIT_PATH, "exec"), m.__dict__)
    return m


def collect_reports(h: Mapping[str, Any]) -> Dict[str, Any]:
    return {name: h.get(name) for name in REPORT_FIELDS}


def run_one(impl, h: Mapping[str, Any], pool) -> Dict[str, Any]:
    reports = collect_reports(h)
    cutoff = h.get("trade_date")
    result = impl.build_price_ref_registry(reports, cutoff=cutoff, pool=pool)
    st2 = {
        "trade_date": cutoff,
        "price_refs": result["price_refs"],
        "price_basis_validation": result["validation"],
    }
    for f in ("investment_plan", "trader_investment_plan", "final_trade_decision"):
        st2[f] = reports.get(f)
    gate = evaluate_price_basis_gate(st2)
    return {
        "status": gate["status"],
        "violations": [
            {"kind": v.get("kind"), "source": v.get("source"),
             "detail": (v.get("detail") or "")[:160], "ref_ids": v.get("ref_ids")}
            for v in gate["violations"]
        ],
        "refs": [
            {k: r.get(k) for k in (
                "ref_id", "value", "basis", "source", "provenance",
                "as_of", "context", "sentence")}
            for r in result["price_refs"]
        ],
    }


def iter_db_horizons(con, report_id: str, horizons: List[str]):
    row = con.execute(
        "select symbol, trade_date, result_data from reports where id=?",
        (report_id,)).fetchone()
    if not row or not row[2]:
        return
    sym, td, rd = row[0], row[1], json.loads(row[2])
    for hor in horizons:
        h = rd.get(hor)
        if isinstance(h, dict) and h:
            yield hor, sym, td, h


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["baseline", "candidate"], required=True)
    args = ap.parse_args()
    impl = load_impl(args.impl)
    os.makedirs(OUT, exist_ok=True)

    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")

    runs: Dict[str, Any] = {}

    # ---- 语料 1：46 档被拦档位 ----
    corpus = [l.strip().split(":") for l in open(CORPUS_46) if l.strip()]
    for i, (rid, horizon, src) in enumerate(corpus):
        for hor, sym, td, h in iter_db_horizons(con, rid, [horizon]):
            key = f"corpus46:{i}:{rid[:8]}:{hor}"
            runs[key] = run_one(impl, h, None)
            runs[key]["symbol"] = sym
            runs[key]["trade_date"] = td
            runs[key]["grp"] = src

    # ---- 语料 2：DAV-1222 冻结 50 份（snapshot 重建 pool）----
    manifest = json.load(open(MANIFEST))
    snap_files = sorted(glob.glob(os.path.join(EXT, "snapshots", "snapshots", "*.pkl")))
    state_files = sorted(glob.glob(os.path.join(EXT, "states", "states", "*.pkl")))
    pools = {}
    for p in snap_files:
        sample = os.path.basename(p).split("_")[0]
        info = manifest["samples"][sample]
        pools[sample] = build_pool(sample, info["symbol"], info["trade_date"],
                                   pickle.load(open(p, "rb")))
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        sample = tag.split("__")[0]
        blob = pickle.load(open(p, "rb"))
        st = blob["final_state"] if isinstance(blob, dict) and "final_state" in blob else blob
        h = {name: st.get(name) for name in REPORT_FIELDS}
        h["trade_date"] = st.get("trade_date")
        for sub_key in ("short_term", "medium_term", "result_data"):
            sub = st.get(sub_key)
            if isinstance(sub, Mapping):
                for name in REPORT_FIELDS:
                    if h.get(name) in (None, ""):
                        h[name] = sub.get(name)
        runs[f"frozen50:{tag}"] = run_one(impl, h, pools[sample])

    # ---- 语料 3：09-25 以后日常报告（双侧 pool=None 同口径）----
    daily_ids = [r[0] for r in con.execute(
        "select id from reports where created_at>='2026-09-25' order by created_at")]
    for rid in daily_ids:
        for hor, sym, td, h in iter_db_horizons(con, rid, ["short_term", "medium_term"]):
            key = f"daily0925:{rid[:8]}:{hor}"
            runs[key] = run_one(impl, h, None)
            runs[key]["symbol"] = sym
            runs[key]["trade_date"] = td

    con.close()

    path = os.path.join(OUT, f"{args.impl}.json")
    with open(path, "w") as fh:
        json.dump({"impl": args.impl, "runs": runs}, fh, ensure_ascii=False, indent=1)
    n_blocked = sum(1 for r in runs.values() if r["status"] == "blocked")
    n_viol = sum(len(r["violations"]) for r in runs.values())
    print(f"{args.impl}: runs={len(runs)} blocked={n_blocked} violations={n_viol} -> {path}")


if __name__ == "__main__":
    main()
