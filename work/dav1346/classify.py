"""DAV-1346 逐档归类：对基线有 cross_basis 拦截的样本逐档判 (a)/(b)。

(a) 真混用：raw/pit_raw 自身句子带坐标语境（支撑/阻力/平台/止损/目标/
    成本线/底线/安全垫/中枢/承接/现价 等）——候选仍触发、仍拦；
(b) R2 连坐：raw 只在事实句（成交均价/折价披露/发行价等）——候选不再
    触发字段级连坐，若无其他违规则放行。

输出：stdout + --table <path> Markdown（判定理由摘要，不落原始长句）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, REPO)

from tradingagents.agents.utils.price_ref_registry import (  # noqa: E402
    RAW_COORDINATE_TRIGGER_KEYWORDS,
)

OUT = os.path.join(HERE, "out")


def trig_words(ctx: str):
    return [kw for kw in RAW_COORDINATE_TRIGGER_KEYWORDS if kw in (ctx or "")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    args = ap.parse_args()

    base = json.load(open(os.path.join(OUT, "baseline.json")))["runs"]
    cand = json.load(open(os.path.join(OUT, "candidate.json")))["runs"]

    rows = []
    for k in sorted(base):
        b, c = base[k], cand[k]
        b_cb = [v for v in b["violations"] if v["kind"] == "cross_basis_coordinate_mix"]
        if not b_cb:
            continue
        raws = [r for r in b["refs"] if r["basis"] in ("raw", "pit_raw")]
        trig = [r for r in raws if trig_words(r["context"])]
        quiet = [r for r in raws if not trig_words(r["context"])]
        c_cb = [v for v in c["violations"] if v["kind"] == "cross_basis_coordinate_mix"]
        if trig or c_cb:
            cls = "a"
            parts = []
            if trig:
                parts.append(
                    f"raw 侧触发词命中 {sorted({w for r in trig for w in trig_words(r['context'])})}"
                    f"（{len(trig)} 条坐标语境 raw）")
            r1 = sum(1 for v in c_cb if (v.get('detail') or '').startswith('同句混用'))
            if r1:
                parts.append(f"R1 同句混用仍拦 {r1} 条")
            parts.append(f"候选 cross_basis {len(b_cb)}→{len(c_cb)}")
            why = "；".join(parts)
        else:
            cls = "b"
            why = (f"{len(quiet)} 条 raw/pit_raw 均在事实句（成交价/均价/折价/发行价），"
                   f"候选 cross_basis {len(b_cb)}→{len(c_cb)}，档位 {b['status']}→{c['status']}")
        rows.append((k, b.get("symbol") or "-", b.get("trade_date") or "-",
                     len(b_cb), len(c_cb), b["status"], c["status"], cls, why,
                     ",".join(r["ref_id"] for r in trig),
                     ",".join(r["ref_id"] for r in quiet)))

    lines = [
        "| 档位 | symbol | trade_date | base_cb | cand_cb | base→cand | 归类 | 依据 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k, sym, td, nb, nc, sb, sc, cls, why, tids, qids in rows:
        lines.append(f"| {k} | {sym} | {td} | {nb} | {nc} | {sb}→{sc} | {cls} | {why} |")
    with open(args.table, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    na = sum(1 for r in rows if r[7] == "a")
    print(f"cross_basis 基线档位={len(rows)}: (a)={na} (b)={len(rows)-na}")
    print(f"table -> {args.table}")
    for r in rows:
        print(f"  [{r[7]}] {r[0]} {r[5]}→{r[6]} cb {r[3]}→{r[4]} trig={r[8] or '-'}")


if __name__ == "__main__":
    main()
