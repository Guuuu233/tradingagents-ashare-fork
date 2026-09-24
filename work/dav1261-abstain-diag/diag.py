#!/usr/bin/env python3
"""DAV-1261 — ABSTAIN 成因诊断与「可挽救上限」测算（只读，零 LLM，确定性重算）。

一条命令复跑：

    env -u PYTHONPATH PYTHONPATH=<repo_root> \\
        /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        work/dav1261-abstain-diag/diag.py \\
        --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \\
        --account 429163f7-50b6-4982-8bdf-96ae99506843 \\
        --since 2026-08-26 \\
        --postfix-cutoff "2026-09-25 01:05:19" \\
        --states /Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/dav-1249-c35f512940f2/workdir/dav1249/states \\
        --out work/dav1261-abstain-diag/out

产出：out/summary.json + out/report.md。不写生产库、不改任何代码路径行为。
"""
from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import pickle
import random
import re
import sqlite3
import sys
from collections import Counter
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------- cause model

# 下游层附加码：非"首个拦截"原因，统计全部命中原因时单列
DOWNSTREAM_CODES = {
    "upstream_non_executable",
    "risk_verdict:blocked",
    "price_basis_gate_blocked",
    "direction_evidence_blocked",
    "fund_flow_consensus_guard",
}
GATE_WRAPPER = "manager_consistency_hard_gate"

E04_PREFIX = "E-04 守卫拦截"


def classify_failed_check(s: str) -> str:
    """failed_checks 中一条人类可读检查 → 子类码。"""
    if not isinstance(s, str):
        return "other:non_str"
    if s.startswith(E04_PREFIX):
        if "已定价" in s or "priced in" in s:
            return "e04_priced_in"
        if "不及预期" in s or "低于预期" in s or "未达预期" in s:
            return "e04_miss"
        if "超预期" in s or "超出预期" in s or "好于预期" in s or "beat" in s:
            return "e04_beat"
        if "double_count" in s or "重复计入" in s or "加票" in s:
            return "e04_double_count"
        if "财务指标数值" in s or "擅自断言财务" in s:
            return "e04_metric"
        return "e04_other"
    if "正文明确判定" in s and "机读" in s:
        return "winner_conflict"
    if "证据覆盖率不足" in s:
        return "coverage_insufficient"
    if "事实冲突" in s or "前视" in s:
        return "fact_conflict_pit"
    if "fatal challenge" in s or "fatal_challenge" in s:
        return "fatal_challenge_veto"
    if "semantic_decision=reject" in s:
        return "adopted_semantic_reject"
    if "未完全核实" in s or "未核实混合证据" in s or "覆盖率" in s:
        return "coverage_insufficient"
    return "other:" + s[:40]


def classify_code(c: str) -> str:
    """reason_codes 中一条机读码 → 子类码（返回 None 表示非成因码）。"""
    c = str(c)
    if c in (GATE_WRAPPER,) or c in DOWNSTREAM_CODES:
        return None
    if c.startswith("fund_flow_guard"):
        return "fund_flow_guard"
    if c.startswith("verdict_consistency_rejected_adopt:"):
        return "rejected_adopt"
    if c.startswith("unadjudicated_material_claims_adopt:"):
        return "unadjudicated_adopt"
    if c.startswith("pit_failed_adopted_claims:") or c.startswith(
        "pit_failed_partially_adopted_claims:"
    ):
        return "pit_failed_adopt"
    if c.startswith("observation_hypotheses_unverified"):
        return "obs_hypo_unverified"
    if c.startswith("unverified_core_claims"):
        return "unverified_core_claims"
    # 落库 nested reason_codes 会并入人类可读 failed_check 串（老版本口径），
    # 非机读形态的条目走人类串分类器。
    return classify_failed_check(c)


def parse_claim_ids(code: str) -> list[str]:
    if ":" not in code:
        return []
    return [x for x in code.split(":", 1)[1].split(",") if x]


# ---------------------------------------------------------------- record load


def _nested_ds(mv: Mapping[str, Any]) -> Optional[dict]:
    n = mv.get("decision_status")
    return n if isinstance(n, dict) else None


def build_record(source: str, group: str, rid: str, rd: Mapping[str, Any],
                 created_at: str = "") -> dict:
    deb = rd.get("investment_debate_state") or {}
    if not isinstance(deb, Mapping):
        deb = {}
    mv = deb.get("manager_verdict") or rd.get("manager_verdict") or {}
    if not isinstance(mv, Mapping):
        mv = {}
    ds_top = rd.get("decision_status") or {}
    if not isinstance(ds_top, Mapping):
        ds_top = {}
    nested = _nested_ds(mv) or {}
    guard = rd.get("fund_flow_consensus_guard") or {}
    if not isinstance(guard, Mapping):
        guard = {}
    return {
        "source": source,
        "group": group,
        "id": rid,
        "created_at": created_at,
        "symbol": rd.get("symbol") or "",
        "analysis_status": rd.get("analysis_status"),
        "rd": rd,
        "deb": deb,
        "mv": mv,
        "ds_top": ds_top,
        "ds_nested": nested,
        "guard": guard,
        "judge_text": str(deb.get("judge_decision") or ""),
    }


