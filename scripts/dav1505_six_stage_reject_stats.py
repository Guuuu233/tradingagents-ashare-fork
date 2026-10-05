#!/usr/bin/env python3
"""DAV-1505/DAV-1531: decision_model.v1 六环节拒收率多空分侧诊断（只读，零模型）。

对固定账户的 v1 报告做版本内去重（symbol + 基准日 + 档位保留最新），按
论点提出 → 核实（事实/语义/资格） → 采纳（经理账本） → 方向形成 → 自洽硬门 → 价格门
六环节统计多空分侧拒收率与原因码，只给事实不给修正方案。

口径按 DAV-1529 评审（2026-10-05）订正——相对 c37c1a7 原版的修正：

- 样本框：整档位缺失单独记账（80 份报告短/中线档位整体不存在），缺失覆盖
  度不再以「5/159」式的局部分母冒充整个报告样本框。
- ② 核实三层分列不变；条件率分母改为「已实施语义核实的 claim」，未实施
  （semantic_decision 缺失/NULL）的并列报覆盖度，不再计入分母；补
  semantic_counts 细项汇总（provenance_blocked / e04_unsupported 等，多原因
  可重叠）；保留原始原因码→归簇映射，不归一即弃原文；明确副本未持久化
  ledger_eligibility 字段，资格为同口径重算（不冒充当时持久化资格）。
- ③ 采纳：审计来源改为 ids.claim_cluster_metrics.double_count_guard_audit
  （契约与 decision_status.py:860-883 一致；mv.* / mv_in_deb.* 为 fallback），
  此前读 mv.double_count_guard_audit 恒为空。合法折叠（dcg_excluded）与
  「有核实未列账本」分开记账；原始列表口径与裁剪后有效账本口径并列，标注
  口径名；资格机会分母补全集/合法排除/剩余机会/选择量四组数。
- ④ 方向形成：明确 −36.99pp 估计对象 = 含 both 重叠档的共同进入口径
  (59/273 vs 109/186)；仅侧桶 (53/197 vs 80/110 = −45.82pp) 另列不混排；
  措辞只描述「提出量接近、④形成率差异明显」，不主张「偏空不来自提出侧」
  「最大可观测来源」「④处突变」等因果结论（各环分母非同一串行总体）；
  中线 CI 含 0 写「未见显著差异」。partial_only/no_support 补裁剪后口径
  （短 87→67 / 69→52，no_support 79→99 / 71→88）。
- ⑤⑥ 先分门再交集，不相加；blocked_after_5_passed 单独列。
- NULL 版本桶：报「未标版本」，版本内去重后 59 报告/118 档，不能与 225
  份原始报告同权；222/3 划分锚点是首条 v1 记录 created_at
  (2026-09-05 13:40:49.037043)，不是可核验生产上线时刻（核验锚点：无）；
  补运行生成月 × 行情基准月份分层产物。
- asymmetry_examples 补 4_same_side_formation|{hz} 键；每点 5 个独立报告
  ID，不足 5 如实标 distinct_reports 数量。

只读约束：
- SQLite 一律 ``file:...?mode=ro&immutable=1`` URI；不写库、不调模型、不联网。

用法：
    env -u PYTHONPATH .venv310/bin/python scripts/dav1505_six_stage_reject_stats.py \
        --db /path/to/tradingagents_backup.db \
        [--output-json dav1505/out/stats.json] \
        [--output-md dav1505/out/report.md]
"""

import argparse
import json
import math
import os
import random
import resource
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Tuple

FIXED_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
DECISION_MODEL_V1 = "decision_model.v1"
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20261005
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
#    frozen copy — the persisted records predate that field being stored, so
#    NOTHING in ces entries carries a persisted ledger_eligibility value and
#    the recompute must not be presented as the persisted eligibility). ──

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
    """归簇核实/裁决原因码（原文同时保留，映射另行产出）。"""
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
    if "semantic_decision=reject" in s or ("全额采纳" in s and "reject" in s):
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


def dcg_excluded_ids(ids: Mapping[str, Any], mv: Mapping[str, Any]) -> List[str]:
    """dcg 审计来源契约与 decision_status.py:860-883 一致：
    ids.claim_cluster_metrics → mv.claim_cluster_metrics →
    mv.investment_debate_state.claim_cluster_metrics 顺序收集。"""
    mv_in_deb = mv.get("investment_debate_state")
    out: List[str] = []
    for src in (
        ids.get("claim_cluster_metrics") if isinstance(ids, Mapping) else None,
        mv.get("claim_cluster_metrics") if isinstance(mv, Mapping) else None,
        (mv_in_deb or {}).get("claim_cluster_metrics") if isinstance(mv_in_deb, Mapping) else None,
    ):
        if isinstance(src, Mapping):
            audit = src.get("double_count_guard_audit")
            if isinstance(audit, Mapping):
                out.extend(norm_id_list(audit.get("excluded_claim_ids")))
    return out


# Keys retained from each claim / claim_evidence_summary entry. Anything else
# (verified_evidence arrays, proposition_audit text, ...) is dropped at extract
# time so the full result_data never stays resident after the row is parsed.
CLAIM_KEEP = ("claim_id", "stance", "speaker_key", "speaker")
CES_KEEP = (
    "decision", "semantic_decision", "semantic_counts", "semantic_hard_guards",
    "counts", "coverage", "pit_failed", "is_observation_or_hypothesis",
    "reason", "claim", "speaker_key", "stance", "speaker",
)
MV_KEEP = (
    "direction", "consistency_check_passed", "adopted_claim_ids",
    "partially_adopted_claims", "rejected_claim_ids", "excluded_evidence",
    "failed_checks",
)
DS_KEEP = ("reason_codes", "failed_checks")
GATE_KEEP = ("status", "violations")


