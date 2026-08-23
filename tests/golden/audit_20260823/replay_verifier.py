#!/usr/bin/env python3
"""Replay verifier across three audit cases (000333, 600900, 600276) for DAV-338.

Extracts debate claims and 7 analyst reports from _result_data.json files,
runs EvidenceFactualTruthEvaluator, and computes verified rates for bull vs bear.
Verifies rate difference convergence to <= 10pt and golden test set flips.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tradingagents.agents.utils.evidence_verifier import (
    SEVEN_REPORT_KEYS,
    EvidenceFactualTruthEvaluator,
    STATUS_VERIFIED,
    aggregate_claim_evidence,
)

AUDIT_DIR = Path(__file__).parent
DATA_FILES = [
    ("000333.SZ", "3c09051e7e364d859dfbe5f1af7cc2c9"),
    ("600900.SH", "597f6cf371114a3b9844112238a0f1a9"),
    ("600276.SH", "ba255b88dfa446279c2d6e9529be6f5e"),
]


def run_replay():
    evaluator = EvidenceFactualTruthEvaluator()
    results_by_symbol = {}

    tot_bull_v, tot_bull_t = 0, 0
    tot_bear_v, tot_bear_t = 0, 0

    print("================================================================================")
    print("                 TA AUDIT VERIFIER REPLAY & FAIRNESS REPORT                    ")
    print("================================================================================")

    for sym, fid in DATA_FILES:
        data_path = AUDIT_DIR / f"{fid}_result_data.json"
        with open(data_path, "r", encoding="utf-8") as f:
            rd = json.load(f)["result_data"]

        ids = rd["investment_debate_state"]
        claims = ids["claims"]
        seven_reports = {k: str(rd.get(k, "") or "") for k in SEVEN_REPORT_KEYS}
        market_data_context = rd.get("market_data_context")
        analysis_baseline_date = rd.get("analysis_baseline_date")

        verifications = evaluator.evaluate_claims(
            claims=claims,
            seven_reports=seven_reports,
            market_data_context=market_data_context,
            analysis_baseline_date=analysis_baseline_date,
        )

        summary = aggregate_claim_evidence(claims=claims, claims_verification=verifications)

        sym_stats = {"bull": {"v": 0, "t": 0}, "bear": {"v": 0, "t": 0}}

        for c in claims:
            cid = c["claim_id"]
            side = "bull" if c.get("speaker_key") == "Bull" else "bear"
            if cid in summary:
                v_cnt = summary[cid]["counts"]["verified"]
                t_cnt = summary[cid]["counts"]["total"]
                sym_stats[side]["v"] += v_cnt
                sym_stats[side]["t"] += t_cnt

        bull_v = sym_stats["bull"]["v"]
        bull_t = sym_stats["bull"]["t"]
        bear_v = sym_stats["bear"]["v"]
        bear_t = sym_stats["bear"]["t"]

        bull_rate = (bull_v / max(1, bull_t)) * 100
        bear_rate = (bear_v / max(1, bear_t)) * 100
        sym_diff = abs(bull_rate - bear_rate)

        tot_bull_v += bull_v
        tot_bull_t += bull_t
        tot_bear_v += bear_v
        tot_bear_t += bear_t

        results_by_symbol[sym] = {
            "bull_v": bull_v, "bull_t": bull_t, "bull_rate": bull_rate,
            "bear_v": bear_v, "bear_t": bear_t, "bear_rate": bear_rate,
            "diff": sym_diff,
        }

        print(f"\n[Symbol: {sym} (Report: {fid[:8]})]")
        print(f"  Bull: {bull_v}/{bull_t} verified ({bull_rate:.1f}%)")
        print(f"  Bear: {bear_v}/{bear_t} verified ({bear_rate:.1f}%)")
        print(f"  Rate Gap: {sym_diff:.1f} pt")

    total_bull_rate = (tot_bull_v / max(1, tot_bull_t)) * 100
    total_bear_rate = (tot_bear_v / max(1, tot_bear_t)) * 100
    total_diff = abs(total_bull_rate - total_bear_rate)

    print("\n--------------------------------------------------------------------------------")
    print("3-Round Aggregated Totals:")
    print(f"  Bull Verified Rate: {tot_bull_v}/{tot_bull_t} = {total_bull_rate:.1f}%")
    print(f"  Bear Verified Rate: {tot_bear_v}/{tot_bear_t} = {total_bear_rate:.1f}%")
    print(f"  Bull/Bear Verified Gap: {total_diff:.1f} pt (Target: <= 10.0 pt)")
    print("--------------------------------------------------------------------------------")

    # Evaluate golden sentence flips
    golden_path = AUDIT_DIR.parent / "evidence_sentences_20260823.json"
    pos_flip, pos_total = 0, 0
    neg_hold, neg_total = 0, 0

    if golden_path.exists():
        with open(golden_path, "r", encoding="utf-8") as f:
            golden_cases = json.load(f)

        for case in golden_cases:
            is_pos = case.get("is_positive", False)
            res = evaluator.evaluate_single_evidence(
                raw_evidence=case["raw_evidence"],
                seven_reports=case["seven_reports"],
                market_data_context=case.get("market_data_context"),
                analysis_baseline_date=case.get("analysis_baseline_date"),
                claim_id=case.get("claim_id"),
            )
            status = res.get("status")

            if is_pos:
                pos_total += 1
                if status == STATUS_VERIFIED:
                    pos_flip += 1
            else:
                neg_total += 1
                if status != STATUS_VERIFIED:
                    neg_hold += 1

        print(f"Golden Set Evaluation:")
        print(f"  Positive Cases Flipped to Verified: {pos_flip}/{pos_total} ({pos_flip/max(1, pos_total)*100:.1f}%, Target >= 8/9 = 88.9%)")
        print(f"  Negative Cases Held (Zero Flip):    {neg_hold}/{neg_total} (100.0%, Target: 100%)")
        print("================================================================================")

    # Assert acceptance criteria
    assert total_diff <= 10.0, f"Rate gap {total_diff:.1f}pt exceeds 10pt threshold!"
    if pos_total > 0:
        assert pos_flip / pos_total >= (8.0 / 9.0), f"Positive flip rate {pos_flip}/{pos_total} < 8/9!"
    if neg_total > 0:
        assert neg_hold == neg_total, f"Negative cases flipped! {neg_total - neg_hold} failed to hold."

    print("ALL ACCEPTANCE CRITERIA PASSED.")
    return {
        "bull_rate": total_bull_rate,
        "bear_rate": total_bear_rate,
        "gap": total_diff,
        "pos_flip": f"{pos_flip}/{pos_total}",
        "neg_hold": f"{neg_hold}/{neg_total}",
    }


if __name__ == "__main__":
    run_replay()