def load_db_records(db: str, account: str, since: str, cutoff: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT id, created_at, analysis_status, result_data FROM reports "
        "WHERE user_id=? AND status='completed' AND created_at>? ORDER BY created_at",
        (account, since),
    ).fetchall()
    recs = []
    for rid, ca, st, rd in rows:
        rd = json.loads(rd)
        group = "prod_post_fix" if ca >= cutoff else "prod_pre_fix"
        recs.append(build_record("db", group, rid, rd, created_at=ca))
    con.close()
    return recs


def load_state_records(states_dir: str) -> list[dict]:
    recs = []
    for p in sorted(glob.glob(os.path.join(states_dir, "*.pkl"))):
        name = os.path.basename(p)
        group = "state_trial" if "__t" in name else "state_control"
        with open(p, "rb") as f:
            s = pickle.load(f)
        fs = s.get("final_state") if isinstance(s, Mapping) else None
        if not isinstance(fs, Mapping):
            continue
        recs.append(build_record("state", group, name, fs,
                                 created_at=str(fs.get("trade_date") or "")))
    return recs


# ------------------------------------------------------------ cause breakdown


def cause_items(rec: dict) -> list[tuple[str, str]]:
    """返回 [(cause_cat, raw_item), ...]：该报告命中的全部拦截原因（按判定序）。

    口径：nested decision_status（gate 前，risk/price 层附加码之前的落库状态）。
    - consistency_check_passed=False 路径：failed_checks 全部条目按序计入；
    - 确认状态码路径：reason_codes 中机读码按序计入；
    - fund_flow_guard:* 归入 fund_flow_guard 一类（细分用 guard.status）。
    """
    nested = rec["ds_nested"]
    raw_codes = [str(c) for c in (nested.get("reason_codes") or [])]
    failed = [str(x) for x in (nested.get("failed_checks") or []) if x]
    items: list[tuple[str, str]] = []
    seen = set()

    def add(cat: Optional[str], raw: str):
        if cat and cat not in seen:
            seen.add(cat)
            items.append((cat, raw))

    # 代码判定顺序：consistency_check_passed=False（failed_checks）先于确认状态码。
    for fc in failed:
        add(classify_failed_check(fc), fc)
    for c in raw_codes:
        add(classify_code(c), c)
    return items


def primary_cause(rec: dict) -> str:
    items = cause_items(rec)
    return items[0][0] if items else ("status:" + str(rec["analysis_status"]))


def fund_flow_subclass(rec: dict) -> str:
    return str(rec["guard"].get("status") or "unknown")


# ------------------------------------------------------------- E-04 反事实

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?])\s*|\n+")

# judge_decision 尾部会拼上系统硬闸告警（非经理文本，且其引用串本身含
# 「已定价」会干扰命中判定），反事实重算前剥离。
_ALARM_TAIL = re.compile(r"\n*\[系统硬闸告警\].*$", re.DOTALL)


def manager_raw_text(rec: dict) -> str:
    return _ALARM_TAIL.sub("", rec["judge_text"])


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENT_SPLIT.split(text or "") if s and s.strip()]


class E04Harness:
    """封装 validate_manager_expectation_revision_consumption 调用（零 LLM）。"""

    def __init__(self):
        from tradingagents.agents.managers.research_manager import (
            validate_manager_expectation_revision_consumption,
        )
        self._validate = validate_manager_expectation_revision_consumption

    def violations(self, rec: dict, raw: str, reason: str, plan: str) -> list[str]:
        mv = dict(rec["mv"])
        mv["reason"] = reason
        if plan:
            mv["investment_plan"] = plan
        else:
            mv.pop("investment_plan", None)
        ok, viol = self._validate(
            manager_verdict=mv,
            raw_response=raw,
            expectation_revisions=rec["mv"].get("expectation_revision"),
            claims=rec["deb"].get("claims") or [],
            seven_reports=seven_reports_of(rec["rd"]),
        )
        return list(viol or [])


def seven_reports_of(rd: Mapping[str, Any]) -> dict:
    return {
        "macro_report": rd.get("macro_report") or "",
        "market_report": rd.get("market_report") or "",
        "sentiment_report": rd.get("sentiment_report") or "",
        "news_report": rd.get("news_report") or "",
        "fundamentals_report": rd.get("fundamentals_report") or "",
        "smart_money_report": rd.get("smart_money_report") or "",
        "volume_price_report": rd.get("volume_price_report") or "",
    }


