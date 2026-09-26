#!/usr/bin/env python3
"""DAV-1311 汇总：逐例表 + 子类型计数 + Wilson 95% 区间 + E-04 四类分法。

    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        work/dav1311-hardgate-audit/summarize.py

读 dossiers/index.json + verdicts.json，输出 out/report.md + out/summary.json。
"""
import json, math, os
from collections import Counter

HERE = os.path.dirname(__file__)
idx = {e["report_id"] + ":" + e["horizon"]: e for e in
       json.load(open(os.path.join(HERE, "dossiers", "index.json"), encoding="utf-8"))}
V = json.load(open(os.path.join(HERE, "verdicts.json"), encoding="utf-8"))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (p, max(0.0, c - h), min(1.0, c + h))


def cat_of(label):
    if label.startswith("reject_in_partial"):
        return "reject_in_partial"
    if label.startswith("reject_in_adopted"):
        return "reject_in_adopted"
    if label.startswith("partial_threshold_adopted"):
        return "partial_threshold_adopted"
    if label.startswith("coverage_floor"):
        return "coverage_floor"
    if label.startswith("coverage_text_conflict"):
        return "coverage_text_conflict"
    if label.startswith("coverage"):
        return "coverage_mixed_adopted"
    if label.startswith("winner_conflict"):
        return "winner_conflict"
    if label.startswith("e04"):
        return "e04"
    return label


hit_cnt = Counter(); hit_correct = Counter(); hit_false = Counter()
e04_class_cnt = Counter(); e04_class_verdict = Counter()
entry_verdicts = Counter()
pseudo_atoms = []
missing = []
lines = ["# DAV-1311 逐例审计表", "",
         "| # | 档位 | 来源 | 标的 | 命中(判定) | 档位判定 | 理由 |",
         "|---|---|---|---|---|---|---|"]

for i, e in enumerate(V["entries"]):
    key = e["key"]
    meta = idx.get(key)
    if not meta:
        missing.append(key); continue
    entry_verdicts[e["verdict"]] += 1
    hs = []
    for h in e["hits"]:
        cat = cat_of(h["label"])
        hit_cnt[cat] += 1
        if h["verdict"] == "correct":
            hit_correct[cat] += 1
        elif h["verdict"] == "false":
            hit_false[cat] += 1
        if h.get("e04_class"):
            e04_class_cnt[h["e04_class"]] += 1
            e04_class_verdict[(h["e04_class"], h["verdict"])] += 1
        if h.get("pseudo_atom"):
            pseudo_atoms.append((key, h["label"]))
        mark = {"correct": "正", "false": "误", "undetermined": "?"}[h["verdict"]]
        hs.append(f"{h['label']}({mark})")
    vcn = {"correct": "正确拦截", "false": "误拦", "undetermined": "无法判定"}[e["verdict"]]
    lines.append(
        f"| {i+1} | `{key}` | {meta['source']} | {meta.get('symbol','')} {meta.get('trade_date','')} "
        f"| {'；'.join(hs)} | {vcn} | {e['reason']} |")

summary = {
    "entry_verdicts": dict(entry_verdicts),
    "hit_counts": {c: {"total": hit_cnt[c], "correct": hit_correct[c], "false": hit_false[c]}
                   for c in hit_cnt},
    "e04_classes": dict(e04_class_cnt),
    "pseudo_atom_cases": pseudo_atoms,
    "missing_in_index": missing,
}

out = os.path.join(HERE, "out"); os.makedirs(out, exist_ok=True)
n = sum(entry_verdicts.values()); k = entry_verdicts["correct"]
p, lo, hi = wilson(k, n)
lines += ["", "## 汇总", "",
          f"- 档位级：正确拦截 {k}/{n} = {p:.1%}，Wilson95% [{lo:.1%}, {hi:.1%}]；"
          f"误拦 {entry_verdicts['false']}；无法判定 {entry_verdicts['undetermined']}。",
          "", "### 命中类型 × 判定", "",
          "| 命中类型 | 命中数 | 正确 | 误拦 | 正确率(Wilson95%) |", "|---|---|---|---|---|"]
for c in hit_cnt:
    t = hit_cnt[c]; ck = hit_correct[c]
    p, lo, hi = wilson(ck, t)
    lines.append(f"| {c} | {t} | {ck} | {hit_false[c]} | {p:.0%} [{lo:.0%},{hi:.0%}] |")
lines += ["", "### E-04 四类分法（DAV-1112 口径，按命中计）", "",
          "| 类别 | 命中数 | 判定 |", "|---|---|---|"]
CLS = {"bare_assertion": "裸断言", "conditional": "条件/情景推演",
       "downgrade_heuristic": "明示未知仅作降权", "traceable_inference": "附可回溯依据的定价推断",
       "negation_mechanical": "否定句机械命中", "mechanical": "词面机械命中(非E-04语义)"}
for cls, cnt in e04_class_cnt.items():
    lines.append(f"| {CLS.get(cls, cls)} | {cnt} | "
                 f"正确={e04_class_verdict.get((cls,'correct'),0)} 误拦={e04_class_verdict.get((cls,'false'),0)} |")
lines += ["", f"核验器伪原子导致的误拦（coverage 子类）：{len(pseudo_atoms)} 例 → {pseudo_atoms}"]

open(os.path.join(out, "report.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
json.dump(summary, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n".join(lines[-30:]))
