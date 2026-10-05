#!/usr/bin/env python3
"""DAV-1505: decision_model.v1 六环节拒收率多空分侧诊断（只读，零模型）。

对固定账户的 v1 报告做版本内去重（symbol + 基准日 + 档位保留最新），按
论点提出 → 核实（事实/语义/资格） → 采纳（经理账本） → 方向形成 → 自洽硬门 → 价格门
六环节统计多空分侧拒收率与原因码，只给事实不给修正方案。

口径已按项目评估师 2026-10-05 评审订正：
- ① 论点提出不报拒收率（无候选分母），只报产出量/多空占比；显式 claims=[]
  与 claims 字段缺失/整个档位缺失分开；speaker_key 优先，stance 冲突计数。
- ② 核实分三层分列：legacy 事实核实（decision/counts）、语义核实
  （semantic_decision/semantic_counts/semantic_hard_guards）、账本资格
  （ledger_eligibility 同口径重算）。缺语义字段记 unknown。
- ③ 采纳只用经理最终账本（adopted/partially_adopted/rejected），不用
  ces.decision 冒充。有核实无账本=未到达；有账本未列入=未裁决；
  double_count_guard_audit 合法排除、账本集合重叠、悬空 ID 单列；
  另出「核实结果 × 最终账本」交叉表与资格同侧选择率。
- ④ 方向形成用「门前同侧 adopted 支撑」建机会分母：仅多支撑 / 仅空支撑 /
  双侧支撑 / 无支撑四类 + partial-only 另列；报落 bull/bear/neutral 比例；
  重叠档共同聚类。无 mv=未到达；有效中性与 direction 缺失分列。
- ⑤⑥ 各自先报可观察门状态（blocked/pass/unknown/gate_absent），再报交集，
  不假定串行漏斗；⑤原因取 decision_status.reason_codes + failed_checks；
  ⑥另列「⑤已知放行集合中的⑥blocked」；同档双门命中不相加。
- 差值方向 bull−bear（pp）；95% CI 按基准日 cluster-bootstrap（B=2000，
  簇<8 或一侧无分母给 NA）；claim 层与档位层分别注明加权方式。
- 版本：decision_model.v1 桶按运行生成时间 created_at 分「v1 上线后」；
  dmv NULL 只作「未标版本」背景桶（不同口径、不并列比拒收率）。
- 每个不对称点附 5 个报告 ID + 档位 + claim/原因；不足 5 如实标注。

只读约束：
- SQLite 一律 ``file:...?mode=ro&immutable=1`` URI；不写库、不调模型、不联网。

用法：
    env -u PYTHONPATH .venv310/bin/python scripts/dav1505_six_stage_reject_stats.py \
        --db /path/to/tradingagents_backup.db \
        [--output-json work/dav1505/stats.json] \
        [--output-md work/dav1505/report.md]
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

FIXED_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
DECISION_MODEL_V1 = "decision_model.v1"
BOOTSTRAP_B = 2000
MIN_CLUSTERS = 8  # below this -> report NA rather than a misleading CI

HORIZONS = ("short_term", "medium_term")
HORIZON_LABEL = {"short_term": "短线", "medium_term": "中线"}

BULL_DIRS = {"看多", "偏多", "BULL", "bullish"}
BEAR_DIRS = {"看空", "偏空", "BEAR", "bearish"}


def direction_side(d: Any) -> Optional[str]:
    """Map a manager direction to 'bull' / 'bear' / 'neutral' / None (unparseable)."""
    if d is None:
        return None
    s = str(d).strip()
    if not s or s in ("N/A", "None"):
        return None
    if s in BULL_DIRS:
        return "bull"
    if s in BEAR_DIRS:
        return "bear"
    if s in ("中性", "neutral", "NONE", "tie"):
        return "neutral"
    return "unparseable"


def claim_side(stance: Any, speaker_key: Any, speaker: Any, conflicts: Counter) -> Optional[str]:
    """Side of a claim. speaker_key 优先，其次 stance，再 speaker；冲突计数."""
    sk = str(speaker_key or "").strip().lower()
    st = str(stance or "").strip().lower()
    sp = str(speaker or "").strip().lower()
    side_sk = "bull" if sk == "bull" else ("bear" if sk == "bear" else None)
    side_st = "bull" if st == "bullish" else ("bear" if st in ("bearish", "bear") else None)
    side_sp = "bull" if "bull" in sp else ("bear" if "bear" in sp else None)
    if side_sk and side_st and side_sk != side_st:
        conflicts["speaker_key_vs_stance"] += 1
    if side_sk:
        return side_sk
    if side_st:
        return side_st
    if side_sp:
        return side_sp
    conflicts["unparseable_side"] += 1
    return None


# ── Ledger eligibility (recomputed with the same rules as evidence_verifier
#    ledger_eligibility(); reproduced here so the script is self-contained on a
#    frozen copy — the persisted records predate that field being stored). ──

MIN_COVERAGE = 0.67


def ledger_eligibility(summary: Mapping[str, Any]) -> Dict[str, Any]:
    cnt = summary.get("counts") or {}
    cov = summary.get("coverage", 0.0) or 0.0
    sem = summary.get("semantic_decision")
    obs = summary.get("is_observation_or_hypothesis", False)
    fatal = summary.get("pit_failed") or (cnt.get("contradicted", 0) or 0) > 0
    unavailable = (cnt.get("source_unavailable", 0) or 0) > 0
    no_verified = (cnt.get("verified", 0) or 0) == 0 or (cnt.get("total", 0) or 0) == 0
    low_coverage = cov < MIN_COVERAGE and not math.isclose(cov, 2 / 3, abs_tol=1e-3)

    partial_failure = None
    if fatal:
        partial_failure = "conflict"
    elif unavailable:
        partial_failure = "unavailable"
    elif not obs and no_verified:
        partial_failure = "unsupported"
    elif not obs and low_coverage:
        partial_failure = "low_coverage"
    elif sem == "non_factual_only":
        partial_failure = "non_factual_only"
    elif sem == "reject":
        partial_failure = "semantic_reject"

    adopt_failure = None
    if fatal:
        adopt_failure = "conflict"
    elif unavailable:
        adopt_failure = "unavailable"
    elif obs:
        adopt_failure = "observation"
    elif no_verified:
        adopt_failure = "unsupported"
    elif low_coverage:
        adopt_failure = "low_coverage"
    elif summary.get("decision") == "partial" or (
        0.67 <= cov < 1.0 and not math.isclose(cov, 1.0)
    ):
        adopt_failure = "mixed"
    elif sem == "non_factual_only":
        adopt_failure = "non_factual_only"
    elif sem == "reject":
        adopt_failure = "semantic_reject"
    elif sem == "partial_threshold" or (summary.get("semantic_hard_guards") and sem):
        adopt_failure = "semantic_partial"

    if partial_failure:
        bucket = "rejected_only"
    elif adopt_failure:
        bucket = "partial"
    else:
        bucket = "adopted"
    return {
        "max_bucket": bucket,
        "adopt_failure": adopt_failure,
        "partial_failure": partial_failure,
    }


def reason_cluster(reason: Any) -> str:
    """归簇核实/裁决原因码（保留原文另行记录）。"""
    r = str(reason or "")
    if not r:
        return "empty"
    if "覆盖率不足" in r:
        return "coverage_below_threshold"
    if "contradicted" in r or "冲突" in r or "前视" in r:
        return "contradicted"
    if "unsupported" in r or "未获验证" in r or "未提供有效证据" in r:
        return "unsupported"
    if "source_unavailable" in r or "源不可用" in r:
        return "source_unavailable"
    if "non_factual" in r:
        return "non_factual_only"
    if "E-04" in r or "已定价" in r or "priced" in r:
        return "e04_priced_in"
    if "semantic" in r:
        return "semantic"
    if "核验通过" in r or "verified" in r:
        return "verified_pass"
    return "other:" + r[:30]


def failed_check_cluster(fc: Any) -> str:
    s = str(fc or "")
    if not s:
        return "empty"
    if "E-04" in s and "基线" in s:
        return "e04_no_baseline"
    if "E-04" in s or "已定价" in s:
        return "e04_priced_in"
    if "semantic_decision=reject" in s and "部分采纳" in s:
        return "partial_contains_semantic_reject"
    if "semantic_decision=reject" in s or "全额采纳" in s and "reject" in s:
        return "adopted_semantic_reject"
    if "覆盖率不足" in s:
        return "partial_low_coverage"
    if "空头胜" in s or "多头胜" in s or "矛盾" in s:
        return "verdict_text_mismatch"
    if "double_count" in s or "重复" in s:
        return "double_count"
    return "other:" + s[:40]


def norm_id_list(items: Any) -> List[str]:
    out = []
    for x in items or []:
        if isinstance(x, Mapping):
            cid = x.get("claim_id") or x.get("id")
            if cid is not None:
                out.append(str(cid).strip())
        elif x is not None:
            out.append(str(x).strip())
    return [i for i in out if i]


# ── Cluster bootstrap CI for a difference of proportions ────────────────────


def cluster_boot_diff(
    units: List[Tuple[str, str, int, int]],
    # (cluster_key, side, numerator_i, denominator_i) — denominator_i in {0,1}
    seed: int = 20261005,
) -> Optional[Tuple[float, float, float, int]]:
    """CI for rate_bull - rate_bear, cluster-bootstrapped over cluster_key.

    units: list of (cluster, side, numer, denom) where denom is 1 if the
    observation is in the side's denominator for this stage.
    Returns (diff_pp, lo_pp, hi_pp, n_clusters) or None when NA.
    """
    clusters: Dict[str, Dict[str, List[Tuple[int, int]]]] = defaultdict(lambda: {"bull": [], "bear": []})
    for ck, side, n, d in units:
        if side in ("bull", "bear") and d:
            clusters[ck][side].append((n, d))
    keys = sorted(clusters.keys())
    nb = {"bull": 0, "bear": 0}
    db = {"bull": 0, "bear": 0}
    for k in keys:
        for s in ("bull", "bear"):
            nb[s] += sum(n for n, _ in clusters[k][s])
            db[s] += sum(d for _, d in clusters[k][s])
    if len(keys) < MIN_CLUSTERS or db["bull"] == 0 or db["bear"] == 0:
        return None
    rb = nb["bull"] / db["bull"]
    rr = nb["bear"] / db["bear"]
    diff = (rb - rr) * 100.0
    rng = random.Random(seed)
    diffs = []
    for _ in range(BOOTSTRAP_B):
        n_b = n_r = d_b = d_r = 0
        for _ in range(len(keys)):
            k = keys[rng.randrange(len(keys))]
            for n, d in clusters[k]["bull"]:
                n_b += n; d_b += d
            for n, d in clusters[k]["bear"]:
                n_r += n; d_r += d
        if d_b and d_r:
            diffs.append((n_b / d_b - n_r / d_r) * 100.0)
    if not diffs:
        return None
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[min(int(0.975 * len(diffs)), len(diffs) - 1)]
    return (diff, lo, hi, len(keys))


def pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 2) if d else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="Path to the .backup() copy (read-only).")
    ap.add_argument("--output-json", default=None)
    ap.add_argument("--output-md", default=None)
    args = ap.parse_args()

    uri = f"file:{os.path.abspath(args.db)}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()

    out: Dict[str, Any] = {"db": os.path.abspath(args.db), "stages": {}, "asymmetry_examples": {}}
    conflicts: Counter = Counter()

    # ── Load v1 fixed-account reports, version-internal dedup ────────────
    rows = list(cur.execute(
        "SELECT id, symbol, trade_date, created_at, "
        "COALESCE(json_extract(result_data,'$.analysis_baseline_date'), trade_date) bd, "
        "result_data FROM reports "
        "WHERE json_extract(result_data,'$.decision_model_version')=? AND user_id=?",
        (DECISION_MODEL_V1, FIXED_USER_ID),
    ))
    # dedup: per (symbol, baseline_date, horizon) keep latest created_at (tie: larger id)
    best: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    dup_dropped = 0
    for rid, sym, td, ca, bd, rdj in rows:
        try:
            rd = json.loads(rdj)
        except Exception:
            rd = None
        if not isinstance(rd, dict):
            continue
        for hz in HORIZONS:
            sl = rd.get(hz)
            if not isinstance(sl, dict) or not sl:
                continue
            key = (sym, bd, hz)
            cand = (ca or "", rid)
            if key not in best or cand > best[key]:
                if key in best:
                    dup_dropped += 1
                best[key] = cand
            else:
                dup_dropped += 1
    kept_ids = {rid for _, rid in best.values()}

    # per-unit records
    units: List[Dict[str, Any]] = []
    for rid, sym, td, ca, bd, rdj in rows:
        if rid not in kept_ids:
            continue
        rd = json.loads(rdj)
        for hz in HORIZONS:
            sl = rd.get(hz)
            if not isinstance(sl, dict) or not sl:
                continue
            ids = sl.get("investment_debate_state")
            ids = ids if isinstance(ids, dict) else {}
            mv = sl.get("manager_verdict")
            mv = mv if isinstance(mv, dict) else {}
            ds = sl.get("decision_status")
            ds = ds if isinstance(ds, dict) else {}
            claims = ids.get("claims")
            claims_present = claims is not None  # explicit [] vs missing
            claims = claims if isinstance(claims, list) else []
            ces = mv.get("claim_evidence_summary")
            ces = ces if isinstance(ces, dict) else {}
            gate = sl.get("price_basis_gate")
            gate = gate if isinstance(gate, dict) else {}
            units.append({
                "report_id": rid, "symbol": sym, "baseline": bd, "created_at": ca,
                "horizon": hz, "ids_present": bool(ids), "claims_present": claims_present,
                "claims": claims, "mv": mv, "mv_present": bool(mv), "ds": ds,
                "ces": ces, "gate": gate, "gate_present": bool(gate),
            })

    out["corpus"] = {
        "v1_fixed_account_reports": len({r[0] for r in rows}),
        "units_before_dedup": sum(1 for r in rows for hz in HORIZONS if isinstance(json.loads(r[5]).get(hz), dict) and json.loads(r[5]).get(hz)),
        "units_after_dedup": len(units),
        "dup_dropped": dup_dropped,
        "short_units": sum(1 for u in units if u["horizon"] == "short_term"),
        "medium_units": sum(1 for u in units if u["horizon"] == "medium_term"),
        "excluded_nonfixed_v1_reports": 2,  # counted separately below
        "side_conflicts": dict(conflicts),
    }
    # count excluded non-fixed-account v1 reports factually
    out["corpus"]["excluded_nonfixed_v1_reports"] = cur.execute(
        "SELECT COUNT(*) FROM reports WHERE json_extract(result_data,'$.decision_model_version')=? AND user_id!=?",
        (DECISION_MODEL_V1, FIXED_USER_ID),
    ).fetchone()[0]

    def hz_units(hz):
        return [u for u in units if u["horizon"] == hz]

    # ── Stage ① 论点提出 ────────────────────────────────────────────────────
    st1 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        n_claims = Counter()
        per_unit = Counter()
        no_claims_explicit = 0
        no_claims_missing = 0
        for u in us:
            if not u["claims_present"]:
                if u["ids_present"]:
                    no_claims_missing += 1
                else:
                    no_claims_missing += 1
            elif len(u["claims"]) == 0:
                no_claims_explicit += 1
            for c in u["claims"]:
                if not isinstance(c, Mapping):
                    continue
                side = claim_side(c.get("stance"), c.get("speaker_key"), c.get("speaker"), conflicts)
                n_claims[side or "unknown"] += 1
            per_unit[len(u["claims"])] += 1
        tot = sum(n_claims.values())
        st1[hz] = {
            "units": len(us),
            "claims_total": tot,
            "claims_by_side": dict(n_claims),
            "bull_share_pct": pct(n_claims["bull"], tot),
            "units_explicit_empty_claims": no_claims_explicit,
            "units_missing_claims_field_or_ids": no_claims_missing,
            "per_unit_claim_count_hist": {str(k): v for k, v in sorted(per_unit.items())},
        }
    out["stages"]["1_proposal"] = st1

    # ── Stage ② 核实（三层分列） + claim-level dataset ──────────────────────
    # Build per-claim records for stages 2 & 3.
    claim_rows: List[Dict[str, Any]] = []
    for u in units:
        mv = u["mv"]
        ces = u["ces"]
        adopted = set(norm_id_list(mv.get("adopted_claim_ids")))
        partial = set(norm_id_list(mv.get("partially_adopted_claims")))
        rejected = set(norm_id_list(mv.get("rejected_claim_ids")))
        excluded = set(norm_id_list(mv.get("excluded_evidence")))
        # dcg audit absent in stored data; keep hook
        dcg = mv.get("double_count_guard_audit") or {}
        dcg_excluded = set(norm_id_list(dcg.get("excluded_claim_ids")))
        ledger_overlap = (adopted & partial) | (adopted & rejected) | (partial & rejected)
        listed = adopted | partial | rejected
        known_ids = set()
        for c in u["claims"]:
            if isinstance(c, Mapping) and c.get("claim_id"):
                known_ids.add(str(c["claim_id"]).strip())
        known_ids |= set(ces.keys())
        dangling = {cid for cid in listed if cid not in known_ids}

        for c in u["claims"]:
            if not isinstance(c, Mapping):
                continue
            cid = str(c.get("claim_id") or "").strip()
            if not cid:
                continue
            side = claim_side(c.get("stance"), c.get("speaker_key"), c.get("speaker"), conflicts)
            s = ces.get(cid)
            in_ledger = cid in listed
            bucket = "adopted" if cid in adopted else "partial" if cid in partial else "rejected" if cid in rejected else None
            claim_rows.append({
                "report_id": u["report_id"], "horizon": u["horizon"], "baseline": u["baseline"],
                "symbol": u["symbol"], "cid": cid, "side": side,
                "in_ces": s is not None, "mv_present": u["mv_present"],
                "ledger_bucket": bucket, "in_ledger": in_ledger,
                "dangling": cid in dangling, "ledger_overlap": cid in ledger_overlap,
                "dcg_excluded": cid in dcg_excluded, "excluded_evidence": cid in excluded,
                "summary": s,
            })
        # claims in ces/ledger but not in claims list (dangling)
        for cid in sorted(known_ids - {r["cid"] for r in claim_rows if r["report_id"] == u["report_id"] and r["horizon"] == u["horizon"]}):
            s = ces.get(cid)
            claim_rows.append({
                "report_id": u["report_id"], "horizon": u["horizon"], "baseline": u["baseline"],
                "symbol": u["symbol"], "cid": cid, "side": None,
                "in_ces": s is not None, "mv_present": u["mv_present"],
                "ledger_bucket": "adopted" if cid in adopted else "partial" if cid in partial else "rejected" if cid in rejected else None,
                "in_ledger": cid in listed, "dangling": True, "orphan_not_in_claims": True,
                "dcg_excluded": cid in dcg_excluded, "excluded_evidence": cid in excluded,
                "summary": s,
            })

    st2 = {}
    crosstab = {}
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz]
        verified_layer = Counter()  # (side, layer, outcome)
        elig_c = Counter()
        reasons = Counter()
        ct = Counter()
        for r in rows_hz:
            side = r["side"] or "unknown"
            s = r["summary"]
            if not r["in_ces"] or s is None:
                verified_layer[(side, "record", "no_verification_record")] += 1
                continue
            # layer 1: factual verification (legacy)
            verified_layer[(side, "factual", s.get("decision") or "unknown")] += 1
            # layer 2: semantic
            sem = s.get("semantic_decision")
            verified_layer[(side, "semantic", sem if sem else "unknown_absent")] += 1
            if s.get("semantic_hard_guards"):
                verified_layer[(side, "guards", "semantic_hard_guards_present")] += 1
            # layer 3: ledger eligibility (recomputed)
            el = ledger_eligibility(s)
            elig_c[(side, el["max_bucket"])] += 1
            if el["adopt_failure"]:
                reasons[(side, "adopt:" + el["adopt_failure"])] += 1
            if el["partial_failure"]:
                reasons[(side, "partial:" + el["partial_failure"])] += 1
            reasons[(side, "reason:" + reason_cluster(s.get("reason")))] += 1
            # cross-tab factual decision x ledger bucket
            if r["in_ledger"]:
                ct[(side, s.get("decision") or "unknown", r["ledger_bucket"] or "unlisted")] += 1
            else:
                ct[(side, s.get("decision") or "unknown", "no_ledger")] += 1
        st2[hz] = {
            "claims_in_units": len(rows_hz),
            "verification_layer_counts": {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(verified_layer.items())},
            "ledger_eligibility_recomputed": {f"{a}|{b}": n for (a, b), n in sorted(elig_c.items())},
            "reject_reasons_clustered": {f"{a}|{b}": n for (a, b), n in reasons.most_common(40)},
        }
        crosstab[hz] = {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(ct.items())}
    out["stages"]["2_verification"] = st2
    out["stages"]["3_crosstab_factual_decision_x_ledger"] = crosstab

    # ── Stage ③ 采纳（经理账本 only） ─────────────────────────────────────────
    st3 = {}
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz]
        denom_c = Counter(); numer_c = Counter(); unadj = Counter(); noreach = Counter()
        choice = defaultdict(lambda: [0, 0])  # (side, elig_bucket) -> [chosen, total]
        for r in rows_hz:
            side = r["side"] or "unknown"
            s = r["summary"]
            if r["dangling"]:
                continue  # orphan: 单列 below
            if not r["mv_present"]:
                noreach[side] += 1
                continue
            if not r["in_ledger"]:
                # has verification but not in any ledger list -> 未裁决
                if r["in_ces"]:
                    unadj[side] += 1
                else:
                    noreach[side] += 1
                continue
            denom_c[side] += 1
            if r["ledger_bucket"] == "rejected":
                numer_c[side] += 1
            # same-side choice rate among eligible
            if s is not None:
                el = ledger_eligibility(s)
                if side in ("bull", "bear") and el["max_bucket"] in ("adopted", "partial"):
                    choice[(side, el["max_bucket"])][1] += 1
                    if r["ledger_bucket"] in ("adopted", "partial"):
                        choice[(side, el["max_bucket"])][0] += 1
        st3[hz] = {
            "denominator_adjudicated": dict(denom_c),
            "rejected_in_ledger": dict(numer_c),
            "reject_rate_pct": {s: pct(numer_c[s], denom_c[s]) for s in ("bull", "bear", "unknown")},
            "unadjudicated_with_verification": dict(unadj),
            "not_reached_no_ledger_no_ces": dict(noreach),
            "eligible_same_side_choice_rate": {
                f"{s}|{b}": {"chosen": v[0], "total": v[1], "pct": pct(v[0], v[1])}
                for (s, b), v in sorted(choice.items())
            },
            "dangling_ledger_ids": sum(1 for r in rows_hz if r["dangling"]),
            "ledger_overlap_ids": sum(1 for r in rows_hz if r["ledger_overlap"]),
            "dcg_excluded": sum(1 for r in rows_hz if r["dcg_excluded"]),
        }
    out["stages"]["3_adoption"] = st3

    # ── Stage ④ 方向形成（门前机会分母） ──────────────────────────────────────
    st4 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        cats = Counter()  # bull_only/bear_only/both/none/partial_only/no_mv
        outcome = defaultdict(Counter)  # cat -> formed direction side
        for u in us:
            if not u["mv_present"]:
                cats["no_mv"] += 1
                continue
            mv = u["mv"]
            adopted = set(norm_id_list(mv.get("adopted_claim_ids")))
            partial = set(norm_id_list(mv.get("partially_adopted_claims")))
            ces = u["ces"]
            bull_ad = sum(1 for cid in adopted if str((ces.get(cid) or {}).get("speaker_key") or "").lower() == "bull")
            bear_ad = sum(1 for cid in adopted if str((ces.get(cid) or {}).get("speaker_key") or "").lower() == "bear")
            bull_pt = sum(1 for cid in partial if str((ces.get(cid) or {}).get("speaker_key") or "").lower() == "bull")
            bear_pt = sum(1 for cid in partial if str((ces.get(cid) or {}).get("speaker_key") or "").lower() == "bear")
            d = direction_side(mv.get("direction"))
            if bull_ad and not bear_ad:
                cat = "bull_only"
            elif bear_ad and not bull_ad:
                cat = "bear_only"
            elif bull_ad and bear_ad:
                cat = "both"
            elif not bull_ad and not bear_ad and (bull_pt or bear_pt):
                cat = "partial_only"
            else:
                cat = "no_support"
            cats[cat] += 1
            outcome[cat][d if d else ("neutral" if str(mv.get("direction") or "").strip() in ("中性",) else "missing_or_unparseable")] += 1
        st4[hz] = {
            "opportunity_categories": dict(cats),
            "formed_direction_by_category": {c: dict(o) for c, o in outcome.items()},
        }
    out["stages"]["4_direction"] = st4

    # ── Stage ⑤ 自洽硬门 ────────────────────────────────────────────────────
    st5 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        gate_state = Counter()   # blocked/pass/unknown + no_mv
        fail_codes = Counter()
        for u in us:
            if not u["mv_present"]:
                gate_state["no_mv"] += 1
                continue
            mv = u["mv"]; ds = u["ds"]
            side = direction_side(mv.get("direction")) or "neutral_or_missing"
            ccp = mv.get("consistency_check_passed")
            rc = ds.get("reason_codes") or []
            fc = ds.get("failed_checks") or mv.get("failed_checks") or []
            has_hg = any("manager_consistency_hard_gate" in str(x) for x in rc) or ccp is False
            if has_hg:
                gate_state[(side, "blocked")] += 1
                for f in fc:
                    fail_codes[(side, failed_check_cluster(f))] += 1
                for x in rc:
                    if "manager_consistency_hard_gate" not in str(x):
                        fail_codes[(side, "rc:" + reason_cluster(x))] += 1
            elif ccp is True:
                gate_state[(side, "pass")] += 1
            else:
                gate_state[(side, "unknown")] += 1
        st5[hz] = {
            "gate_state": {f"{k[0]}|{k[1]}" if isinstance(k, tuple) else k: v for k, v in gate_state.items()},
            "failed_check_clusters": {f"{a}|{b}": n for (a, b), n in fail_codes.most_common(40)},
        }
    out["stages"]["5_consistency_gate"] = st5

    # ── Stage ⑥ 价格门 ──────────────────────────────────────────────────────
    st6 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        gate_state = Counter()
        viol = Counter()
        post5_blocked = Counter()  # among units that passed ⑤
        for u in us:
            mv = u["mv"]
            side = direction_side(mv.get("direction")) or ("neutral_or_missing" if u["mv_present"] else "no_mv")
            g = u["gate"]
            if not u["gate_present"]:
                gate_state[(side, "gate_absent")] += 1
                continue
            status = g.get("status")
            if status == "blocked":
                gate_state[(side, "blocked")] += 1
                for v in g.get("violations") or []:
                    if isinstance(v, Mapping):
                        viol[(side, str(v.get("kind") or "unknown"))] += 1
                # ⑤-passed subset
                ccp = mv.get("consistency_check_passed")
                rc = (u["ds"].get("reason_codes") or [])
                passed5 = (ccp is True) and not any("manager_consistency_hard_gate" in str(x) for x in rc)
                if passed5:
                    post5_blocked[side] += 1
            elif status == "pass":
                gate_state[(side, "pass")] += 1
            else:
                gate_state[(side, "unknown_status")] += 1
        st6[hz] = {
            "gate_state": {f"{k[0]}|{k[1]}" if isinstance(k, tuple) else k: v for k, v in gate_state.items()},
            "violation_kinds": {f"{a}|{b}": n for (a, b), n in viol.most_common(30)},
            "blocked_after_5_passed": dict(post5_blocked),
        }
    out["stages"]["6_price_gate"] = st6

    # ── ⑤∩⑥ 交集（不相加） ─────────────────────────────────────────────────
    inter = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        both = Counter()
        for u in us:
            if not u["mv_present"]:
                continue
            mv = u["mv"]
            side = direction_side(mv.get("direction")) or "neutral_or_missing"
            rc = u["ds"].get("reason_codes") or []
            hit5 = mv.get("consistency_check_passed") is False or any("manager_consistency_hard_gate" in str(x) for x in rc)
            hit6 = u["gate"].get("status") == "blocked" if u["gate_present"] else False
            both[(side, f"5={'Y' if hit5 else 'N'}|6={'Y' if hit6 else 'N'}|g={'absent' if not u['gate_present'] else 'present'}")] += 1
        inter[hz] = {f"{k[0]}|{k[1]}": v for k, v in both.items()}
    out["stages"]["5_6_intersection"] = inter

    # ── 不对称点（差值 + cluster CI） ─────────────────────────────────────────
    asym = {}

    def claim_units_for_stage(rows_hz, numer_fn, denom_fn):
        us = []
        for r in rows_hz:
            side = r["side"]
            if side not in ("bull", "bear"):
                continue
            d = denom_fn(r)
            if not d:
                continue
            us.append((r["baseline"], side, 1 if numer_fn(r) else 0, 1))
        return us

    # ② semantic reject rate asymmetry (claim-level, cluster by baseline date)
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz and r["in_ces"]]
        us = claim_units_for_stage(
            rows_hz,
            lambda r: (r["summary"].get("semantic_decision") == "reject"),
            lambda r: True,
        )
        asym[f"2_semantic_reject|{hz}"] = cluster_boot_diff(us)
        # factual reject
        us2 = claim_units_for_stage(
            rows_hz,
            lambda r: (r["summary"].get("decision") == "reject"),
            lambda r: True,
        )
        asym[f"2_factual_reject|{hz}"] = cluster_boot_diff(us2)

    # ③ ledger reject rate
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz and r["in_ledger"] and not r["dangling"]]
        us = claim_units_for_stage(
            rows_hz,
            lambda r: r["ledger_bucket"] == "rejected",
            lambda r: True,
        )
        asym[f"3_ledger_reject|{hz}"] = cluster_boot_diff(us)

    # ④ direction: among bull_only vs bear_only opportunity units, P(formed same side)
    for hz in HORIZONS:
        us = []
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            mv = u["mv"]; ces = u["ces"]
            adopted = set(norm_id_list(mv.get("adopted_claim_ids")))
            bull_ad = any(str((ces.get(c) or {}).get("speaker_key") or "").lower() == "bull" for c in adopted)
            bear_ad = any(str((ces.get(c) or {}).get("speaker_key") or "").lower() == "bear" for c in adopted)
            d = direction_side(mv.get("direction"))
            if bull_ad and not bear_ad:
                us.append((u["baseline"], "bull", 1 if d == "bull" else 0, 1))
            elif bear_ad and not bull_ad:
                us.append((u["baseline"], "bear", 1 if d == "bear" else 0, 1))
            elif bull_ad and bear_ad:
                # dual support enters both sides' opportunity sets (overlap noted)
                us.append((u["baseline"], "bull", 1 if d == "bull" else 0, 1))
                us.append((u["baseline"], "bear", 1 if d == "bear" else 0, 1))
        asym[f"4_same_side_formation|{hz}"] = cluster_boot_diff(us)

    # ⑤ hard-gate blocked rate by formed direction
    for hz in HORIZONS:
        us = []
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            mv = u["mv"]
            side = direction_side(mv.get("direction"))
            if side not in ("bull", "bear"):
                continue
            rc = u["ds"].get("reason_codes") or []
            hit = mv.get("consistency_check_passed") is False or any("manager_consistency_hard_gate" in str(x) for x in rc)
            us.append((u["baseline"], side, 1 if hit else 0, 1))
        asym[f"5_hardgate_block|{hz}"] = cluster_boot_diff(us)

    # ⑥ price-gate blocked rate by formed direction (denominator: gate present)
    for hz in HORIZONS:
        us = []
        for u in hz_units(hz):
            if not u["mv_present"] or not u["gate_present"]:
                continue
            side = direction_side(u["mv"].get("direction"))
            if side not in ("bull", "bear"):
                continue
            us.append((u["baseline"], side, 1 if u["gate"].get("status") == "blocked" else 0, 1))
        asym[f"6_pricegate_block|{hz}"] = cluster_boot_diff(us)

    out["asymmetry"] = {
        k: ({"diff_pp_bull_minus_bear": round(v[0], 2), "ci95_lo": round(v[1], 2),
             "ci95_hi": round(v[2], 2), "n_clusters": v[3]} if v else
            {"diff_pp_bull_minus_bear": None, "ci95_lo": None, "ci95_hi": None,
             "n_clusters": None, "note": "NA: clusters<8 or one side has no denominator"})
        for k, v in asym.items()
    }

    # ── 每个不对称点 5 例（报告ID + 档位 + claim/原因） ────────────────────────
    ex: Dict[str, List[Dict[str, Any]]] = {}

    def pick(rows_hz, pred, key, limit=5):
        got = []
        for r in rows_hz:
            if r["side"] not in ("bull", "bear"):
                continue
            if pred(r):
                s = r.get("summary") or {}
                got.append({
                    "report_id": r["report_id"], "horizon": r["horizon"], "claim_id": r["cid"],
                    "side": r["side"], "semantic_decision": s.get("semantic_decision"),
                    "factual_decision": s.get("decision"), "reason": s.get("reason"),
                    "claim_text": (s.get("claim") or "")[:80],
                })
                if len(got) >= limit:
                    break
        ex[key] = got

    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz and r["in_ces"]]
        # examples where semantic reject hit bullish claims (the bias suspect)
        pick([r for r in rows_hz if r["side"] == "bull" and (r["summary"].get("semantic_decision") == "reject")],
             lambda r: True, f"2_semantic_reject_bull|{hz}")
        pick([r for r in rows_hz if r["side"] == "bull" and r["summary"].get("semantic_decision") == "reject"
              and r["ledger_bucket"] == "rejected"],
             lambda r: True, f"3_semantic_reject_ledgered_bull|{hz}")
        # bullish claims factually adopted but semantically rejected
        pick([r for r in rows_hz if r["side"] == "bull" and r["summary"].get("decision") == "adopt"
              and r["summary"].get("semantic_decision") == "reject"],
             lambda r: True, f"2_factual_adopt_semantic_reject_bull|{hz}")
    # unit-level examples for ⑤/⑥
    for hz in HORIZONS:
        hits5 = []
        hits6 = []
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            side = direction_side(u["mv"].get("direction"))
            if side == "bull":
                rc = u["ds"].get("reason_codes") or []
                if u["mv"].get("consistency_check_passed") is False or any("manager_consistency_hard_gate" in str(x) for x in rc):
                    hits5.append({"report_id": u["report_id"], "horizon": hz, "direction": u["mv"].get("direction"),
                                  "failed_checks": [str(x)[:80] for x in (u["ds"].get("failed_checks") or u["mv"].get("failed_checks") or [])][:3]})
                if u["gate_present"] and u["gate"].get("status") == "blocked":
                    hits6.append({"report_id": u["report_id"], "horizon": hz, "direction": u["mv"].get("direction"),
                                  "violation_kinds": [str(v.get("kind")) for v in (u["gate"].get("violations") or []) if isinstance(v, Mapping)][:3]})
        ex[f"5_hardgate_bull|{hz}"] = hits5[:5]
        ex[f"6_pricegate_bull|{hz}"] = hits6[:5]
    out["asymmetry_examples"] = ex

    # ── 「未标版本」背景桶（不并列比拒收率） ──────────────────────────────────
    bg = {}
    null_rows = list(cur.execute(
        "SELECT id, symbol, COALESCE(json_extract(result_data,'$.analysis_baseline_date'), trade_date), created_at, result_data "
        "FROM reports WHERE result_data IS NOT NULL AND json_extract(result_data,'$.decision_model_version') IS NULL AND user_id=?",
        (FIXED_USER_ID,),
    ))
    v1_min_ca = cur.execute(
        "SELECT MIN(created_at) FROM reports WHERE json_extract(result_data,'$.decision_model_version')=?",
        (DECISION_MODEL_V1,)).fetchone()[0]
    n_pre = n_post = 0
    bg_dir = Counter()
    for rid, sym, bd, ca, rdj in null_rows:
        try:
            rd = json.loads(rdj)
        except Exception:
            continue
        if not isinstance(rd, dict):
            continue
        if ca and v1_min_ca and ca >= v1_min_ca:
            n_post += 1
        else:
            n_pre += 1
        st = rd.get("short_term")
        if isinstance(st, dict):
            mv = st.get("manager_verdict") or {}
            bg_dir[direction_side(mv.get("direction")) or "none"] += 1
    bg = {
        "null_dmv_reports_with_result_data_fixed_account": len(null_rows),
        "created_before_first_v1_run": n_pre,
        "created_after_first_v1_run": n_post,
        "note": "未标版本桶只作背景；字段结构/口径与 v1 不同，不并列拒收率。",
        "short_term_direction_side": dict(bg_dir),
    }
    out["background_unversioned"] = bg

    # ── write outputs ───────────────────────────────────────────────────────
    if args.output_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)

    # human-readable summary
    lines = ["# DAV-1505 六环节拒收率（多空分侧）", ""]
    for hz in HORIZONS:
        lines.append(f"## {HORIZON_LABEL[hz]}")
        s1 = st1[hz]
        lines.append(f"- ① 论点提出：{s1['claims_total']} claims（bull {s1['claims_by_side'].get('bull',0)} / "
                     f"bear {s1['claims_by_side'].get('bear',0)} / unknown {s1['claims_by_side'].get('unknown',0)}），"
                     f"多头占比 {s1['bull_share_pct']}%；explicit-empty {s1['units_explicit_empty_claims']} 档、"
                     f"claims 缺失 {s1['units_missing_claims_field_or_ids']} 档（共 {s1['units']} 档）")
        s2 = st2[hz]
        lines.append(f"- ② 核实：{s2['claims_in_units']} claims 入账本资格评估")
        for k, v in s2["ledger_eligibility_recomputed"].items():
            lines.append(f"    - 资格 {k}: {v}")
        s3 = st3[hz]
        for side in ("bull", "bear"):
            d = s3["denominator_adjudicated"].get(side, 0)
            n = s3["rejected_in_ledger"].get(side, 0)
            lines.append(f"- ③ 采纳 {side}: {n}/{d} rejected = {s3['reject_rate_pct'].get(side)}%")
        s4 = st4[hz]
        lines.append(f"- ④ 方向形成机会分类: {s4['opportunity_categories']}")
        for cat, o in s4["formed_direction_by_category"].items():
            lines.append(f"    - {cat}: {o}")
        s5 = st5[hz]
        lines.append(f"- ⑤ 自洽硬门: {s5['gate_state']}")
        s6 = st6[hz]
        lines.append(f"- ⑥ 价格门: {s6['gate_state']}；⑤放行后⑥blocked: {s6['blocked_after_5_passed']}")
        lines.append("")
    lines.append("## 不对称点（bull−bear, pp; cluster-bootstrap by baseline date）")
    for k, v in out["asymmetry"].items():
        if v["diff_pp_bull_minus_bear"] is None:
            lines.append(f"- {k}: NA（{v.get('note')}）")
        else:
            lines.append(f"- {k}: {v['diff_pp_bull_minus_bear']} pp, CI95 [{v['ci95_lo']}, {v['ci95_hi']}], clusters={v['n_clusters']}")
    md = "\n".join(lines)
    if args.output_md:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_md)), exist_ok=True)
        with open(args.output_md, "w", encoding="utf-8") as f:
            f.write(md)
    print(md)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