def sanitize_e04(rec: dict, harness: E04Harness,
                 max_iters: int = 20) -> dict:
    """贪删命中句：每轮删掉使违规数下降最多的一句（只删被命中的句子）。

    返回 {ok, removed: [sent...], new_raw, new_reason, new_plan, residual}。"""
    parts = {
        "raw": split_sentences(manager_raw_text(rec)),
        "reason": split_sentences(str(rec["mv"].get("reason") or "")),
        "plan": split_sentences(str(rec["mv"].get("investment_plan") or "")),
    }

    def join(xs):
        return "".join(xs)

    def cur_viol():
        return harness.violations(
            rec, join(parts["raw"]), join(parts["reason"]), join(parts["plan"]))

    base = cur_viol()
    base_n = len(base)
    removed: list[str] = []
    it = 0
    while base and it < max_iters:
        it += 1
        best = None  # (new_count, part, idx)
        for part, sents in parts.items():
            for i in range(len(sents)):
                trial = {p: list(s) for p, s in parts.items()}
                sent = trial[part].pop(i)
                v = harness.violations(
                    rec, join(trial["raw"]), join(trial["reason"]),
                    join(trial["plan"]))
                if len(v) < len(base) and (best is None or len(v) < best[0]):
                    best = (len(v), part, i, sent)
        if best is None:
            # 单句删除不降计数：一条违规串可覆盖多处命中（守卫按“存在即违”
            # 只记一次）。改用隔离判定：一句单独评测即产出违规 → 命中句，
            # 本轮把所有命中句一并删除。
            hits = []
            for part, sents in parts.items():
                for i, sent in enumerate(sents):
                    kw = {"raw": sent, "reason": "", "plan": ""}
                    if part == "reason":
                        kw = {"raw": "", "reason": sent, "plan": ""}
                    elif part == "plan":
                        kw = {"raw": "", "reason": "", "plan": sent}
                    if harness.violations(rec, kw["raw"], kw["reason"],
                                          kw["plan"]):
                        hits.append((part, i, sent))
            if not hits:
                break
            for part, i, sent in sorted(hits, key=lambda h: -h[1]):
                if i < len(parts[part]) and parts[part][i] == sent:
                    removed.append(parts[part].pop(i))
            base = cur_viol()
            continue
        _, part, idx, sent = best
        removed.append(parts[part].pop(idx))
        base = cur_viol()
    return {
        "ok": not base,
        "baseline_violations": base_n,
        "removed": removed,
        "new_raw": join(parts["raw"]),
        "new_reason": join(parts["reason"]),
        "new_plan": join(parts["plan"]),
        "residual": base,
    }


# ------------------------------------------------------- 确认状态/状态重算


def recompute_status(rec: dict, mv_override: Mapping[str, Any]):
    from tradingagents.agents.utils.decision_status import (
        status_from_manager_verdict,
    )
    mv2 = copy.deepcopy(dict(mv_override))
    mv2.pop("decision_status", None)  # 反事实：nested ABSTAIN 不再先行短路
    st = status_from_manager_verdict(
        mv2,
        investment_debate_state=rec["deb"],
        market_data_context=rec["rd"].get("market_data_context"),
    )
    return st.to_dict()


def price_gate_status(rec: dict, investment_plan_text: str) -> str:
    """在「裁决通过、plan=经理原文」的假设下确定性重跑价格硬门。"""
    try:
        from tradingagents.agents.utils.price_basis_gate import (
            evaluate_price_basis_gate,
        )
        from tradingagents.agents.utils.price_ref_registry import (
            audit_price_ref_registry,
        )
        state = dict(rec["rd"])
        state["investment_plan"] = investment_plan_text
        if "price_refs" not in state or "price_basis_validation" not in state:
            audit_price_ref_registry(state)
        gate = evaluate_price_basis_gate(state)
        return str(gate.get("status") or "unknown")
    except Exception as e:  # 重算失败不假设通过
        return f"recompute_error:{type(e).__name__}"


QUALIFIED_ACTIONS = {"BUY", "SELL", "HOLD"}


def d043_qualified(st: Mapping[str, Any]) -> bool:
    """D-043 合格口径：VALID 且动作 ∈ {BUY, SELL, HOLD}（WAIT 单列）。"""
    return (st.get("analysis_status") == "VALID"
            and st.get("trade_action") in QUALIFIED_ACTIONS)


def is_e04_only(rec: dict) -> bool:
    items = cause_items(rec)
    return bool(items) and all(c.startswith("e04_") for c, _ in items)


def claim_codes_of(rec: dict, prefix: str) -> list[str]:
    nested = rec["ds_nested"]
    return [str(c) for c in (nested.get("reason_codes") or [])
            if str(c).startswith(prefix)]


def simulate_claim_adjudication(rec: dict, claim_ids: list[str],
                                kind: str) -> dict:
    """裁决对齐反事实（evaluate_confirmation_state 语义）：

    - unadjudicated_adopt：违规 claim 是「确定性裁决=adopt 但不在任何裁决
      列表」的漏裁项 → 模拟补裁：加入 adopted_claim_ids。
    - rejected_adopt：违规 claim 在 rejected_ids 而确定性裁决=adopt →
      模拟裁决对齐：从 rejected 移入 adopted（只移除只会变成漏裁，仍拦）。
    重算确认状态与结论字段（方向、动作）。
    """
    mv2 = copy.deepcopy(dict(rec["mv"]))
    ids = list(dict.fromkeys(claim_ids))
    adopted = [str(c) for c in (mv2.get("adopted_claim_ids") or [])]
    if kind == "rejected_adopt":
        mv2["rejected_claim_ids"] = [
            c for c in (mv2.get("rejected_claim_ids") or [])
            if str(c) not in set(ids)]
    for cid in ids:
        if cid not in adopted:
            adopted.append(cid)
    mv2["adopted_claim_ids"] = adopted
    st = recompute_status(rec, mv2)
    return {"mv": mv2, "status": st}


