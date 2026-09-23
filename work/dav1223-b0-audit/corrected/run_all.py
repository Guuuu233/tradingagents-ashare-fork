"""DAV-1225 B0 纠错 + W0–W4 what-if 一条命令复跑。

用法（仓库根目录）：
    env -u PYTHONPATH .venv310/bin/python work/dav1223-b0-audit/corrected/run_all.py

输入：work/dav1223-b0-audit/ext/{snapshots,states}/ + snapshot_manifest.json
输出：work/dav1223-b0-audit/corrected/out/{*.json,*.jsonl,SUMMARY.md}

零 LLM、零外部请求、零生产写库、不改 trunk。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import pickle
import random
import re
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DIR = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
# corrected/ 作为包
sys.path.insert(0, AUDIT_DIR)
from corrected import classify as C                      # noqa: E402
from corrected import whatif_impl as W                   # noqa: E402
from corrected.pool import (                             # noqa: E402
    SnapshotPool, SnapshotPoolError, build_pool, selfcheck_missing_column,
)
from tradingagents.agents.utils.price_ref_registry import (  # noqa: E402
    REPORT_FIELDS, audit_price_ref_registry,
)
from tradingagents.agents.utils.price_basis_gate import (  # noqa: E402
    DECISION_REPORT_FIELDS, _LEVEL_PATTERN, evaluate_price_basis_gate,
    _is_decision_driving,
)

OUT = os.path.join(HERE, "out")


def _resolve_audit_root() -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--audit-root", default=AUDIT_DIR,
        help="包含 ext/、snapshot_manifest.json、violations_attr.json 的目录；"
             "输入不入库（R5），以路径+SHA256 记录",
    )
    args, _ = ap.parse_known_args()
    root = os.path.abspath(args.audit_root)
    if not os.path.isdir(os.path.join(root, "ext")):
        raise SystemExit(
            f"缺少输入：{root}/ext 不存在。输入为 DAV-1222 冻结附件"
            "（10 snapshot + 50 state pickle），按 R5 不入库，"
            "请用 --audit-root 指向本地副本。"
        )
    return root


AUDIT_ROOT = _resolve_audit_root()
EXT = os.path.join(AUDIT_ROOT, "ext")
MANIFEST = os.path.join(AUDIT_ROOT, "snapshot_manifest.json")
ARCHIVED = os.path.join(AUDIT_ROOT, "violations_attr.json")
MANUAL_REVIEW = os.path.join(HERE, "manual_review.json")

EXTRA_STATE_KEYS = (
    "trade_date", "trade_action", "decision_status",
    "short_term", "medium_term", "result_data",
    "short_term_result", "medium_term_result",
)

KINDS = [
    "cross_basis_coordinate_mix",
    "decision_driving_missing_as_of",
    "decision_driving_unspecified_basis",
    "invalid_conversion",
    "unbacked_executable_level",
    "executable_level_wrong_basis",
    "audit_unavailable",
]


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


def gate_w0(state: Dict[str, Any]) -> Dict[str, Any]:
    """trunk 原样：audit + evaluate。"""
    audit_price_ref_registry(state)
    return evaluate_price_basis_gate(state)


def _median(xs: List[float]) -> float:
    """[R4] 标准中位数（偶数个取两中值平均）。"""
    return statistics.median(xs)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    report: Dict[str, Any] = {"steps": {}}
    md: List[str] = []
    md.append("# DAV-1225 corrected B0 复跑汇总\n")

    # ------------------------------------------------------------------
    # 0. 输入校验
    # ------------------------------------------------------------------
    manifest = json.load(open(MANIFEST))
    snap_files = sorted(glob.glob(os.path.join(EXT, "snapshots", "snapshots", "*.pkl")))
    state_files = sorted(glob.glob(os.path.join(EXT, "states", "states", "*.pkl")))
    sha_rows = []
    ok = True
    for p in snap_files:
        name = os.path.basename(p)
        sample = name.split("_")[0]
        expect = manifest["samples"].get(sample, {}).get("snapshot_sha256")
        got = sha256(p)
        match = (expect == got)
        ok &= match
        sha_rows.append({"file": name, "sha256": got, "manifest_sha256": expect, "match": match})
    state_sha = {os.path.basename(p): sha256(p) for p in state_files}
    report["steps"]["input_sha256"] = {
        "snapshots": sha_rows, "all_match": ok, "n_states": len(state_files),
        "state_sha256": state_sha,
    }
    md.append(f"## 输入校验\n- snapshot SHA256 vs manifest：{'10/10 全匹配' if ok else '存在不匹配！'}\n"
              f"- state 数：{len(state_files)}（SHA256 已记录于 out/run_all.json）\n")
    assert ok, "snapshot SHA256 与 manifest 不匹配"

    # ------------------------------------------------------------------
    # 1. snapshot parser 修正 + 字段清单
    # ------------------------------------------------------------------
    pools: Dict[str, SnapshotPool] = {}
    pool_rows = []
    for p in snap_files:
        name = os.path.basename(p)
        sample = name.split("_")[0]
        info = manifest["samples"][sample]
        snap = pickle.load(open(p, "rb"))
        pool = build_pool(sample, info["symbol"], info["trade_date"], snap)
        pools[sample] = pool
        pool_rows.append({
            "sample": sample, "symbol": info["symbol"], "trade_date": info["trade_date"],
            "stock_data_header": pool.stock_data_header,
            "n_bars": len(pool.bars), "n_indicators": len(pool.indicators),
            "indicator_fields": sorted(pool.indicators),
            "named_values": len(pool.named_values),
            "stock_data_basis": pool.stock_data_basis,
            "price_range": pool.price_range(),
        })
    selfcheck = selfcheck_missing_column()
    report["steps"]["snapshot_parser"] = {"pools": pool_rows, "selfcheck_missing_column": selfcheck}
    md.append("## 1. snapshot parser（按 header 取列）\n")
    md.append("| sample | symbol | 实际表头 | bars | 指标字段 | price_range |\n|---|---|---|---|---|---|")
    for r in pool_rows:
        md.append(f"| {r['sample']} | {r['symbol']} | `{','.join(r['stock_data_header'])}` "
                  f"| {r['n_bars']} | {r['n_indicators']} | {r['price_range']} |")
    md.append(f"\n缺列自检（fail-closed）：`{selfcheck}`\n")
    md.append("注：10/10 实际表头为 `date,low,close,volume,open,high` 时——"
              "即 DAV-1223 B0 按固定列号解析正是把 low/close/volume 错读为 open/high/low 的根因。\n")

    # ------------------------------------------------------------------
    # 2. W0 复现
    # ------------------------------------------------------------------
    archived = json.load(open(ARCHIVED))
    arch_ck = Counter((x["tag"], x["kind"]) for x in archived)
    arch_kind = Counter(x["kind"] for x in archived)

    states: Dict[str, Dict[str, Any]] = {}
    w0_runs: List[Dict[str, Any]] = []
    w0_kind = Counter()
    w0_mismatch: List[Any] = []
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        st = load_state(p)
        states[tag] = st
        g = gate_w0(st)
        ck = Counter(v["kind"] for v in g["violations"])
        for k, n in ck.items():
            w0_kind[k] += n
            if arch_ck.get((tag, k)) != n:
                w0_mismatch.append({"run": tag, "kind": k, "got": n, "archived": arch_ck.get((tag, k))})
        for (t, k), n in arch_ck.items():
            if t == tag and ck.get(k, 0) == 0 and n > 0:
                w0_mismatch.append({"run": tag, "kind": k, "got": 0, "archived": n})
        w0_runs.append({
            "run": tag, "status": g["status"], "n_violations": len(g["violations"]),
            "n_refs": len(st.get("price_refs") or []),
            "kinds": dict(ck),
        })
    n_blocked = sum(1 for r in w0_runs if r["status"] == "blocked")
    vcounts = [r["n_violations"] for r in w0_runs]
    w0_ok = not w0_mismatch
    report["steps"]["w0_replay"] = {
        "total": sum(w0_kind.values()), "kinds": dict(w0_kind),
        "blocked": n_blocked, "passed": 50 - n_blocked,
        "per_run_min": min(vcounts), "per_run_median": _median(vcounts),
        "per_run_max": max(vcounts),
        "matches_archive": w0_ok, "mismatches": w0_mismatch[:50],
        "runs": w0_runs,
    }
    md.append("## 2. W0 复现（trunk 原样重跑）\n")
    md.append(f"- 总 violation：{sum(w0_kind.values())}（存档 {sum(arch_kind.values())}）"
              f"——逐 run 逐 kind 比对：{'全部一致' if w0_ok else '存在差异 ' + str(w0_mismatch[:10])}\n")
    md.append(f"- 逐 kind：{dict(w0_kind)}\n- blocked {n_blocked}/50；"
              f"每 run min {min(vcounts)} / 中位数 {_median(vcounts)}（标准中位数，"
              f"偶数样本取两中值均值）/ max {max(vcounts)}\n")

    # ------------------------------------------------------------------
    # 3. 473 unspecified + 111 typed + 45 conv + 52 exec 逐条分类
    # ------------------------------------------------------------------
    ref_rows: List[Dict[str, Any]] = []           # 全部 ref（含 basis/provenance）
    unspec_rows: List[Dict[str, Any]] = []
    typed_rows: List[Dict[str, Any]] = []
    conv_rows: List[Dict[str, Any]] = []
    exec_rows: List[Dict[str, Any]] = []
    cross_refs: List[Dict[str, Any]] = []         # cross_basis 违规涉及的 ref
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        sample = tag.split("__")[0]
        pool = pools[sample]
        st = load_state(p)
        g = gate_w0(st)
        refs = {r["ref_id"]: r for r in st.get("price_refs") or []}
        dd_ids = set()
        for v in g["violations"]:
            if v["kind"] in ("decision_driving_unspecified_basis",):
                dd_ids.update(v["ref_ids"])
            if v["kind"] == "invalid_conversion":
                for rid in v["ref_ids"]:
                    if rid in refs:
                        row = C.classify_invalid_conversion(refs[rid])
                        row["run"] = tag; row["sample"] = sample
                        conv_rows.append(row)
            if v["kind"] == "cross_basis_coordinate_mix":
                cross_refs.append({"run": tag, "sample": sample, "ref_ids": v["ref_ids"],
                                   "detail": v["detail"], "source": v["source"]})
            if v["kind"] in ("unbacked_executable_level", "executable_level_wrong_basis"):
                m = re.search(r"可执行价位 ([\d.]+)（(\w+)）", v["detail"])
                val = float(m.group(1)) if m else None
                fld = m.group(2) if m else v.get("source")
                # 在原文中找命中位置
                text = st.get(fld) or ""
                mstart = mend = 0
                for mm in _LEVEL_PATTERN.finditer(text):
                    try:
                        if val is not None and abs(float(mm.group(1)) - val) <= 5e-3:
                            mstart, mend = mm.start(1), mm.end(1)
                            break
                    except (TypeError, ValueError):
                        pass
                row = C.classify_executable_level(val or 0.0, fld, text, mstart, mend, pool)
                row["run"] = tag; row["sample"] = sample
                row["kind"] = v["kind"]; row["detail"] = v["detail"]
                row["ref_ids"] = v["ref_ids"]
                exec_rows.append(row)
        for r in st.get("price_refs") or []:
            row = {"run": tag, "sample": sample, "ref_id": r["ref_id"],
                   "value": r["value"], "basis": r["basis"], "source": r["source"],
                   "provenance": r.get("provenance"), "as_of": r.get("as_of"),
                   "context": r.get("context")}
            ref_rows.append(row)
            if (r.get("provenance") or "").startswith("typed_disclosure"):
                # stock_name 见上方调用
                t = C.classify_typed_disclosure(
                    r, stock_name=manifest["samples"][sample].get("stock_name"))
                t["run"] = tag; t["sample"] = sample
                typed_rows.append(t)
            if r["basis"] == "unspecified" and r["ref_id"] in dd_ids:
                u = C.classify_unspecified_ref(r, pool)
                u["run"] = tag; u["sample"] = sample
                unspec_rows.append(u)

    # --- unspecified 汇总 + 抽样 + [R1] 人工复核 ---
    cat_counter = Counter(r["category"] for r in unspec_rows)
    subtype_counter = Counter((r["category"], r["subtype"]) for r in unspec_rows)
    rng = random.Random(20260923)
    samples_audit = {}
    for cat, rows in sorted(_groupby(unspec_rows, "category").items()):
        take = rows if len(rows) <= 30 else rng.sample(rows, 30)
        samples_audit[cat] = [
            {"run": t["run"], "ref_id": t["ref_id"], "value": t["value"],
             "context": t["context"], "reason": t["reason"]}
            for t in take
        ]
    # [R1] 载入人工复核表（manual_review.json：{"run|ref_id": {...}}）
    manual: Dict[str, Any] = {}
    if os.path.exists(MANUAL_REVIEW):
        manual = json.load(open(MANUAL_REVIEW))
    precision = {}
    for cat, rows in samples_audit.items():
        n_ok = n_w = n_u = 0
        for t in rows:
            key = f"{t['run']}|{t['ref_id']}"
            mv = manual.get(key)
            if mv:
                t["manual_verdict"] = mv["verdict"]
                t["manual_reason"] = mv["reason"]
                t["manual_category"] = mv.get("category")
            if mv and mv["verdict"] == "correct":
                n_ok += 1
            elif mv and mv["verdict"] == "unsure":
                n_u += 1
            elif mv:
                n_w += 1
        total = len(rows)
        precision[cat] = {
            "sampled": total, "manual_correct": n_ok,
            "manual_wrong": n_w, "manual_unsure": n_u,
            "precision": round(n_ok / total, 3) if total else None,
        }
    report["steps"]["unspecified_473"] = {
        "n": len(unspec_rows), "categories": dict(cat_counter),
        "subtypes": {f"{k[0]}|{k[1]}": v for k, v in subtype_counter.items()},
        "sampled_for_manual_review": {k: len(v) for k, v in samples_audit.items()},
        "manual_review_file": "manual_review.json",
        "manual_precision": precision,
        "samples": samples_audit,
    }

    # --- typed disclosure ---
    td_verdict = Counter(r["verdict"] for r in typed_rows)
    td_by_type = Counter((r["disclosure_type"], r["verdict"]) for r in typed_rows)
    report["steps"]["typed_disclosure_111"] = {
        "n": len(typed_rows), "verdicts": dict(td_verdict),
        "by_type": {f"{k[0]}|{k[1]}": v for k, v in sorted(td_by_type.items())},
        "false_rows": [{"run": r["run"], "ref_id": r["ref_id"], "value": r["value"],
                        "type": r["disclosure_type"], "reason": r["reason"],
                        "context": r["context"]} for r in typed_rows if r["verdict"] == "false"],
        "ambiguous_rows": [{"run": r["run"], "ref_id": r["ref_id"], "value": r["value"],
                            "type": r["disclosure_type"], "reason": r["reason"],
                            "context": r["context"]} for r in typed_rows if r["verdict"] == "ambiguous"],
    }

    # --- cross_basis 重算：该违规涉及的 raw/pit_raw 伙伴 ref 全为假披露 → 假披露触发 ---
    # 需要先知道每个 ref 的 verdict
    td_map = {(r["run"], r["ref_id"]): r["verdict"] for r in typed_rows}
    ref_map = {(r["run"], r["ref_id"]): r for r in ref_rows}
    cb_stats = Counter()
    for cr in cross_refs:
        partners = [rid for rid in cr["ref_ids"]
                    if (cr["run"], rid) in ref_map
                    and ref_map[(cr["run"], rid)]["basis"] in ("raw", "pit_raw")]
        verdicts = [td_map.get((cr["run"], rid)) for rid in partners]
        if partners and all(v == "false" for v in verdicts):
            cb_stats["all_partners_false_disclosure"] += 1
        elif partners and any(v == "false" for v in verdicts):
            cb_stats["some_false"] += 1
        elif partners and all(v == "true" for v in verdicts):
            cb_stats["all_true_disclosure"] += 1
        elif partners:
            cb_stats["has_ambiguous"] += 1
        else:
            cb_stats["no_typed_partner"] += 1
    report["steps"]["cross_basis_recount"] = {
        "n_violations": len(cross_refs), "breakdown": dict(cb_stats),
    }

    # --- conv / exec 汇总 ---
    report["steps"]["invalid_conversion_45"] = {
        "n": len(conv_rows),
        "categories": dict(Counter(r["category"] for r in conv_rows)),
    }
    report["steps"]["executable_52"] = {
        "n": len(exec_rows),
        "categories": {f"{k[0]}|{k[1]}": v for k, v in Counter((r["kind"], r["category"]) for r in exec_rows).items()},
        "by_category": dict(Counter(r["category"] for r in exec_rows)),
    }

    for name, rows in (
        ("refs_all", ref_rows), ("unspecified_classification", unspec_rows),
        ("typed_disclosure_review", typed_rows),
        ("invalid_conversion_review", conv_rows),
        ("executable_review", exec_rows), ("cross_basis_refs", cross_refs),
    ):
        with open(os.path.join(OUT, f"{name}.jsonl"), "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    md.append("## 3. 逐条复核\n")
    md.append(f"### 3.1 unspecified decision-driving refs（{len(unspec_rows)} 条）\n")
    for cat, n in cat_counter.most_common():
        md.append(f"- {cat}: {n}")
    md.append("")
    md.append(f"### 3.1b 抽样人工复核精度（R1，manual_review.json 入库）\n")
    md.append("| 类 | 抽样数 | correct | wrong | unsure | 精度 |")
    md.append("|---|---|---|---|---|---|")
    for cat, p in precision.items():
        md.append(f"| {cat} | {p['sampled']} | {p['manual_correct']} | "
                  f"{p['manual_wrong']} | {p['manual_unsure']} | {p['precision']} |")
    md.append("")
    md.append(f"### 3.2 typed disclosure（{len(typed_rows)} 条）\n")
    md.append(f"- verdict: {dict(td_verdict)}")
    md.append(f"- by_type: " + "; ".join(f"{k[0]}={k[1]}:{v}" for k, v in sorted(td_by_type.items())))
    amb_left = [r for r in typed_rows if r["verdict"] == "ambiguous"]
    md.append(f"- 剩余 ambiguous（{len(amb_left)} 条，逐条列理由）:")
    for r in amb_left:
        md.append(f"  - {r['run']} {r['ref_id']} {r['disclosure_type']} {r['value']}: "
                  f"{r['reason']}｜{r['context'][:80]}")
    md.append("")
    md.append(f"### 3.3 cross_basis 556 条重算\n- {dict(cb_stats)}")
    md.append(f"\n### 3.4 invalid_conversion（{len(conv_rows)} 条）\n"
              f"- {dict(Counter(r['category'] for r in conv_rows))}")
    md.append(f"\n### 3.5 executable level（{len(exec_rows)} 条）\n"
              f"- {dict(Counter(r['category'] for r in exec_rows))}\n")
    md.append("> 口径注记（R4）：中位数一律为标准中位数（偶数样本取两中值均值）。\n"
              "> W1 层 executable 类计数上升机制：W1 删除伪命中 ref 后，原先命中\n"
              "> `executable_level_wrong_basis` 的价位失去候选 ref，迁移为\n"
              "> `unbacked_executable_level`；同时 violation 去重键\n"
              "> (kind, ref_ids, detail) 中 ref_ids/bases 变化使少量条目重新计数。\n")
    md.append("")

    # ------------------------------------------------------------------
    # 4. W1–W4
    # ------------------------------------------------------------------
    layer_results: Dict[str, Any] = {}
    prev_status = {r["run"]: r["status"] for r in w0_runs}
    prev_kinds = {r["run"]: r["kinds"] for r in w0_runs}
    for layer, lname in ((1, "W1"), (2, "W2"), (3, "W3"), (4, "W4")):
        runs = []
        kind_tot = Counter()
        unblocked = []
        for p in state_files:
            tag = os.path.basename(p)[:-4]
            sample = tag.split("__")[0]
            st = states[tag]
            g = W.run_layer(st, layer=layer, pool=pools[sample], keep_refs=True)
            ck = Counter(v["kind"] for v in g["violations"])
            kind_tot.update(ck)
            entry = {"run": tag, "status": g["status"], "n_violations": len(g["violations"]),
                     "kinds": dict(ck)}
            if layer == 4:
                entry["pool_bridge"] = g.get("pool_bridge") or []
            # [R3] pass run 中被改写为 derived_estimate 的 ref 逐条列出
            if layer >= 2 and g["status"] == "pass":
                entry["derived_estimate_refs"] = [
                    {"ref_id": r["ref_id"], "value": r["value"],
                     "source": r["source"], "context": r["context"]}
                    for r in g.get("_refs", [])
                    if r.get("basis") == "derived_estimate"
                ]
            runs.append(entry)
            if prev_status.get(tag) == "blocked" and g["status"] == "pass":
                # 最后一个 blocker = 上一层该 run 的 violation kinds
                unblocked.append({"run": tag, "last_blockers": prev_kinds.get(tag)})
        nb = sum(1 for r in runs if r["status"] == "blocked")
        vc = [r["n_violations"] for r in runs]
        # s04/s06/s08/s09 明细
        focus = {}
        for s in ("s04", "s06", "s08", "s09"):
            focus[s] = {r["run"]: {"status": r["status"], "kinds": r["kinds"]}
                        for r in runs if r["run"].startswith(s + "__")}
        layer_results[lname] = {
            "kind_totals": dict(kind_tot),
            "total": sum(kind_tot.values()),
            "blocked": nb, "passed": 50 - nb, "clean": 50 - nb,
            "per_run_min": min(vc), "per_run_median": _median(vc), "per_run_max": max(vc),
            "blocked_to_pass": unblocked,
            "focus_samples": focus,
            "runs": runs,
        }
        prev_status = {r["run"]: r["status"] for r in runs}
        prev_kinds = {r["run"]: r["kinds"] for r in runs}

    report["steps"]["whatif"] = layer_results
    md.append("## 4. W0–W4 what-if 分层\n")
    md.append("| 层 | 总 violation | blocked | pass/clean | 分 kind | blocked→pass |")
    md.append("|---|---|---|---|---|---|")
    md.append(f"| W0 | {sum(w0_kind.values())} | {n_blocked} | {50 - n_blocked} | {dict(w0_kind)} | — |")
    for lname in ("W1", "W2", "W3", "W4"):
        L = layer_results[lname]
        b2p = ", ".join(u["run"] for u in L["blocked_to_pass"]) or "无"
        md.append(f"| {lname} | {L['total']} | {L['blocked']} | {L['passed']} | {L['kind_totals']} | {b2p} |")
    md.append("")
    for lname in ("W1", "W2", "W3", "W4"):
        L = layer_results[lname]
        md.append(f"### {lname} 重点样本（s04/s06/s08/s09 各 5 replicate）")
        for s, rr in L["focus_samples"].items():
            for run, info in sorted(rr.items()):
                md.append(f"- {run}: {info['status']} {info['kinds']}")
        # [R3] pass run 的 derived_estimate refs 逐条列出
        if lname in ("W2", "W3", "W4"):
            md.append(f"\n{lname} pass run 的 derived_estimate refs：")
            for r in L["runs"]:
                if r["status"] == "pass":
                    dr = r.get("derived_estimate_refs") or []
                    md.append(f"- {r['run']}（{len(dr)} 条）：")
                    for d in dr:
                        md.append(f"    - {d['ref_id']} {d['value']} [{d['source']}] {d['context'][:90]}")
        md.append("")

    # ------------------------------------------------------------------
    # 5. 已知坏锚逐层必拦
    # ------------------------------------------------------------------
    anchors = []
    # (a) b188060f fixture（DAV-1142 原句）
    fixture_state = {
        "trade_date": "2026-05-22",
        "news_report": "今日大宗交易成交 78.61 元，较当日收盘 84.03 元折价 6.45%。",
        "trader_investment_plan": "下行风险较大，第一下行目标 78.61 元（大宗折价锚位）。",
    }
    # (b) s06 真实样本锚：大宗交易价进坐标语境
    s06_anchor_runs = []
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        if not tag.startswith("s06__"):
            continue
        st = states[tag]
        txt = " ".join(str(st.get(f) or "") for f in REPORT_FIELDS)
        if re.search(r"大宗交易[^。]{0,30}(?:支撑|压力|元)", txt) or "55.63" in txt:
            s06_anchor_runs.append(tag)
    for layer in range(0, 5):
        g = gate_w0(dict(fixture_state)) if layer == 0 else W.run_layer(fixture_state, layer=layer)
        anchors.append({"anchor": "b188060f_fixture", "layer": f"W{layer}",
                        "status": g["status"],
                        "kinds": dict(Counter(v["kind"] for v in g["violations"]))})
    for tag in s06_anchor_runs:
        for layer in range(0, 5):
            st = states[tag]
            g = gate_w0(dict(st)) if layer == 0 else W.run_layer(st, layer=layer, pool=pools["s06"])
            anchors.append({"anchor": f"s06_real_anchor:{tag}", "layer": f"W{layer}",
                            "status": g["status"],
                            "kinds": dict(Counter(v["kind"] for v in g["violations"]))})
    anchors_ok = all(a["status"] == "blocked" for a in anchors)
    report["steps"]["anchors"] = {"list": anchors, "all_blocked": anchors_ok,
                                  "s06_anchor_runs": s06_anchor_runs}
    md.append("## 5. 已知坏锚逐层结果\n")
    md.append(f"- s06 实锚 run：{s06_anchor_runs}")
    md.append(f"- 全部仍 blocked：{anchors_ok}")
    for a in anchors:
        md.append(f"- {a['anchor']} @ {a['layer']}: {a['status']} {a['kinds']}")
    md.append("")

    with open(os.path.join(OUT, "run_all.json"), "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "SUMMARY.md"), "w") as fh:
        fh.write("\n".join(md) + "\n")
    print("\n".join(md[:60]))
    print(f"\n... written to {OUT}")


def _groupby(rows: List[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    d: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        d[r[key]].append(r)
    return d


if __name__ == "__main__":
    main()