def _trim(src: Any, keys: Tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(src, Mapping):
        return {}
    return {k: src.get(k) for k in keys if k in src}


def extract_unit_parts(sl: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract only the fields the six-stage stats need from one horizon slot,
    so the caller can release the full result_data dict immediately."""
    ids = sl.get("investment_debate_state")
    ids = ids if isinstance(ids, dict) else {}
    mv = sl.get("manager_verdict")
    mv = mv if isinstance(mv, dict) else {}
    ds = sl.get("decision_status")
    ds = ds if isinstance(ds, dict) else {}
    gate = sl.get("price_basis_gate")
    gate = gate if isinstance(gate, dict) else {}

    claims_raw = ids.get("claims")
    claims_present = claims_raw is not None
    claims = []
    if isinstance(claims_raw, list):
        for c in claims_raw:
            if isinstance(c, Mapping):
                claims.append(_trim(c, CLAIM_KEEP))
    ces_raw = mv.get("claim_evidence_summary")
    ces = {}
    if isinstance(ces_raw, Mapping):
        for cid, s in ces_raw.items():
            if isinstance(s, Mapping):
                ces[str(cid).strip()] = _trim(s, CES_KEEP)
    return {
        "ids_present": bool(ids),
        "claims_present": claims_present,
        "claims": claims,
        "mv": _trim(mv, MV_KEEP),
        "mv_present": bool(mv),
        "ds": _trim(ds, DS_KEEP),
        "ces": ces,
        "gate": _trim(gate, GATE_KEEP),
        "gate_present": bool(gate),
        "dcg_excluded": set(dcg_excluded_ids(ids, mv)),
    }


# ── Cluster bootstrap CI for a difference of proportions ────────────────────


def cluster_boot_diff(
    units: List[Tuple[str, str, int, int]],
    # (cluster_key, side, numerator_i, denominator_i) — denominator_i in {0,1}
    seed: int = BOOTSTRAP_SEED,
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
    # 逐行流式处理：result_data 单条可达 MB 级，绝不 list(cur.execute(...)).
    # 每行解析后立即裁剪成 unit 字典（只保留六环节所需字段），原 result_data
    # dict 在同一迭代内丢弃——进程峰值内存 ≈ 一行 result_data + 累积小聚合。
    # dedup 的胜者 unit 随 best[key] 一并保留；被后行者顶掉的中间胜者即被替换。
    best: Dict[Tuple[str, str, str], Tuple[str, str, Dict[str, Any]]] = {}
    dup_dropped = 0
    whole_missing = {hz: 0 for hz in HORIZONS}
    whole_missing_report_ids = {hz: [] for hz in HORIZONS}
    v1_report_ids = set()
    units_before_dedup = 0

    cur.execute(
        "SELECT id, symbol, trade_date, created_at, "
        "COALESCE(json_extract(result_data,'$.analysis_baseline_date'), trade_date), "
        "result_data FROM reports "
        "WHERE json_extract(result_data,'$.decision_model_version')=? AND user_id=?",
        (DECISION_MODEL_V1, FIXED_USER_ID),
    )
    for rid, sym, td, ca, bd, rdj in cur:
        v1_report_ids.add(rid)
        try:
            rd = json.loads(rdj)
        except Exception:
            rd = None
        if not isinstance(rd, dict):
            continue
        for hz in HORIZONS:
            sl = rd.get(hz)
            if not isinstance(sl, dict) or not sl:
                whole_missing[hz] += 1
                whole_missing_report_ids[hz].append(rid)
                continue
            units_before_dedup += 1
            key = (sym, bd, hz)
            cand = (ca or "", rid)
            if key not in best or cand > best[key][:2]:
                if key in best:
                    dup_dropped += 1
                parts = extract_unit_parts(sl)
                parts.update({
                    "report_id": rid, "symbol": sym, "baseline": bd,
                    "created_at": ca, "horizon": hz,
                })
                best[key] = (ca or "", rid, parts)
            else:
                dup_dropped += 1
        del rd  # release full result_data before next row

    units: List[Dict[str, Any]] = [v[2] for v in best.values()]

    out["corpus"] = {
        "v1_fixed_account_reports": len(v1_report_ids),
        "units_before_dedup": units_before_dedup,
        "units_after_dedup": len(units),
        "dup_dropped": dup_dropped,
        "short_units": sum(1 for u in units if u["horizon"] == "short_term"),
        "medium_units": sum(1 for u in units if u["horizon"] == "medium_term"),
        "whole_horizon_missing_reports": {
            hz: {
                "count": whole_missing[hz],
                "sample_report_ids": sorted(whole_missing_report_ids[hz])[:10],
            } for hz in HORIZONS
        },
        "whole_horizon_missing_note": (
            "整档位缺失：报告级别整个 short_term/medium_term 字段不存在，"
            "独立于档内 claims/mv/ds 缺失记账；不并入单档缺失分母。"
        ),
        "excluded_nonfixed_v1_reports": 0,  # filled below
        "side_conflicts": dict(conflicts),
    }
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

    # ── Stage ②③ shared claim-level dataset ─────────────────────────────
    claim_rows: List[Dict[str, Any]] = []
    for u in units:
        mv = u["mv"]
        ces = u["ces"]
        adopted = set(norm_id_list(mv.get("adopted_claim_ids")))
        partial = set(norm_id_list(mv.get("partially_adopted_claims")))
        rejected = set(norm_id_list(mv.get("rejected_claim_ids")))
        excluded_ev = set(norm_id_list(mv.get("excluded_evidence")))
        dcg_excluded = u["dcg_excluded"]
        ledger_overlap = (adopted & partial) | (adopted & rejected) | (partial & rejected)
        listed = adopted | partial | rejected
        known_ids = set()
        for c in u["claims"]:
            if isinstance(c, Mapping) and c.get("claim_id"):
                known_ids.add(str(c["claim_id"]).strip())
        known_ids |= set(ces.keys())
        dangling = {cid for cid in listed if cid not in known_ids}

        seen_here = set()
        for c in u["claims"]:
            if not isinstance(c, Mapping):
                continue
            cid = str(c.get("claim_id") or "").strip()
            if not cid:
                continue
            seen_here.add(cid)
            side = claim_side(c.get("stance"), c.get("speaker_key"), c.get("speaker"), conflicts)
            s = ces.get(cid)
            claim_rows.append({
                "report_id": u["report_id"], "horizon": u["horizon"], "baseline": u["baseline"],
                "symbol": u["symbol"], "cid": cid, "side": side,
                "in_ces": s is not None, "mv_present": u["mv_present"],
                "ledger_bucket": ("adopted" if cid in adopted else
                                  "partial" if cid in partial else
                                  "rejected" if cid in rejected else None),
                "in_ledger": cid in listed,
                "dangling": cid in dangling, "ledger_overlap": cid in ledger_overlap,
                "dcg_excluded": cid in dcg_excluded, "excluded_evidence": cid in excluded_ev,
                "summary": s,
            })
        # claims in ces/ledger but absent from claims list (orphan rows)
        for cid in sorted(known_ids - seen_here):
            s = ces.get(cid)
            ss = (s or {})
            side = claim_side(ss.get("stance"), ss.get("speaker_key"), ss.get("speaker"), conflicts)
            claim_rows.append({
                "report_id": u["report_id"], "horizon": u["horizon"], "baseline": u["baseline"],
                "symbol": u["symbol"], "cid": cid, "side": side,
                "in_ces": s is not None, "mv_present": u["mv_present"],
                "ledger_bucket": ("adopted" if cid in adopted else
                                  "partial" if cid in partial else
                                  "rejected" if cid in rejected else None),
                "in_ledger": cid in listed, "dangling": True,
                "orphan_not_in_claims": True,
                "dcg_excluded": cid in dcg_excluded, "excluded_evidence": cid in excluded_ev,
                "summary": s,
            })

    # ── Stage ② 核实（三层分列 + 条件率分母订正 + semantic_counts 细项） ──────
    st2 = {}
    crosstab = {}
    reason_map_raw: Dict[str, Counter] = {hz: Counter() for hz in HORIZONS}
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz]
        verified_layer = Counter()  # (side, layer, outcome)
        elig_c = Counter()
        reasons = Counter()
        sem_counts_agg = Counter()       # (side, metric) -> sum
        sem_present_c = Counter()        # side -> claims with semantic_decision set
        sem_missing_c = Counter()        # side -> claims with no semantic_decision
        ct = Counter()
        for r in rows_hz:
            side = r["side"] or "unknown"
            s = r["summary"]
            if not r["in_ces"] or s is None:
                verified_layer[(side, "record", "no_verification_record")] += 1
                continue
            verified_layer[(side, "factual", s.get("decision") or "unknown")] += 1
            sem = s.get("semantic_decision")
            if sem:
                sem_present_c[side] += 1
                verified_layer[(side, "semantic", sem)] += 1
            else:
                sem_missing_c[side] += 1
                verified_layer[(side, "semantic", "unknown_absent")] += 1
            if s.get("semantic_hard_guards"):
                verified_layer[(side, "guards", "semantic_hard_guards_present")] += 1
            # semantic_counts 细项汇总（多原因可重叠，不相加成总数）
            sc = s.get("semantic_counts")
            if isinstance(sc, Mapping):
                for k, v in sc.items():
                    if isinstance(v, (int, float)):
                        sem_counts_agg[(side, str(k))] += int(v)
            el = ledger_eligibility(s)
            elig_c[(side, el["max_bucket"])] += 1
            if el["adopt_failure"]:
                reasons[(side, "adopt:" + el["adopt_failure"])] += 1
            if el["partial_failure"]:
                reasons[(side, "partial:" + el["partial_failure"])] += 1
            raw_reason = s.get("reason")
            reasons[(side, "reason:" + reason_cluster(raw_reason))] += 1
            if raw_reason:
                reason_map_raw[hz][(side, str(raw_reason))] += 1
            if r["in_ledger"]:
                ct[(side, s.get("decision") or "unknown", r["ledger_bucket"] or "unlisted")] += 1
            else:
                ct[(side, s.get("decision") or "unknown", "no_ledger")] += 1
        st2[hz] = {
            "claims_in_units": len(rows_hz),
            "verification_layer_counts": {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(verified_layer.items())},
            "semantic_coverage": {
                s: {
                    "implemented": sem_present_c[s],
                    "absent": sem_missing_c[s],
                    "implemented_share_pct": pct(sem_present_c[s], sem_present_c[s] + sem_missing_c[s]),
                } for s in ("bull", "bear", "unknown")
            },
            "semantic_counts_aggregate": {
                f"{a}|{b}": n for (a, b), n in sorted(sem_counts_agg.items())
            },
            "semantic_counts_note": "多原因可重叠：同一 claim 的 semantic_counts 各键可同时 >0，合计≠claim 数。",
            "ledger_eligibility_recomputed": {f"{a}|{b}": n for (a, b), n in sorted(elig_c.items())},
            "ledger_eligibility_note": (
                "副本未持久化 ledger_eligibility 字段；此处为与 evidence_verifier 同口径的"
                "事后重算，不能冒充当时持久化的资格。"
            ),
            "reject_reasons_clustered": {f"{a}|{b}": n for (a, b), n in reasons.most_common(60)},
        }
        crosstab[hz] = {f"{a}|{b}|{c}": n for (a, b, c), n in sorted(ct.items())}
    out["stages"]["2_verification"] = st2
    out["stages"]["3_crosstab_factual_decision_x_ledger"] = crosstab
    out["reason_code_to_cluster_map"] = {
        hz: {
            f"{side}|{raw}": {"cluster": reason_cluster(raw), "count": n}
            for (side, raw), n in sorted(reason_map_raw[hz].items())
        } for hz in HORIZONS
    }
    out["reason_code_to_cluster_note"] = "多原因计数可重叠；raw→cluster 多对一。"

    # ── Stage ③ 采纳（经理账本 only；双口径并列） ────────────────────────────
    st3 = {}
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz]
        raw_denom = Counter(); raw_numer = Counter()
        trim_denom = Counter(); trim_numer = Counter()
        legit_fold = Counter(); unadj = Counter(); noreach = Counter()
        # elig opportunity: universe / legit_excluded / remaining / chosen
        elig_full = defaultdict(lambda: [0, 0, 0, 0])     # side -> [univ, legit, remain, chosen]
        elig_part = defaultdict(lambda: [0, 0, 0, 0])
        for r in rows_hz:
            side = r["side"] or "unknown"
            s = r["summary"]
            if r.get("orphan_not_in_claims") and not r["in_ledger"] and not r["in_ces"]:
                continue  # pure dangling artifact, tallied below
            if r.get("orphan_not_in_claims") and r["in_ledger"]:
                pass  # dangling-ledger claim: still enters raw/trimmed tallies below
            if not r["mv_present"]:
                noreach[side] += 1
                continue
            in_dcg = r["dcg_excluded"]
            # raw ledger view (untrimmed)
            if r["in_ledger"]:
                raw_denom[side] += 1
                if r["ledger_bucket"] == "rejected":
                    raw_numer[side] += 1
            # trimmed ledger view: 合法折叠移出账本
            if r["in_ledger"] and not in_dcg:
                trim_denom[side] += 1
                if r["ledger_bucket"] == "rejected":
                    trim_numer[side] += 1
            # verified-but-unlisted bookkeeping
            if not r["in_ledger"]:
                if r["in_ces"]:
                    if in_dcg:
                        legit_fold[side] += 1   # legitimately folded by dcg audit
                    else:
                        unadj[side] += 1        # truly unadjudicated
                else:
                    noreach[side] += 1
            # eligibility opportunity denominator: universe includes legit-folded
            # and claims present in ces even when absent from the claims list.
            if s is not None:
                el = ledger_eligibility(s)
                b = el["max_bucket"]
                if side in ("bull", "bear") and b in ("adopted", "partial"):
                    tgt = elig_full if b == "adopted" else elig_part
                    tgt[side][0] += 1                    # universe
                    if in_dcg:
                        tgt[side][1] += 1                # legit folded
                    else:
                        tgt[side][2] += 1                # remaining opportunity
                        if r["ledger_bucket"] in ("adopted", "partial"):
                            tgt[side][3] += 1            # chosen
        st3[hz] = {
            "raw_list_denominator_adjudicated": dict(raw_denom),
            "raw_list_rejected": dict(raw_numer),
            "raw_list_reject_rate_pct": {s: pct(raw_numer[s], raw_denom[s]) for s in ("bull", "bear", "unknown")},
            "trimmed_ledger_denominator": dict(trim_denom),
            "trimmed_ledger_rejected": dict(trim_numer),
            "trimmed_ledger_reject_rate_pct": {s: pct(trim_numer[s], trim_denom[s]) for s in ("bull", "bear", "unknown")},
            "denominator_note": (
                "raw_list = 经理账本原始列表；trimmed_ledger = 去除 dcg 合法折叠后的有效账本。"
                "两口径并列，raw 不得称「有效最终账本选择损耗」。"
            ),
            "verified_but_unlisted_legit_fold": dict(legit_fold),
            "verified_but_unlisted_unadjudicated": dict(unadj),
            "not_reached_no_ledger_no_ces": dict(noreach),
            "eligibility_opportunity": {
                f"{side}|{bucket}": {
                    "universe": v[0], "legit_excluded": v[1],
                    "remaining_opportunity": v[2], "chosen": v[3],
                    "chosen_rate_pct": pct(v[3], v[2]),
                    "universe_rate_pct": pct(v[3], v[0]),
                }
                for bucket, dd in (("full", elig_full), ("partial", elig_part))
                for side, v in sorted(dd.items())
            },
            "dangling_ledger_ids": sum(1 for r in rows_hz if r["dangling"]),
            "ledger_overlap_ids": sum(1 for r in rows_hz if r["ledger_overlap"]),
            "dcg_excluded_refs": sum(1 for r in rows_hz if r["dcg_excluded"]),
            "dcg_source": "investment_debate_state.claim_cluster_metrics.double_count_guard_audit (contract decision_status.py:860-883)",
        }
    out["stages"]["3_adoption"] = st3

    # ── Stage ④ 方向形成（门前机会分母） ──────────────────────────────────────
    st4 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        cats = Counter()
        cats_trim = Counter()
        outcome = defaultdict(Counter)
        outcome_trim = defaultdict(Counter)
        for u in us:
            if not u["mv_present"]:
                cats["no_mv"] += 1
                cats_trim["no_mv"] += 1
                continue
            mv = u["mv"]
            ces = u["ces"]
            dcg = u["dcg_excluded"]
            adopted = set(norm_id_list(mv.get("adopted_claim_ids")))
            partial = set(norm_id_list(mv.get("partially_adopted_claims")))
            adopted_trim = adopted - dcg
            partial_trim = partial - dcg

            def sidekey(cid):
                return str((ces.get(cid) or {}).get("speaker_key") or "").lower()

            bull_ad = any(sidekey(c) == "bull" for c in adopted)
            bear_ad = any(sidekey(c) == "bear" for c in adopted)
            bull_pt = any(sidekey(c) == "bull" for c in partial)
            bear_pt = any(sidekey(c) == "bear" for c in partial)
            bull_ad_t = any(sidekey(c) == "bull" for c in adopted_trim)
            bear_ad_t = any(sidekey(c) == "bear" for c in adopted_trim)
            bull_pt_t = any(sidekey(c) == "bull" for c in partial_trim)
            bear_pt_t = any(sidekey(c) == "bear" for c in partial_trim)

            d = direction_side(mv.get("direction"))
            d_outcome = d if d else ("neutral" if str(mv.get("direction") or "").strip() in ("中性",) else "missing_or_unparseable")

            def _cat(ba, ra, bp, rp):
                if ba and not ra:
                    return "bull_only"
                if ra and not ba:
                    return "bear_only"
                if ba and ra:
                    return "both"
                if not ba and not ra and (bp or rp):
                    return "partial_only"
                return "no_support"

            cat = _cat(bull_ad, bear_ad, bull_pt, bear_pt)
            cat_t = _cat(bull_ad_t, bear_ad_t, bull_pt_t, bear_pt_t)
            cats[cat] += 1
            cats_trim[cat_t] += 1
            outcome[cat][d_outcome] += 1
            outcome_trim[cat_t][d_outcome] += 1
        st4[hz] = {
            "opportunity_categories_raw": dict(cats),
            "opportunity_categories_trimmed": dict(cats_trim),
            "formed_direction_by_category_raw": {c: dict(o) for c, o in outcome.items()},
            "formed_direction_by_category_trimmed": {c: dict(o) for c, o in outcome_trim.items()},
            "trim_note": (
                "trimmed = adopted/partial 列表先剔除 dcg 合法折叠 claim 再分类；"
                "adopted 与合法排除交集为 0，故 ④ 主机会分母（adopted 支撑）不变，"
                "只影响 partial_only / no_support。"
            ),
        }
    out["stages"]["4_direction"] = st4

    # ── Stage ⑤ 自洽硬门 ────────────────────────────────────────────────────
    st5 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        gate_state = Counter()
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
            "failed_check_clusters": {f"{a}|{b}": n for (a, b), n in fail_codes.most_common(60)},
        }
    out["stages"]["5_consistency_gate"] = st5

    # ── Stage ⑥ 价格门 ──────────────────────────────────────────────────────
    st6 = {}
    for hz in HORIZONS:
        us = hz_units(hz)
        gate_state = Counter()
        viol = Counter()
        post5_blocked = Counter()
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

    # ② semantic reject: conditional rate over IMPLEMENTED semantic checks only.
    #    Unknown (semantic_decision absent) claims are excluded from the
    #    denominator and reported alongside as coverage.
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz and r["in_ces"]]
        us = claim_units_for_stage(
            rows_hz,
            lambda r: (r["summary"].get("semantic_decision") == "reject"),
            lambda r: bool(r["summary"].get("semantic_decision")),
        )
        asym[f"2_semantic_reject_conditional|{hz}"] = cluster_boot_diff(us)
        # factual reject
        us2 = claim_units_for_stage(
            rows_hz,
            lambda r: (r["summary"].get("decision") == "reject"),
            lambda r: True,
        )
        asym[f"2_factual_reject|{hz}"] = cluster_boot_diff(us2)

    # ③ ledger reject rate — trimmed effective-ledger 口径 (dcg-excluded removed)
    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows
                   if r["horizon"] == hz and r["in_ledger"] and not r["dcg_excluded"] and not r.get("orphan_not_in_claims")]
        us = claim_units_for_stage(
            rows_hz,
            lambda r: r["ledger_bucket"] == "rejected",
            lambda r: True,
        )
        asym[f"3_ledger_reject_trimmed|{hz}"] = cluster_boot_diff(us)
        # raw list 口径并列
        rows_raw = [r for r in claim_rows
                    if r["horizon"] == hz and r["in_ledger"] and not r.get("orphan_not_in_claims")]
        us_raw = claim_units_for_stage(
            rows_raw,
            lambda r: r["ledger_bucket"] == "rejected",
            lambda r: True,
        )
        asym[f"3_ledger_reject_raw|{hz}"] = cluster_boot_diff(us_raw)

    # ④ direction formation. Estimand A (primary): opportunity denominator
    #    includes the `both` category entering BOTH sides (76/49 overlap units),
    #    diff = (59/273 − 109/186)×100 ≈ −36.99pp. Estimand B (side-only):
    #    bull_only vs bear_only only (53/197 vs 80/110 ≈ −45.82pp). Reported
    #    separately so the objects are not conflated.
    for hz in HORIZONS:
        us_incl = []
        us_only = []
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            mv = u["mv"]; ces = u["ces"]
            adopted = set(norm_id_list(mv.get("adopted_claim_ids")))

            def sidekey(cid):
                return str((ces.get(cid) or {}).get("speaker_key") or "").lower()

            bull_ad = any(sidekey(c) == "bull" for c in adopted)
            bear_ad = any(sidekey(c) == "bear" for c in adopted)
            d = direction_side(mv.get("direction"))
            if bull_ad and not bear_ad:
                us_incl.append((u["baseline"], "bull", 1 if d == "bull" else 0, 1))
                us_only.append((u["baseline"], "bull", 1 if d == "bull" else 0, 1))
            elif bear_ad and not bull_ad:
                us_incl.append((u["baseline"], "bear", 1 if d == "bear" else 0, 1))
                us_only.append((u["baseline"], "bear", 1 if d == "bear" else 0, 1))
            elif bull_ad and bear_ad:
                # dual support enters both sides' opportunity sets (overlap)
                us_incl.append((u["baseline"], "bull", 1 if d == "bull" else 0, 1))
                us_incl.append((u["baseline"], "bear", 1 if d == "bear" else 0, 1))
        asym[f"4_same_side_formation_incl_both|{hz}"] = cluster_boot_diff(us_incl)
        asym[f"4_same_side_formation_side_only|{hz}"] = cluster_boot_diff(us_only)

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
    out["asymmetry_interpretation_note"] = (
        "4_same_side_formation_incl_both: −36.99pp 的估计对象 = (59/273 − 109/186)×100，"
        "分母含 both 重叠档；4_same_side_formation_side_only: −45.82pp = (53/197 − 80/110)×100 仅侧桶。"
        "两口径并列，不得把仅侧桶数值紧接 incl_both 报告。中线 CI 含 0 → 「未见显著差异」，"
        "不写「差异不存在」。可写『提出量接近、④形成率两侧差异明显』；不得写『偏空不来自"
        "提出侧』『最大可观测来源』『④处突变』（各环分母非同一串行总体，不能按绝对 pp 识别因果）。"
    )

    # ── 每个不对称点 5 个独立报告例（不足 5 如实标 distinct_reports） ──────────
    ex: Dict[str, List[Dict[str, Any]]] = {}

    def pick(rows_hz, pred, key, limit=5):
        got = []
        seen_reports = set()
        for r in rows_hz:
            if r["side"] not in ("bull", "bear"):
                continue
            if r["report_id"] in seen_reports:
                continue
            if pred(r):
                s = r.get("summary") or {}
                got.append({
                    "report_id": r["report_id"], "horizon": r["horizon"], "claim_id": r["cid"],
                    "side": r["side"], "semantic_decision": s.get("semantic_decision"),
                    "factual_decision": s.get("decision"), "reason": s.get("reason"),
                    "claim_text": (s.get("claim") or "")[:80],
                })
                seen_reports.add(r["report_id"])
                if len(got) >= limit:
                    break
        ex[key] = {"examples": got, "distinct_reports": len(seen_reports),
                   "note": None if len(seen_reports) >= limit else f"不足{limit}个独立报告"}

    for hz in HORIZONS:
        rows_hz = [r for r in claim_rows if r["horizon"] == hz and r["in_ces"]]
        pick([r for r in rows_hz if r["side"] == "bull" and (r["summary"].get("semantic_decision") == "reject")],
             lambda r: True, f"2_semantic_reject_bull|{hz}")
        pick([r for r in rows_hz if r["side"] == "bull" and r["summary"].get("semantic_decision") == "reject"
              and r["ledger_bucket"] == "rejected"],
             lambda r: True, f"3_semantic_reject_ledgered_bull|{hz}")
        pick([r for r in rows_hz if r["side"] == "bull" and r["summary"].get("decision") == "adopt"
              and r["summary"].get("semantic_decision") == "reject"],
             lambda r: True, f"2_factual_adopt_semantic_reject_bull|{hz}")

    # ④ unit-level examples: bull_only units that formed bear direction, and
    #    bear_only units that formed bull (asymmetry probes both directions)
    for hz in HORIZONS:
        got = {"bull_only_formed_bear": [], "bear_only_formed_bull": []}
        seen = {"bull_only_formed_bear": set(), "bear_only_formed_bull": set()}
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            mv = u["mv"]; ces = u["ces"]
            adopted = set(norm_id_list(mv.get("adopted_claim_ids")))

            def sidekey(cid):
                return str((ces.get(cid) or {}).get("speaker_key") or "").lower()

            bull_ad = any(sidekey(c) == "bull" for c in adopted)
            bear_ad = any(sidekey(c) == "bear" for c in adopted)
            d = direction_side(mv.get("direction"))
            if bull_ad and not bear_ad and d == "bear":
                k = "bull_only_formed_bear"
            elif bear_ad and not bull_ad and d == "bull":
                k = "bear_only_formed_bull"
            else:
                continue
            if u["report_id"] in seen[k]:
                continue
            got[k].append({
                "report_id": u["report_id"], "horizon": hz,
                "direction": mv.get("direction"), "side_formed": d,
                "adopted_bull": bull_ad, "adopted_bear": bear_ad,
            })
            seen[k].add(u["report_id"])
        ex[f"4_same_side_formation|{hz}"] = {
            "examples": (got["bull_only_formed_bear"][:5] + got["bear_only_formed_bull"][:5]),
            "distinct_reports": len(seen["bull_only_formed_bear"] | seen["bear_only_formed_bull"]),
            "note": ("bull_only→bear 与 bear_only→bull 合并列示；"
                     + ("" if len(seen["bull_only_formed_bear"] | seen["bear_only_formed_bull"]) >= 5
                        else "不足5个独立报告")),
        }

    for hz in HORIZONS:
        hits5 = []
        hits6 = []
        seen5 = set()
        seen6 = set()
        for u in hz_units(hz):
            if not u["mv_present"]:
                continue
            side = direction_side(u["mv"].get("direction"))
            if side == "bull":
                rc = u["ds"].get("reason_codes") or []
                if u["mv"].get("consistency_check_passed") is False or any("manager_consistency_hard_gate" in str(x) for x in rc):
                    if u["report_id"] not in seen5:
                        hits5.append({"report_id": u["report_id"], "horizon": hz, "direction": u["mv"].get("direction"),
                                      "failed_checks": [str(x)[:80] for x in (u["ds"].get("failed_checks") or u["mv"].get("failed_checks") or [])][:3]})
                        seen5.add(u["report_id"])
                if u["gate_present"] and u["gate"].get("status") == "blocked":
                    if u["report_id"] not in seen6:
                        hits6.append({"report_id": u["report_id"], "horizon": hz, "direction": u["mv"].get("direction"),
                                      "violation_kinds": [str(v.get("kind")) for v in (u["gate"].get("violations") or []) if isinstance(v, Mapping)][:3]})
                        seen6.add(u["report_id"])
        ex[f"5_hardgate_bull|{hz}"] = {"examples": hits5[:5], "distinct_reports": len(seen5),
                                      "note": None if len(seen5) >= 5 else "不足5个独立报告"}
        ex[f"6_pricegate_bull|{hz}"] = {"examples": hits6[:5], "distinct_reports": len(seen6),
                                       "note": None if len(seen6) >= 5 else "不足5个独立报告"}
    out["asymmetry_examples"] = ex

    # ── 「未标版本」背景桶（版本内去重 + 分层，逐行流式） ──────────────────
    v1_min_ca = cur.execute(
        "SELECT MIN(created_at) FROM reports WHERE json_extract(result_data,'$.decision_model_version')=?",
        (DECISION_MODEL_V1,)).fetchone()[0]
    # dedup NULL bucket per (symbol, bd, hz) — same rule as v1；只保留每个报告
    # 去重胜出后的最小必要字段，result_data 逐行即用即弃。
    nbest: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    nraw_reports = 0
    nmeta: Dict[str, Dict[str, Any]] = {}  # rid -> {sym, bd, ca, st_dir}
    cur.execute(
        "SELECT id, symbol, COALESCE(json_extract(result_data,'$.analysis_baseline_date'), trade_date), "
        "created_at, result_data FROM reports "
        "WHERE result_data IS NOT NULL AND json_extract(result_data,'$.decision_model_version') IS NULL AND user_id=?",
        (FIXED_USER_ID,),
    )
    for rid, sym, bd, ca, rdj in cur:
        nraw_reports += 1
        try:
            rd = json.loads(rdj)
        except Exception:
            rd = None
        if not isinstance(rd, dict):
            continue
        st_dir = "none"
        st = rd.get("short_term")
        if isinstance(st, dict):
            mv = st.get("manager_verdict") or {}
            st_dir = direction_side(mv.get("direction")) or "none"
        kept_any_hz = False
        for hz in HORIZONS:
            sl = rd.get(hz)
            if not isinstance(sl, dict) or not sl:
                continue
            key = (sym, bd, hz)
            cand = (ca or "", rid)
            if key not in nbest or cand > nbest[key]:
                nbest[key] = cand
                kept_any_hz = True
        if kept_any_hz:
            nmeta[rid] = {"sym": sym, "bd": bd, "ca": ca, "st_dir": st_dir}
        del rd

    nkept_reports = {rid for _, rid in nbest.values()}
    n_pre = n_post = 0
    bg_dir = Counter()
    run_month = Counter()
    base_month = Counter()
    for rid in nkept_reports:
        m = nmeta.get(rid)
        if m is None:
            continue
        ca, bd = m["ca"], m["bd"]
        if ca and v1_min_ca and ca >= v1_min_ca:
            n_post += 1
        else:
            n_pre += 1
        if ca:
            run_month[ca[:7]] += 1
        if bd:
            base_month[str(bd)[:7]] += 1
        bg_dir[m["st_dir"]] += 1
    bg = {
        "label": "未标版本（decision_model_version IS NULL）背景桶",
        "raw_reports_with_result_data_fixed_account": nraw_reports,
        "deduped_reports": len(nkept_reports),
        "deduped_units": len(nbest),
        "created_before_first_v1_run": n_pre,
        "created_after_first_v1_run": n_post,
        "anchor_note": (
            f"划分锚点 = 首条 v1 记录 created_at ({v1_min_ca})，仅表示本副本中 v1 "
            "首次出现的时间，不是可核验的生产上线时刻（可核验上线锚点：无）。"
        ),
        "run_month_counts": dict(sorted(run_month.items())),
        "baseline_month_counts": dict(sorted(base_month.items())),
        "note": "未标版本桶只作背景；字段结构/口径与 v1 不同，不并列拒收率，也不得与 225 份原始报告同权混用。",
        "short_term_direction_side": dict(bg_dir),
    }
    out["background_unversioned"] = bg

    # ── 运行生成月 × 基准月份分层（v1 主样本） ──────────────────────────────
    strat: Dict[str, Counter] = {"run_month": Counter(), "baseline_month": Counter(),
                                 "run_x_baseline": Counter()}
    for u in units:
        ca = u["created_at"] or ""
        bd = str(u["baseline"] or "")
        rm = ca[:7] if ca else "unknown"
        bm = bd[:7] if bd else "unknown"
        strat["run_month"][(u["horizon"], rm)] += 1
        strat["baseline_month"][(u["horizon"], bm)] += 1
        strat["run_x_baseline"][(u["horizon"], rm, bm)] += 1
    out["stratification"] = {
        "run_month": {f"{hz}|{m}": n for (hz, m), n in sorted(strat["run_month"].items())},
        "baseline_month": {f"{hz}|{m}": n for (hz, m), n in sorted(strat["baseline_month"].items())},
        "run_x_baseline": {f"{hz}|{rm}|{bm}": n for (hz, rm, bm), n in sorted(strat["run_x_baseline"].items())},
        "note": "基准日覆盖 4–9 月，无 10 月基准日；运行生成月 9/10 月。",
    }

    # ── write outputs ───────────────────────────────────────────────────────
    # ru_maxrss: macOS 返回 bytes，Linux 返回 KB——归一化到 bytes。
    peak_rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        peak_rss_bytes *= 1024
    out["runtime"] = {
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_gb": round(peak_rss_bytes / (1024 ** 3), 3),
        "streaming_note": (
            "result_data 逐行流式读取+即用即弃；单进程峰值 RSS 限 4 GB，"
            "实测值以本键为准（macOS ru_maxrss 为字节）。"
        ),
    }
    if args.output_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)

    # human-readable summary
    lines = ["# DAV-1505/DAV-1531 六环节拒收率（多空分侧）", ""]
    lines.append("> 口径已按 DAV-1529 评审订正：③双口径并列（raw_list / trimmed_ledger），")
    lines.append("> ②条件率分母 = 已实施语义核实 claim（未实施并列报覆盖度），④主估计对象")
    lines.append("> 含 both 重叠档（−36.99pp）与仅侧桶（−45.82pp）分列，NULL 桶按版本内去重。")
    lines.append("")
    for hz in HORIZONS:
        lines.append(f"## {HORIZON_LABEL[hz]}")
        s1 = st1[hz]
        lines.append(f"- ① 论点提出：{s1['claims_total']} claims（bull {s1['claims_by_side'].get('bull',0)} / "
                     f"bear {s1['claims_by_side'].get('bear',0)} / unknown {s1['claims_by_side'].get('unknown',0)}），"
                     f"多头占比 {s1['bull_share_pct']}%；explicit-empty {s1['units_explicit_empty_claims']} 档、"
                     f"claims 缺失 {s1['units_missing_claims_field_or_ids']} 档（共 {s1['units']} 档）")
        s2 = st2[hz]
        lines.append(f"- ② 核实：{s2['claims_in_units']} claims")
        for k, v in s2["semantic_coverage"].items():
            lines.append(f"    - 语义覆盖 {k}: implemented {v['implemented']} / absent {v['absent']} ({v['implemented_share_pct']}%)")
        for k, v in s2["ledger_eligibility_recomputed"].items():
            lines.append(f"    - 资格(重算) {k}: {v}")
        s3 = st3[hz]
        for side in ("bull", "bear"):
            d = s3["raw_list_denominator_adjudicated"].get(side, 0)
            n = s3["raw_list_rejected"].get(side, 0)
            td = s3["trimmed_ledger_denominator"].get(side, 0)
            tn = s3["trimmed_ledger_rejected"].get(side, 0)
            lines.append(f"- ③ 采纳 {side}: raw {n}/{d} = {s3['raw_list_reject_rate_pct'].get(side)}% ; "
                         f"trimmed {tn}/{td} = {s3['trimmed_ledger_reject_rate_pct'].get(side)}%")
        for side in ("bull", "bear"):
            lf = s3["verified_but_unlisted_legit_fold"].get(side, 0)
            ua = s3["verified_but_unlisted_unadjudicated"].get(side, 0)
            lines.append(f"    - 有核实未列账本 {side}: legit_fold {lf} / unadjudicated {ua}")
        for k, v in s3["eligibility_opportunity"].items():
            lines.append(f"    - 资格机会 {k}: 全集 {v['universe']} / 合法排除 {v['legit_excluded']} / "
                         f"剩余机会 {v['remaining_opportunity']} / 选择量 {v['chosen']} "
                         f"(chosen/remaining {v['chosen_rate_pct']}%, chosen/universe {v['universe_rate_pct']}%)")
        s4 = st4[hz]
        lines.append(f"- ④ 方向形成机会分类 raw: {s4['opportunity_categories_raw']}")
        lines.append(f"  ④ trimmed: {s4['opportunity_categories_trimmed']}")
        for cat, o in s4["formed_direction_by_category_raw"].items():
            lines.append(f"    - {cat}: {o}")
        s5 = st5[hz]
        lines.append(f"- ⑤ 自洽硬门: {s5['gate_state']}")
        s6 = st6[hz]
        lines.append(f"- ⑥ 价格门: {s6['gate_state']}；⑤放行后⑥blocked: {s6['blocked_after_5_passed']}")
        lines.append("")
    lines.append("## 不对称点（bull−bear, pp; cluster-bootstrap by baseline date, B=2000, seed 20261005）")
    for k, v in out["asymmetry"].items():
        if v["diff_pp_bull_minus_bear"] is None:
            lines.append(f"- {k}: NA（{v.get('note')}）")
        else:
            lines.append(f"- {k}: {v['diff_pp_bull_minus_bear']} pp, CI95 [{v['ci95_lo']}, {v['ci95_hi']}], clusters={v['n_clusters']}")
    lines.append("")
    lines.append("## 措辞边界")
    lines.append("- 可写：提出量接近、④形成率两侧差异明显（短线 CI 不含 0）。")
    lines.append("- 不可写：偏空不来自提出侧 / 最大可观测来源 / ④处突变（分母非同一串行总体）。")
    lines.append("- 中线 ④ CI 含 0 → 未见显著差异，不写差异不存在。")
    lines.append("")
    lines.append("## 运行时")
    lines.append(f"- 单进程峰值 RSS：{out['runtime']['peak_rss_gb']} GB（红线 4 GB）")
    lines.append("- result_data 逐行流式，即用即弃；产物全量落盘（stats.json / report.md）。")
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
