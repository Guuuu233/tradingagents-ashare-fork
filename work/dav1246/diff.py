"""DAV-1246 重算 diff：baseline(5909db5) vs candidate 逐 ref/逐 run 对比汇总。

用法：先跑 recompute.py --impl baseline 与 --impl candidate，再跑本脚本。
输出 work/dav1246/out/diff_summary.md + ref_diff.jsonl。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    base = json.load(open(os.path.join(OUT, "baseline.json")))
    cand = json.load(open(os.path.join(OUT, "candidate.json")))
    md = []
    md.append("# DAV-1246 候选 vs 5909db5 重算 diff\n")

    all_runs = sorted(set(base["runs"]) | set(cand["runs"]))
    b2p, p2b = [], []
    ref_changes = []
    viol_changes = {}
    for tag in all_runs:
        rb, rc = base["runs"].get(tag), cand["runs"].get(tag)
        if rb is None or rc is None:
            continue
        if rb["status"] == "blocked" and rc["status"] == "pass":
            b2p.append(tag)
        if rb["status"] == "pass" and rc["status"] == "blocked":
            p2b.append(tag)
        vb = {(v["kind"], tuple(v["ref_ids"]), v["detail"]) for v in rb["violations"]}
        vc = {(v["kind"], tuple(v["ref_ids"]), v["detail"]) for v in rc["violations"]}
        if vb != vc:
            viol_changes[tag] = {
                "removed": sorted([list(x) for x in vb - vc], key=str),
                "added": sorted([list(x) for x in vc - vb], key=str),
            }
        mb = {r["ref_id"]: r for r in rb["refs"]}
        mc = {r["ref_id"]: r for r in rc["refs"]}
        # ref_id 为序号，删除/新增 ref 会错位——按 (value,source,context) 对齐
        kb = {(r["value"], r["source"], r["context"]): r for r in rb["refs"]}
        kc = {(r["value"], r["source"], r["context"]): r for r in rc["refs"]}
        for k in sorted(set(kb) | set(kc), key=str):
            a, c = kb.get(k), kc.get(k)
            if a is None:
                ref_changes.append({"run": tag, "change": "added", "value": k[0],
                                    "source": k[1], "to": c["basis"],
                                    "to_prov": c.get("provenance"),
                                    "context": (k[2] or "")[:110]})
            elif c is None:
                ref_changes.append({"run": tag, "change": "removed", "value": k[0],
                                    "source": k[1], "from": a["basis"],
                                    "from_prov": a.get("provenance"),
                                    "context": (k[2] or "")[:110]})
            elif a["basis"] != c["basis"] or a.get("provenance") != c.get("provenance"):
                ref_changes.append({"run": tag, "change": "basis",
                                     "value": k[0], "source": k[1],
                                     "from": a["basis"], "to": c["basis"],
                                     "from_prov": a.get("provenance"),
                                     "to_prov": c.get("provenance"),
                                     "context": (k[2] or "")[:110]})

    nb = sum(1 for r in base["runs"].values() if r["status"] == "pass")
    nc = sum(1 for r in cand["runs"].values() if r["status"] == "pass")
    frozen_b = sum(1 for k, r in base["runs"].items()
                   if not k.startswith("prod_") and r["status"] == "pass")
    frozen_c = sum(1 for k, r in cand["runs"].items()
                   if not k.startswith("prod_") and r["status"] == "pass")
    md.append(f"## 总览\n- frozen 50：baseline pass {frozen_b}/50（W4=6/50）→ "
              f"candidate pass {frozen_c}/50\n"
              f"- 含生产报告合计：{nb}/{len(all_runs)} → {nc}/{len(all_runs)}\n"
              f"- blocked→pass：{b2p}\n- pass→blocked：{p2b}\n")

    md.append("## 逐 run violation 变化\n")
    for tag, ch in sorted(viol_changes.items()):
        md.append(f"### {tag}")
        for v in ch["removed"]:
            md.append(f"- 移除 `{v[0]}` {v[1]} {v[2][:90]}")
        for v in ch["added"]:
            md.append(f"- 新增 `{v[0]}` {v[1]} {v[2][:90]}")
    md.append("")

    md.append(f"## 逐 ref 角色/basis 变化（{len(ref_changes)} 条）\n")
    for ch in ref_changes:
        md.append(f"- `{ch['run']}` {ch['change']} value={ch['value']} "
                  f"{ch.get('from')}→{ch.get('to')} "
                  f"prov={ch.get('from_prov')}→{ch.get('to_prov')} | "
                  f"{ch['context']}")
    md.append("")

    # 4390ddfd 验收点核对（按 数值+原句 匹配，不按 ref 编号——编号随登记重排）
    r9 = cand["runs"]["prod_4390ddfd"]
    md.append("## 4390ddfd 验收点\n")
    der = [(r["ref_id"], r["value"], (r["context"] or "")[:40])
           for r in r9["refs"] if r["basis"] == "derived_estimate"]
    md.append(f"- derived_estimate refs：{der}")
    # 应清除的 ref（数值 + 原句关键片段）
    gone = {
        "pr-156 上行空间 14.31": (14.31, "上行空间"),
        "pr-157 下行回撤 30.04": (30.04, "下行回撤"),
        "pr-189 批发参考价 2000": (2000.0, "批发参考价"),
        "pr-116 最高4板": (4.0, "最高4板"),
        "pr-200 滑点 5": (5.0, "元滑点"),
    }
    for label, (v, frag) in gone.items():
        still = [r for r in r9["refs"]
                 if abs(r["value"] - v) <= 5e-3 and frag in (r["context"] or "")]
        viol = [x for x in r9["violations"]
                if any(rr.get("context") and frag in rr["context"]
                       for rid in x["ref_ids"]
                       for rr in r9["refs"] if rr["ref_id"] == rid)]
        md.append(f"- {label}：仍登记 {len(still)} 条；关联违规 {len(viol)} 条")
    p191 = [r for r in r9["refs"]
            if (r.get("provenance") or "").startswith("pool_bridge")]
    md.append(f"- 桥接 refs：{[(r['ref_id'], r['value'], r['provenance']) for r in p191]}")
    md.append(f"- 4390ddfd gate status：{r9['status']}（违规 {len(r9['violations'])} 条）")
    md.append(f"- b188060f gate status：{cand['runs']['prod_b188060f']['status']}")
    s06 = {k: cand["runs"][k]["status"] for k in cand["runs"] if k.startswith("s06__")}
    md.append(f"- s06 r1–r5：{s06}")

    # D-040/D-036：大 JSON 不入库，仅记 SHA256
    for fn in ("baseline.json", "candidate.json"):
        fp = os.path.join(OUT, fn)
        if os.path.exists(fp):
            md.append(f"- out/{fn} SHA256：`{sha256(fp)}`（不入库）")

    with open(os.path.join(OUT, "ref_diff.jsonl"), "w") as fh:
        for ch in ref_changes:
            fh.write(json.dumps(ch, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, "diff_summary.md"), "w") as fh:
        fh.write("\n".join(md))
    print("\n".join(md[:40]))
    print(f"... -> {OUT}/diff_summary.md ({len(ref_changes)} ref changes)")


if __name__ == "__main__":
    main()