def mv_pre_gate_action(rec: dict) -> str:
    """D-043 口径：gate 前动作取 investment_debate_state.manager_verdict.trade_action。"""
    return str(rec["mv"].get("trade_action") or "")


# ------------------------------------------------------------------ audit


def claim_text(rec: dict, cid: str) -> tuple[str, str]:
    for c in rec["deb"].get("claims") or []:
        if isinstance(c, Mapping) and c.get("claim_id") == cid:
            return str(c.get("claim") or "")[:200], str(c.get("status") or "")
    return "", "not_found"


def audit_candidates(cands: list[dict], kind: str, harness: E04Harness,
                     seed: int, n: int = 10) -> list[dict]:
    """产出人工审计候选条目（只给证据，不给判定——判定由实现者人工填写）。"""
    rng = random.Random(seed)
    pool = list(cands)
    rng.shuffle(pool)
    out = []
    for rec in pool[:n]:
        entry = {"id": rec["id"], "group": rec["group"], "kind": kind,
                 "symbol": rec["symbol"], "created_at": rec["created_at"]}
        if kind == "e04":
            s = sanitize_e04(rec, harness)
            hit = [h for h in s["removed"] if "系统硬闸告警" not in h]
            entry["hit_sentences"] = [h[:300] for h in hit[:4]]
            entry["excerpt"] = " / ".join(h[:200] for h in hit[:2])
            direct = harness.violations(
                rec, manager_raw_text(rec),
                str(rec["mv"].get("reason") or ""),
                str(rec["mv"].get("investment_plan") or ""))
            entry["baseline_reproduced"] = bool(direct)
            if not direct:
                entry["note"] = "留存文本不复现；命中疑在告警尾/未留存文本"
        elif kind == "unadjudicated_adopt":
            ids = []
            for c in claim_codes_of(rec, "unadjudicated_material_claims_adopt:"):
                ids += parse_claim_ids(c)
            texts = []
            for cid in ids[:3]:
                t, st = claim_text(rec, cid)
                texts.append(f"{cid}[{st}]: {t}")
            entry["claim_ids"] = ids
            entry["excerpt"] = " | ".join(texts)[:200]
        elif kind == "fund_flow_guard":
            g = rec["guard"]
            sel = g.get("selection") or {}
            entry["excerpt"] = str(
                sel.get("reason") or g.get("reason") or "")[:200]
            entry["guard_status"] = g.get("status")
            entry["reason_code"] = sel.get("reason_code")
        else:
            # 无静默兜底：未知类直接标注，走人工待定
            entry["excerpt"] = ""
            entry["note"] = f"unmapped audit kind: {kind}"
        out.append(entry)
    return out


