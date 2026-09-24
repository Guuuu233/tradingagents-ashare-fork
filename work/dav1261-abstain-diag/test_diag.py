"""DAV-1261 diag.py 合成夹具单测 — 不依赖生产库。

运行：
    env -u PYTHONPATH PYTHONPATH=. \\
        /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \\
        -m pytest work/dav1261-abstain-diag/test_diag.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diag  # noqa: E402


def _rec(nested_codes, failed_checks=None, mv_extra=None, deb_extra=None):
    mv = {
        "direction": "偏多",
        "trade_action": "NO_TRADE",
        "decision_status": {
            "analysis_status": "ABSTAIN",
            "trade_action": "NO_TRADE",
            "reason_codes": list(nested_codes),
            "failed_checks": list(failed_checks or []),
        },
    }
    mv.update(mv_extra or {})
    deb = {"manager_verdict": mv, "judge_decision": "正文。",
           "claims": [], "evidence_verification": []}
    deb.update(deb_extra or {})
    rd = {"analysis_status": "ABSTAIN", "investment_debate_state": deb,
          "manager_verdict": mv, "decision_status": {
              "analysis_status": "ABSTAIN",
              "reason_codes": list(nested_codes) + ["risk_verdict:blocked"]}}
    return diag.build_record("db", "prod_pre_fix", "t1", rd)


def test_classify_failed_check_e04_subtypes():
    assert diag.classify_failed_check(
        "E-04 守卫拦截：缺乏可回溯证据，经理不得将“已定价/priced in”当作已确证事实引用"
    ) == "e04_priced_in"
    assert diag.classify_failed_check(
        "E-04 守卫拦截：基本面无有效旧基线，经理不得在正文或裁决理由中断言业绩“超预期”"
    ) == "e04_beat"
    assert diag.classify_failed_check(
        "E-04 守卫拦截：基本面无有效旧基线，经理不得断言业绩不及预期（命中 x）"
    ) == "e04_miss"
    assert diag.classify_failed_check(
        "部分采纳列表中包含了证据覆盖率不足 (50.0% < 67%) 的 claim: INV-5"
    ) == "coverage_insufficient"
    assert diag.classify_failed_check(
        "正文明确判定空头胜，但机读块为多头胜(bull)，正文与机读裁决严重矛盾"
    ) == "winner_conflict"


def test_cause_items_merges_human_strings_in_reason_codes():
    # 落库 nested：failed_checks=None，人类串并入 reason_codes
    rec = _rec(["manager_consistency_hard_gate",
                "E-04 守卫拦截：缺乏可回溯证据，经理不得将“已定价/priced in”当作已确证事实引用"])
    items = diag.cause_items(rec)
    assert [c for c, _ in items] == ["e04_priced_in"]
    assert diag.primary_cause(rec) == "e04_priced_in"
    assert diag.is_e04_only(rec)


def test_cause_items_failed_checks_order_first():
    rec = _rec(["manager_consistency_hard_gate",
                "unadjudicated_material_claims_adopt:INV-4"],
               failed_checks=["E-04 守卫拦截：…“已定价”…"])
    cats = [c for c, _ in diag.cause_items(rec)]
    assert cats == ["e04_priced_in", "unadjudicated_adopt"]
    assert not diag.is_e04_only(rec)


def test_downstream_codes_not_causes():
    rec = _rec(["fund_flow_guard:data_conflict", "direction_evidence_blocked",
                "fund_flow_consensus_guard", "risk_verdict:blocked"])
    cats = [c for c, _ in diag.cause_items(rec)]
    assert cats == ["fund_flow_guard"]


def test_parse_claim_ids():
    assert diag.parse_claim_ids(
        "unadjudicated_material_claims_adopt:INV-2,INV-5") == ["INV-2", "INV-5"]


def test_simulate_adjudication_unadjudicated():
    # unadjudicated claim（确定性裁决=adopt，未在任何裁决列表）补裁后放行。
    claims = [{"claim_id": "INV-1", "claim": "x", "status": "unresolved"}]
    ver = [{"claim_id": "INV-1", "status": "verified", "is_fatal": False}]
    rec = _rec(
        ["manager_consistency_hard_gate",
         "unadjudicated_material_claims_adopt:INV-1"],
        mv_extra={"consistency_check_passed": True, "failed_checks": [],
                  "adopted_claim_ids": ["INV-9"], "rejected_claim_ids": [],
                  "partially_adopted_claims": []},
        deb_extra={"claims": claims, "evidence_verification": ver,
                   "focus_claim_ids": [], "unresolved_claim_ids": ["INV-1"],
                   "claim_evidence_summary": {
                       "INV-1": {"decision": "adopt", "counts": {"total": 1, "verified": 1}}}})
    # claim INV-9 不在任何已知集合，防误伤：同时提供其摘要
    rec["deb"]["claim_evidence_summary"]["INV-9"] = {
        "decision": "adopt", "counts": {"total": 1, "verified": 1}}
    res = diag.simulate_claim_adjudication(rec, ["INV-1"], "unadjudicated_adopt")
    assert "INV-1" in res["mv"]["adopted_claim_ids"]
    assert res["status"]["analysis_status"] == "VALID"


def test_simulate_adjudication_rejected_adopt():
    claims = [{"claim_id": "INV-2", "claim": "y", "status": "resolved"}]
    rec = _rec(
        ["manager_consistency_hard_gate",
         "verdict_consistency_rejected_adopt:INV-2"],
        mv_extra={"consistency_check_passed": True, "failed_checks": [],
                  "adopted_claim_ids": [], "rejected_claim_ids": ["INV-2"],
                  "partially_adopted_claims": []},
        deb_extra={"claims": claims, "evidence_verification": [],
                   "focus_claim_ids": ["INV-2"], "unresolved_claim_ids": [],
                   "claim_evidence_summary": {
                       "INV-2": {"decision": "adopt",
                                 "counts": {"total": 1, "verified": 1}}}})
    res = diag.simulate_claim_adjudication(rec, ["INV-2"], "rejected_adopt")
    assert "INV-2" not in (res["mv"].get("rejected_claim_ids") or [])
    assert "INV-2" in res["mv"]["adopted_claim_ids"]
    assert res["status"]["analysis_status"] == "VALID"


def test_alarm_tail_stripped():
    rec = _rec([], deb_extra={
        "judge_decision": "经理正文。\n\n[系统硬闸告警] 裁决自洽硬闸未通过：E-04，已阻断。"})
    assert diag.manager_raw_text(rec) == "经理正文。"
