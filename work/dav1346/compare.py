"""DAV-1346 重放对比：baseline(46db6549) vs candidate（R2 raw 侧坐标触发收窄）。

输入：work/dav1346/out/{baseline,candidate}.json（recompute.py 两侧同口径）。
输出：
  * stdout：翻转清单（解拦/新拦）、逐 violation kind 计数差；
  * --table <path>：逐档位归类表（Markdown，D-040：只写判定理由摘要，
    不含供应商原始数据；原句依据仅在 refs.context 内核对，不落盘）。
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def load(name):
    return json.load(open(os.path.join(OUT, f"{name}.json")))["runs"]


def viol_keys(run):
    return [(v["kind"], v["source"]) for v in run["violations"]]


def has_cross_basis(run):
    return any(v["kind"] == "cross_basis_coordinate_mix" for v in run["violations"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table")
    args = ap.parse_args()

    base = load("baseline")
    cand = load("candidate")
    keys = sorted(set(base) | set(cand))

    unblocked, newly_blocked = [], []
    kind_base, kind_cand = Counter(), Counter()
    for k in keys:
        b, c = base.get(k), cand.get(k)
        for kind, _src in viol_keys(b):
            kind_base[kind] += 1
        for kind, _src in viol_keys(c):
            kind_cand[kind] += 1
        if b["status"] == "blocked" and c["status"] != "blocked":
            unblocked.append(k)
        elif b["status"] != "blocked" and c["status"] == "blocked":
            newly_blocked.append(k)

    print(f"runs={len(keys)} baseline_blocked={sum(1 for r in base.values() if r['status']=='blocked')} "
          f"candidate_blocked={sum(1 for r in cand.values() if r['status']=='blocked')}")
    print(f"unblocked={len(unblocked)} newly_blocked={len(newly_blocked)}")
    print("\n== violation kind counts (baseline -> candidate) ==")
    for kind in sorted(set(kind_base) | set(kind_cand)):
        print(f"  {kind}: {kind_base.get(kind,0)} -> {kind_cand.get(kind,0)}")

    print("\n== unblocked runs ==")
    for k in unblocked:
        b, c = base[k], cand[k]
        dropped = Counter(viol_keys(b)) - Counter(viol_keys(c))
        kept = Counter(viol_keys(c))
        print(f"  {k} sym={b.get('symbol')} td={b.get('trade_date')}")
        print(f"    dropped: {dict(dropped)}")
        if kept:
            print(f"    kept   : {dict(kept)}")
        # which raw refs lost trigger / which kept
        crefs = {r["ref_id"]: r for r in c["refs"]}
        brefs = {r["ref_id"]: r for r in b["refs"]}
        raws = [r for r in b["refs"] if r["basis"] in ("raw", "pit_raw")]
        for r in raws:
            print(f"    rawref {r['ref_id']} {r['value']} {r['basis']} {r['source']} ctx={(r['context'] or '')[:80]}")

    print("\n== newly_blocked runs ==")
    for k in newly_blocked:
        print(f"  {k}")

    # cross_basis deltas per run (for classification table)
    if args.table:
        lines = ["| run | symbol | trade_date | base_viol | cand_viol | flip |", "|---|---|---|---|---|---|"]
        for k in keys:
            b, c = base[k], cand[k]
            bb, cb = Counter(viol_keys(b)), Counter(viol_keys(c))
            flip = ""
            if b["status"] != c["status"]:
                flip = f"{b['status']}→{c['status']}"
            if bb != cb or flip:
                lines.append(
                    f"| {k} | {b.get('symbol','')} | {b.get('trade_date','')} | "
                    f"{sum(bb.values())} | {sum(cb.values())} | {flip} |")
        with open(args.table, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\ntable -> {args.table}")


if __name__ == "__main__":
    main()