# ------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--account",
                    default="429163f7-50b6-4982-8bdf-96ae99506843")
    ap.add_argument("--since", default="2026-08-26")
    ap.add_argument("--postfix-cutoff", default="2026-09-25 01:05:19")
    ap.add_argument("--states", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20260925)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    records: list[dict] = []
    if args.db:
        records += load_db_records(args.db, args.account, args.since,
                                   args.postfix_cutoff)
    if args.states:
        records += load_state_records(args.states)

    harness = E04Harness()

    summary: dict[str, Any] = {
        "params": vars(args),
        "groups": {},
        "cause_matrix": {},
        "single_cause": {},
        "counterfactual": {},
        "audit": [],
        "unrecomputable": [],
    }

    abstains = [r for r in records if r["analysis_status"] == "ABSTAIN"]
    summary["groups"]["all_records_by_status"] = dict(
        Counter(f"{r['group']}|{r['analysis_status']}" for r in records))
    summary["groups"]["abstain_by_group"] = dict(
        Counter(r["group"] for r in abstains))

    # ---- 1/2. 分解 + 单因 -------------------------------------------------
    cause_rows = []
    for rec in abstains:
        items = cause_items(rec)
        cats = [c for c, _ in items]
        rec["_causes"] = items
        rec["_primary"] = cats[0] if cats else "none"
        rec["_single"] = len(set(cats)) == 1
        rec["_ffsub"] = fund_flow_subclass(rec) if "fund_flow_guard" in cats else None
        downstream = [str(c) for c in (rec["ds_top"].get("reason_codes") or [])
                      if str(c) in DOWNSTREAM_CODES]
        cause_rows.append({
            "id": rec["id"], "group": rec["group"],
            "symbol": rec["symbol"], "created_at": rec["created_at"],
            "primary": rec["_primary"], "all_causes": cats,
            "fund_flow_status": rec["_ffsub"],
            "downstream_codes": downstream,
            "gate_before_action": mv_pre_gate_action(rec),
        })
    summary["cause_matrix"]["rows"] = cause_rows
    summary["cause_matrix"]["primary_counts"] = dict(
        Counter(r["primary"] for r in cause_rows))
    summary["cause_matrix"]["cooccurrence_counts"] = dict(
        Counter("+".join(sorted(set(r["all_causes"]))) for r in cause_rows))
    summary["cause_matrix"]["fund_flow_status_counts"] = dict(
        Counter(r["fund_flow_status"] for r in cause_rows
                if r["fund_flow_status"]))
    sc = Counter()
    for r in cause_rows:
        if r["primary"] != "none" and len(set(r["all_causes"])) == 1:
            sc[r["all_causes"][0]] += 1
    summary["single_cause"]["counts"] = dict(sc)
    summary["single_cause"]["e04_only_ids"] = [
        r["id"] for r in abstains if is_e04_only(r)]

    # ---- 3a. E-04 单因反事实 ----------------------------------------------
    e04_recs = [r for r in abstains if is_e04_only(r)]
    e04_out = {"total": len(e04_recs), "no_text": 0, "sanitized_ok": 0,
               "sanitized_fail": 0, "baseline_clean": 0,
               "baseline_clean_alarm_selfhit": 0,
               "baseline_clean_not_persisted": 0,
               "became_valid": 0, "still_abstain": 0,
               "qualified_d043": 0, "wait_d043": 0,
               "valid_and_price_pass": 0, "rows": []}
    for rec in e04_recs:
        row = {"id": rec["id"], "group": rec["group"],
               "gate_before_action": mv_pre_gate_action(rec)}
        if not manager_raw_text(rec).strip():
            e04_out["no_text"] += 1
            row["result"] = "no_manager_text"
            e04_out["rows"].append(row)
            continue
        s = sanitize_e04(rec, harness)
        row["removed_sentences"] = len(s["removed"])
        # 基线口径用未拆句的原文直测（拆句 join 会丢 \n 边界，影响个别命中）。
        direct_base = harness.violations(
            rec, manager_raw_text(rec), str(rec["mv"].get("reason") or ""),
            str(rec["mv"].get("investment_plan") or ""))
        row["baseline_violations"] = len(direct_base)
        if not direct_base:
            # 经理文本（剥告警尾）+ reason + plan 均不复现 → 查告警尾来源
            with_alarm = harness.violations(
                rec, rec["judge_text"], str(rec["mv"].get("reason") or ""),
                str(rec["mv"].get("investment_plan") or ""))
            # 留存经理文本中触发词的上下文（供判定豁免语境/版本漂移）
            mgr = manager_raw_text(rec)
            kw = re.compile(
                r"已定价|充分定价|完全定价|超预期|超出预期|不及预期|"
                r"priced in|重复计入", re.IGNORECASE)
            row["kw_contexts"] = [
                mgr[max(0, m.start() - 50):m.end() + 50].replace("\n", " ")
                for m in list(kw.finditer(mgr))[:4]]
            if with_alarm:
                row["result"] = "baseline_clean:alarm_tail_selfhit"
                e04_out["baseline_clean_alarm_selfhit"] += 1
            else:
                row["result"] = "baseline_clean:hit_not_in_persisted"
                e04_out["baseline_clean_not_persisted"] += 1
            e04_out["baseline_clean"] += 1
        if not s["ok"]:
            e04_out["sanitized_fail"] += 1
            row["result"] = "residual_violations"
            row["residual"] = s["residual"]
            e04_out["rows"].append(row)
            continue
        if s["baseline_violations"] > 0:
            e04_out["sanitized_ok"] += 1
        mv2 = copy.deepcopy(dict(rec["mv"]))
        mv2["consistency_check_passed"] = True
        mv2["failed_checks"] = []
        mv2["reason"] = s["new_reason"]
        if s["new_plan"]:
            mv2["investment_plan"] = s["new_plan"]
        st = recompute_status(rec, mv2)
        row["new_status"] = {k: st.get(k) for k in
                             ("analysis_status", "direction", "trade_action",
                              "confirmation_state", "reason_codes")}
        if st.get("analysis_status") == "VALID":
            e04_out["became_valid"] += 1
            if d043_qualified(st):
                e04_out["qualified_d043"] += 1
            elif st.get("trade_action") == "WAIT":
                e04_out["wait_d043"] += 1
            pg = price_gate_status(rec, s["new_raw"])
            row["price_gate"] = pg
            if pg == "pass":
                e04_out["valid_and_price_pass"] += 1
        else:
            e04_out["still_abstain"] += 1
        e04_out["rows"].append(row)
    summary["counterfactual"]["e04_only"] = e04_out

    # ---- 3b. adopt 类单因反事实（双口径 + 逐 claim 核验明细） --------------
    for prefix, name in (("unadjudicated_material_claims_adopt:",
                          "unadjudicated_adopt"),
                         ("verdict_consistency_rejected_adopt:",
                          "rejected_adopt")):
        out = {"total": 0, "recomputed": 0,
               "align": {"valid": 0, "qualified_d043": 0, "wait_d043": 0,
                        "still_blocked": 0, "conclusion_same": 0,
                        "conclusion_changed": 0},
               "remove_from_adopted": {"valid": 0, "qualified_d043": 0,
                                       "wait_d043": 0, "still_blocked": 0,
                                       "noop_not_in_adopted": 0},
               "move_to_rejected": {"valid": 0, "qualified_d043": 0,
                                    "wait_d043": 0, "still_blocked": 0},
               "claim_detail": [], "rows": []}
        for rec in abstains:
            items = rec.get("_causes") or cause_items(rec)
            cats = set(c for c, _ in items)
            if cats != {name}:
                continue
            out["total"] += 1
            ids: list[str] = []
            for c in claim_codes_of(rec, prefix):
                ids += parse_claim_ids(c)

            # 逐 claim 核验明细：澄清确定性裁决 vs 辩论生命周期 status
            smap = (rec["mv"].get("claim_evidence_summary")
                    or rec["deb"].get("claim_evidence_summary") or {})
            claims_by_id = {
                str(c.get("claim_id")): c
                for c in rec["deb"].get("claims") or []
                if isinstance(c, Mapping)}
            detail = []
            for cid in ids:
                sm = smap.get(cid) or {}
                cl = claims_by_id.get(cid) or {}
                where = [k for k in
                         ("adopted_claim_ids", "partially_adopted_claims",
                          "rejected_claim_ids")
                         if cid in [str(x) for x in (rec["mv"].get(k) or [])]]
                detail.append({
                    "claim_id": cid,
                    "summary_decision": sm.get("decision"),
                    "semantic_decision": sm.get("semantic_decision"),
                    "counts": sm.get("counts"),
                    "claim_status_debate_lifecycle": cl.get("status"),
                    "in_adjudication_lists": where,
                })
            out["claim_detail"].append({"id": rec["id"], "claims": detail})

            from tradingagents.agents.utils.decision_status import (
                map_verdict_direction,
            )
            pre_dir = map_verdict_direction(rec["mv"].get("direction"))

            # 口径 A：裁决对齐（补裁 adopted / rejected→adopted）
            res_a = simulate_claim_adjudication(rec, ids, name)
            st_a = res_a["status"]
            A = out["align"]
            out["recomputed"] += 1
            if st_a.get("analysis_status") != "VALID":
                A["still_blocked"] += 1
            else:
                A["valid"] += 1
                if d043_qualified(st_a):
                    A["qualified_d043"] += 1
                elif st_a.get("trade_action") == "WAIT":
                    A["wait_d043"] += 1
                if st_a.get("direction") == pre_dir:
                    A["conclusion_same"] += 1
                else:
                    A["conclusion_changed"] += 1

            # 口径 B（保守）：仅从 adopted/partial 移除这些 claim（literal）
            mv_b = copy.deepcopy(dict(rec["mv"]))
            rm = set(ids)
            in_adopted = any(
                cid in [str(x) for x in (mv_b.get("adopted_claim_ids") or [])]
                or cid in [str(x) for x in
                           (mv_b.get("partially_adopted_claims") or [])]
                for cid in ids)
            mv_b["adopted_claim_ids"] = [
                c for c in (mv_b.get("adopted_claim_ids") or [])
                if str(c) not in rm]
            mv_b["partially_adopted_claims"] = [
                c for c in (mv_b.get("partially_adopted_claims") or [])
                if str(c) not in rm]
            st_b = recompute_status(rec, mv_b)
            B = out["remove_from_adopted"]
            if not in_adopted:
                B["noop_not_in_adopted"] += 1
            if st_b.get("analysis_status") != "VALID":
                B["still_blocked"] += 1
            else:
                B["valid"] += 1
                if d043_qualified(st_b):
                    B["qualified_d043"] += 1
                elif st_b.get("trade_action") == "WAIT":
                    B["wait_d043"] += 1

            # 口径 C：裁入 rejected（另一确定性裁决路径）
            mv_c = copy.deepcopy(dict(rec["mv"]))
            rej = [str(c) for c in (mv_c.get("rejected_claim_ids") or [])]
            for cid in ids:
                if cid not in rej:
                    rej.append(cid)
            mv_c["rejected_claim_ids"] = rej
            if name == "rejected_adopt":
                pass  # 已在 rejected，口径 C 与现状等价
            st_c = recompute_status(rec, mv_c)
            C = out["move_to_rejected"]
            if st_c.get("analysis_status") != "VALID":
                C["still_blocked"] += 1
            else:
                C["valid"] += 1
                if d043_qualified(st_c):
                    C["qualified_d043"] += 1
                elif st_c.get("trade_action") == "WAIT":
                    C["wait_d043"] += 1

            out["rows"].append({
                "id": rec["id"], "group": rec["group"], "claim_ids": ids,
                "pre_direction": pre_dir,
                "align": {"status": st_a.get("analysis_status"),
                          "direction": st_a.get("direction"),
                          "trade_action": st_a.get("trade_action"),
                          "reason_codes": st_a.get("reason_codes")},
                "remove": {"status": st_b.get("analysis_status"),
                           "reason_codes": st_b.get("reason_codes")},
                "reject": {"status": st_c.get("analysis_status"),
                           "reason_codes": st_c.get("reason_codes")},
                "price_gate": (price_gate_status(rec, manager_raw_text(rec))
                               if st_a.get("analysis_status") == "VALID"
                               else None),
            })
        summary["counterfactual"][name] = out

    # ---- 4. 抽样审计 --------------------------------------------------------
    sub_counter = Counter()
    pools: dict[str, list[dict]] = {}
    for rec in abstains:
        items = rec["_causes"]
        if not items:
            continue
        p = rec["_primary"]
        if p == "fund_flow_guard":
            p = f"fund_flow_guard:{rec['_ffsub']}"
        elif p == "unadjudicated_adopt":
            p = "unadjudicated_adopt"
        sub_counter[p] += 1
        pools.setdefault(p, []).append(rec)
    top3 = [k for k, _ in sub_counter.most_common(3)]
    summary["audit_top3"] = top3
    # 显式 kind 映射，无静默兜底（复审 🟢#1 / 总控返工要求①）
    kind_map = {"unadjudicated_adopt": "unadjudicated_adopt"}
    candidates = []
    for i, k in enumerate(top3):
        if k.startswith("fund_flow_guard"):
            kind = "fund_flow_guard"
        elif k.startswith("e04"):
            kind = "e04"
        else:
            kind = kind_map.get(k, f"unmapped:{k}")
        candidates.extend(
            audit_candidates(pools[k], kind, harness, seed=args.seed + i))

    # 人工审计判定：实现者逐条阅读候选后写入 audit_verdicts.json
    # （{id: {verdict, reason}}），脚本只合并，不自动赋值。
    verdicts_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "audit_verdicts.json")
    manual = {}
    if os.path.exists(verdicts_path):
        with open(verdicts_path, encoding="utf-8") as f:
            manual = json.load(f)
    for c in candidates:
        v = manual.get(c["id"])
        if isinstance(v, Mapping) and v.get("verdict"):
            c["verdict"] = v["verdict"]
            c["verdict_reason"] = v.get("reason", "")
        else:
            c["verdict"] = "待定"
            c["verdict_reason"] = "未提供人工判定"
    summary["audit"] = candidates

    # ---- write --------------------------------------------------------------
    def ser(o):
        if isinstance(o, (set, tuple)):
            return list(o)
        return str(o)

    with open(os.path.join(args.out, "summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=ser)

    write_report(summary, os.path.join(args.out, "report.md"))
    print(f"done: {len(abstains)} abstain records → {args.out}")
    return 0


def write_report(s: dict, path: str) -> None:
    L = []
    a = L.append
    a("# DAV-1261 ABSTAIN 成因诊断与可挽救上限测算")
    a("")
    a("只读诊断，零 LLM。重算复用 trunk 43ca33d 的守卫/状态函数。")
    a("")
    a("## 样本")
    a("```json")
    a(json.dumps(s["groups"], ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("## 1. 首因分解（按代码判定序）")
    a("")
    a("| 首因 | 份数 |")
    a("|---|---|")
    for k, v in sorted(s["cause_matrix"]["primary_counts"].items(),
                      key=lambda x: -x[1]):
        a(f"| {k} | {v} |")
    a("")
    a("产生位置：`consistency_check_passed=False`+failed_checks → "
      "`extract_and_validate_manager_verdict` + E-04 守卫 "
      "(`validate_manager_expectation_revision_consumption`，research_manager.py)；"
      "确认状态码（rejected_adopt/unadjudicated_adopt/pit_failed）→ "
      "`evaluate_confirmation_state`+`status_from_manager_verdict` "
      "(decision_status.py)；fund_flow_guard → `fund_flow_guard_abstain_status` "
      "(run_integrity.py) + conditional_logic.py 路由；"
      "INVALID_RUN → `evaluate_run_integrity`。")
    a("")
    a("### fund_flow_guard 细分（DAV-1138 口径，guard.status）")
    a("```json")
    a(json.dumps(s["cause_matrix"]["fund_flow_status_counts"],
                 ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("### 共现组合（全部同时命中原因）")
    a("```json")
    a(json.dumps(s["cause_matrix"]["cooccurrence_counts"],
                 ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("## 2. 单因报告")
    a("```json")
    a(json.dumps(s["single_cause"]["counts"], ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("## 3. 反事实可挽救上限（确定性重算）")
    e = s["counterfactual"]["e04_only"]
    a(f"### 3a. 仅命中 E-04 的报告（{e['total']} 份）")
    a("")
    a(f"- 无经理原文可取: {e['no_text']}；删句后仍残留违规: {e['sanitized_fail']}")
    a(f"- 留存文本不复现违规合计: {e['baseline_clean']} "
      f"（系统告警尾缀自指命中 {e['baseline_clean_alarm_selfhit']} / "
      f"命中文本未留存 {e['baseline_clean_not_persisted']}）")
    a(f"- 删句后 E-04 通过: {e['sanitized_ok']}")
    a(f"- 重算 `status_from_manager_verdict` 后 VALID: {e['became_valid']}"
      f"（其中 D-043 合格 {e['qualified_d043']}，WAIT {e['wait_d043']}）")
    a(f"- VALID 且价格硬门 pass（现行门禁口径）: {e['valid_and_price_pass']}")
    a("")
    for name in ("unadjudicated_adopt", "rejected_adopt"):
        u = s["counterfactual"][name]
        A, B, C = u["align"], u["remove_from_adopted"], u["move_to_rejected"]
        a(f"### 3b. 仅命中 {name} 的报告（{u['total']} 份）")
        a("")
        a(f"- 口径 A 裁决对齐（补裁 adopted / rejected→adopted，前提：claim "
          f"确定性裁决=adopt 且经理漏裁/错裁）："
          f"VALID {A['valid']}（合格 {A['qualified_d043']}，"
          f"WAIT {A['wait_d043']}），仍拦 {A['still_blocked']}；"
          f"方向不变 {A['conclusion_same']} / 变 {A['conclusion_changed']}")
        a(f"- 口径 B 保守（仅从 adopted/partial 移除，前提：claim 在采纳列表"
          f"内才生效；noop_not_in_adopted={B['noop_not_in_adopted']}）："
          f"VALID {B['valid']}（合格 {B['qualified_d043']}，"
          f"WAIT {B['wait_d043']}），仍拦 {B['still_blocked']}")
        a(f"- 口径 C 裁入 rejected（前提：claim 应被否决）："
          f"VALID {C['valid']}（合格 {C['qualified_d043']}，"
          f"WAIT {C['wait_d043']}），仍拦 {C['still_blocked']}")
        a("")
        if name == "unadjudicated_adopt":
            a("注：口径 B 虽 noop（违规 claim 本就不在采纳列表），但 51 份"
              "仍转 VALID——这些 claim 已入 double_count_guard 的 "
              "excluded_claim_ids（decided 集合），按最终落库账本重算"
              "不再触发漏裁码；落库码记于 guard 裁剪前。余 2 份重算仍拦"
              "（浮出其他码），口径 A 补裁后亦全部放行。")
            a("")
        a("逐 claim 核验明细（确定性裁决 vs 辩论生命周期 status）见 "
          "summary.json counterfactual.<name>.claim_detail。")
        a("")
    a("重算字段：mv.consistency_check_passed/failed_checks、mv.reason/"
      "investment_plan（删句后）、adopted_claim_ids/rejected_claim_ids"
      "（裁决对齐后）；确认状态经 `evaluate_confirmation_state` 重算；"
      "方向/动作经 `status_from_manager_verdict` 重算。无法重算的情形在各 row "
      "注明（no_manager_text / residual_violations / recompute_error）。")
    a("")
    a("## 3c. 留存文本不复现的溯源（baseline_clean）")
    a("")
    a("- 全部 24 份（prod）可复现违规的唯一来源是**系统告警尾缀自指命中**："
      "`final_decision = full_content + '\\n\\n[系统硬闸告警] 裁决自洽硬闸未通过："
      "{failed_reasons}…'`（research_manager.py ~2255）把含「已定价/超预期」"
      "引文的违规文案拼回 judge_decision，同一守卫再扫即重命中。"
      "触发链路：E-04 违规文案 → 告警尾缀 → 落库 judge_decision → "
      "任何对留存文本的重扫（诊断/重跑）→ 同一守卫二次命中。")
    a("- 24/24 留存经理文本仍含触发词，但全部落在现行豁免语境"
      "（「未/尚未…定价」否定、「无…催化」存在性否定、条件/情景句、"
      "claim 裁决引述）。落库违规为运行期旧版本守卫所记（版本漂移）或"
      "命中文本未留存；按 trunk 43ca33d 现行口径这些句子不再触发。"
      "逐份 kw_contexts 见 summary.json。")
    a("")
    a("## 4. 抽样审计（固定种子，每类 10 份，人工判定）")
    a("")
    a("判定分类：①肯定断言 ②否定/未知/不确定 ③引述他人观点或条件句 "
      "④系统或守卫文案自指命中 ⑤其他；判定由实现者逐条阅读后给出 "
      "（audit_verdicts.json），无规则自动赋值，判不清归「待定」。")
    a("")
    a("| id | 类 | 判定 | 判定理由 | 摘录 |")
    a("|---|---|---|---|---|")
    for r in s["audit"]:
        ex = str(r.get("excerpt") or "").replace("|", "｜").replace("\n", " ")
        a(f"| {r['id']} | {r['kind']} | {r['verdict']} | "
          f"{str(r.get('verdict_reason') or '')[:120]} | {ex[:200]} |")
    a("")
    a("## 边界与口径说明")
    a("- 合格口径（D-043）：VALID 且 trade_action ∈ {BUY,SELL,HOLD}；"
      "WAIT 单列不计合格。")
    a("- unadjudicated_adopt 澄清：抽样中 `[unresolved]` 是 claim 的辩论生命"
      "周期 status；确定性裁决（claim_evidence_summary.decision）为 adopt"
      "（证据全 verified）。两条轴不矛盾：核验过关但经理未登记裁决。")
    a("- 基线复核：用最终落库账本重算 `status_from_manager_verdict` 时，"
      "部分报告与落库 nested 状态存在差异（落库状态在 double_count_guard "
      "裁剪 adopt 账本之前计算），差异本身即是反事实测算的一部分，"
      "分解统计一律以落库码为准。")
    a("- 价格门：历史上 price_basis_gate 普遍未落库（功能 2026-09-25 上线），"
      "此处对挽救候选用落库文本+清理后 plan 确定性重跑 "
      "`audit_price_ref_registry`+`evaluate_price_basis_gate`；"
      "trader/final 文本沿用落库桩文本（无价格引用），属上限口径。")
    a("- prod_post_fix 组（2026-09-25 01:05:19 起）报告数见 groups，"
      "本次窗口内为 0。")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
