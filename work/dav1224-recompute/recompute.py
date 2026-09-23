"""DAV-1224 验收1/2：候选 trunk 代码对 50 份冻结 state 零 LLM 重算。

用法（仓库根目录，项目锁定解释器）：
    env -u PYTHONPATH .venv310/bin/python work/dav1224-recompute/recompute.py \
        --audit-root /path/to/dav1223-b0-audit

输入：DAV-1222 冻结附件本地副本（10 snapshot + 50 state pkl，按 D-040 不入库，
以 SHA256 记录）。snapshot 仅用于构造 [C5] 桥接源（stock_data/indicators），
等价于生产路径 collected pool 挂入 state 的字段——零 LLM、零外部请求。

输出：out/recompute.json + out/SUMMARY.md
对齐口径：W4 预期 6 pass（s01__r4、s03__r3、s05__r2、s05__r4、s10__r3、s10__r4）；
坏锚 b188060f fixture + s06 r1–r5 全部 blocked。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pickle
import sys
from collections import Counter
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tradingagents.agents.utils.price_ref_registry import (  # noqa: E402
    PRICE_REF_SOURCE_KEY,
    REPORT_FIELDS,
    audit_price_ref_registry,
)
from tradingagents.agents.utils.price_basis_gate import (  # noqa: E402
    evaluate_price_basis_gate,
)

EXTRA_STATE_KEYS = (
    "trade_date", "trade_action", "decision_status",
    "short_term", "medium_term", "result_data",
    "short_term_result", "medium_term_result",
    "instrument_context", "market_data_context", "company_of_interest",
)

EXPECTED_PASS = {"s01__r4", "s03__r3", "s05__r2", "s05__r4", "s10__r3", "s10__r4"}


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(path: str) -> Dict[str, Any]:
    blob = pickle.load(open(path, "rb"))
    st = blob["final_state"] if isinstance(blob, dict) and "final_state" in blob else blob
    new = {f: st.get(f) for f in REPORT_FIELDS}
    for k in EXTRA_STATE_KEYS:
        if k in st:
            new[k] = st[k]
    return new


def run_candidate(state: Dict[str, Any], source: Dict[str, Any] | None) -> Dict[str, Any]:
    """候选 trunk 路径：audit + evaluate（不经 what-if 副本）。"""
    if source is not None:
        # 等价于生产 finalize_price_ref_state(state, collected_pool) 的挂接。
        state[PRICE_REF_SOURCE_KEY] = source
    audit_price_ref_registry(state)
    gate = evaluate_price_basis_gate(state)
    gate["n_refs"] = len(state.get("price_refs") or [])
    gate["pool_bridge"] = state.get("price_ref_pool_bridge") or []
    return gate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-root", required=True,
                    help="包含 ext/、snapshot_manifest.json 的 DAV-1223 B0 目录")
    args = ap.parse_args()
    root = os.path.abspath(args.audit_root)
    ext = os.path.join(root, "ext")
    manifest = json.load(open(os.path.join(root, "snapshot_manifest.json")))

    snap_files = sorted(glob.glob(os.path.join(ext, "snapshots", "snapshots", "*.pkl")))
    state_files = sorted(glob.glob(os.path.join(ext, "states", "states", "*.pkl")))

    sha_ok = True
    for p in snap_files:
        sample = os.path.basename(p).split("_")[0]
        expect = manifest["samples"].get(sample, {}).get("snapshot_sha256")
        sha_ok &= (expect == sha256(p))
    state_sha = {os.path.basename(p): sha256(p) for p in state_files}

    sources: Dict[str, Dict[str, Any]] = {}
    for p in snap_files:
        sample = os.path.basename(p).split("_")[0]
        info = manifest["samples"][sample]
        snap = pickle.load(open(p, "rb"))
        sources[sample] = {
            "stock_data": str(snap.get("stock_data") or ""),
            "indicators": snap.get("indicators") or {},
            "price_basis": snap.get("price_basis"),
            "symbol": info["symbol"],
            "trade_date": info["trade_date"],
        }

    runs = []
    kind_tot = Counter()
    states: Dict[str, Dict[str, Any]] = {}
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        sample = tag.split("__")[0]
        st = load_state(p)
        states[tag] = st
        g = run_candidate(st, sources.get(sample))
        ck = Counter(v["kind"] for v in g["violations"])
        kind_tot.update(ck)
        runs.append({
            "run": tag, "status": g["status"], "n_violations": len(g["violations"]),
            "kinds": dict(ck), "n_refs": g["n_refs"],
            "pool_bridge": g["pool_bridge"],
        })

    got_pass = {r["run"] for r in runs if r["status"] == "pass"}
    diff_pass = sorted(got_pass ^ EXPECTED_PASS)

    # 坏锚：b188060f fixture + s06 r1–r5
    anchors = []
    fixture = {
        "trade_date": "2026-05-22",
        "news_report": "今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价 6.45%。",
        "trader_investment_plan": "下行风险较大，第一下行目标 78.61 元（大宗折价锚位）。",
    }
    g = run_candidate(dict(fixture), None)
    anchors.append({"anchor": "b188060f_fixture", "status": g["status"],
                    "kinds": dict(Counter(v["kind"] for v in g["violations"]))})
    for tag, st in sorted(states.items()):
        if not tag.startswith("s06__"):
            continue
        g = run_candidate(dict(st), sources.get("s06"))
        anchors.append({"anchor": f"s06:{tag}", "status": g["status"],
                        "kinds": dict(Counter(v["kind"] for v in g["violations"]))})
    anchors_ok = all(a["status"] == "blocked" for a in anchors)

    report = {
        "input": {"snapshot_sha256_all_match": sha_ok, "n_states": len(state_files),
                  "state_sha256": state_sha},
        "summary": {
            "blocked": sum(1 for r in runs if r["status"] == "blocked"),
            "passed": sorted(got_pass),
            "expected_pass": sorted(EXPECTED_PASS),
            "pass_set_matches_w4": not diff_pass,
            "pass_diff": diff_pass,
            "kind_totals": dict(kind_tot),
            "anchors_all_blocked": anchors_ok,
        },
        "runs": runs,
        "anchors": anchors,
    }
    out_dir = os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "recompute.json"), "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    md = ["# DAV-1224 候选 trunk 冻结语料重算\n",
          f"- snapshot SHA256 vs manifest：{'10/10 匹配' if sha_ok else '存在不匹配！'}；state 数 {len(state_files)}",
          f"- pass {len(got_pass)}/50：{sorted(got_pass)}",
          f"- 预期（W4）：{sorted(EXPECTED_PASS)}",
          f"- pass 集合一致：{not diff_pass}；差异：{diff_pass or '无'}",
          f"- violation kinds：{dict(kind_tot)}",
          f"- 坏锚全部 blocked：{anchors_ok}", "",
          "## 坏锚明细"]
    for a in anchors:
        md.append(f"- {a['anchor']}: {a['status']} {a['kinds']}")
    md.append("\n## 逐 run")
    for r in runs:
        md.append(f"- {r['run']}: {r['status']} {r['kinds']} bridge={len(r['pool_bridge'])}")
    with open(os.path.join(out_dir, "SUMMARY.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md[:12]))


if __name__ == "__main__":
    main()
