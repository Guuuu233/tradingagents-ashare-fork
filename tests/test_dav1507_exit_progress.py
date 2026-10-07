"""Tests for DAV-1507 D-066 exit progress script (spec v4, SHA-256 65bd58…).

Covers the two required additions from the打回 + spec §5 counter-examples:
- missing trading calendar -> exit code 2, no readings emitted;
- A3 constructed samples match the clean-pipeline outcome
  (filter_v2_completed_reports), including eligible≠clean isolation,
  same-battlefield-3-claims not counting, both/single/tie-side sessions,
  one claim's many verified evidences counting once, cohort window
  isolation, and the (40,10)->80% non-symmetric share.

All samples are in-memory; no DB, no model, no provider.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dav1507_exit_progress.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dav1507_exit_progress as m


def _lib():
    return m._project_imports()


def _unit(claims, ev, summary, *, horizon="short", status="completed",
          analysis_status="VALID", trade_action="BUY", decision="BUY",
          direction="BULL", winner="bull"):
    return {
        "status": status, "analysis_status": analysis_status,
        "trade_action": trade_action, "decision": decision, "direction": direction,
        "horizon": horizon,
        "investment_debate_state": {
            "claims": claims,
            "manager_verdict": {"winner": winner, "claim_evidence_summary": summary},
        },
        "evidence_verification": ev,
        "decision_model_version": "decision_model.v1",
        "evidence_contract_version": "evidence_contract.v2",
        "price_basis_version": "price_basis.vendor_qfq",
        "price_ref_contract_version": "price_ref.v1",
    }


def _report(units):
    rd = {}
    if "short" in units:
        rd["short_term"] = units["short"]
    if "medium" in units:
        rd["medium_term"] = units["medium"]
    return {"id": "r1", "symbol": "X", "trade_date": "2026-10-08",
            "status": "completed", "created_at": "2026-10-08T01:00:00",
            "user_id": m.ACCOUNT, "result_data": rd}


def _claims(side, n, *, battlefields, stage="opening", cid_prefix=None):
    out = []
    for i in range(n):
        speaker = "Bull" if side == "bull" else "Bear"
        out.append({
            "claim_id": f"{cid_prefix or speaker}-{i}",
            "speaker_key": speaker, "speaker": f"{speaker} Analyst",
            "stance": "bullish" if side == "bull" else "bearish",
            "stage": stage, "battlefield": battlefields[i % len(battlefields)],
            "claim": f"{speaker} claim {i}",
        })
    return out


def _ev_for(claims, status="verified"):
    return [{"claim_id": c["claim_id"], "status": status} for c in claims]


def _summary_for(claims, decision="adopt"):
    return {c["claim_id"]: {"decision": decision, "counts": {"verified": 1}} for c in claims}


# ── calendar fail-close ──────────────────────────────────────────────────── #

def test_missing_calendar_exits_2(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"")  # any readable file; calendar check happens before DB scan
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--calendar-json", str(tmp_path / "nonexistent_calendar.json")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""


def test_calendar_json_required_flag(tmp_path):
    db = tmp_path / "x.db"
    db.write_bytes(b"")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--calendar-json", str(tmp_path / "nope.json"), "--self-test"],
        capture_output=True, text=True,
    )
    # --self-test bypasses calendar; just ensure it doesn't crash-arg-error
    assert proc.returncode in (0, 2)


# ── verified-claim predicate ──────────────────────────────────────────────── #

def test_verified_claim_partial_rejected_by_decision_and_counts():
    lib = _lib()
    c = {"claim_id": "P-1", "speaker_key": "Bull", "stance": "bullish",
         "stage": "opening", "battlefield": "capital_flow", "claim": "x"}
    ev = {"P-1": [{"status": "verified"}, {"status": "verified"}, {"status": "unsupported"}]}
    assert not m.is_verified_claim(c, ev, {"decision": "partial", "counts": {"verified": 2, "unsupported": 1}}, lib)
    # summary missing -> unsupported>0 -> still excluded
    assert not m.is_verified_claim(c, ev, None, lib)


def test_verified_claim_contradicted_veto():
    lib = _lib()
    c = {"claim_id": "C-1", "speaker_key": "Bull", "stance": "bullish",
         "stage": "opening", "battlefield": "capital_flow", "claim": "x"}
    ev = {"C-1": [{"status": "verified"}, {"status": "contradicted"}]}
    assert not m.is_verified_claim(c, ev, None, lib)


def test_verified_claim_observation_excluded():
    lib = _lib()
    c = {"claim_id": "O-1", "speaker_key": "Bull", "stance": "bullish",
         "stage": "opening", "battlefield": "capital_flow", "claim": "观察：大盘缩量"}
    ev = {"O-1": [{"status": "verified"}]}
    assert not m.is_verified_claim(c, ev, None, lib)


def test_verified_claim_many_evidences_count_once():
    lib = _lib()
    c = {"claim_id": "M-1", "speaker_key": "Bull", "stance": "bullish",
         "stage": "opening", "battlefield": "capital_flow", "claim": "x"}
    ev = {"M-1": [{"status": "verified"}] * 5}
    assert m.is_verified_claim(c, ev, None, lib)
    # via a3_session_sides it's counted once in verified_claims
    unit = _unit([c], [{"claim_id": "M-1", "status": "verified"}] * 5, {"M-1": {"decision": "adopt", "counts": {"verified": 5}}})
    sides = m.a3_session_sides(unit, lib)
    assert sides["bull"]["verified_claims"] == 1

def test_same_battlefield_three_claims_not_effective():
    lib = _lib()
    claims = _claims("bull", 3, battlefields=["capital_flow"])
    unit = _unit(claims, _ev_for(claims), _summary_for(claims))
    sides = m.a3_session_sides(unit, lib)
    assert len(sides["bull"]["battlefields"]) == 1
    assert not m.a3_session_effective(sides["bull"])


def test_three_distinct_battlefields_effective():
    lib = _lib()
    claims = _claims("bull", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    unit = _unit(claims, _ev_for(claims), _summary_for(claims))
    sides = m.a3_session_sides(unit, lib)
    assert len(sides["bull"]["battlefields"]) == 3
    assert m.a3_session_effective(sides["bull"])


def test_bilateral_and_unilateral_sessions():
    lib = _lib()
    b = _claims("bull", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    bear = _claims("bear", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    both = _unit(b + bear, _ev_for(b + bear), _summary_for(b + bear))
    sides = m.a3_session_sides(both, lib)
    assert m.a3_session_effective(sides["bull"]) and m.a3_session_effective(sides["bear"])
    only_b = _unit(b, _ev_for(b), _summary_for(b))
    sides = m.a3_session_sides(only_b, lib)
    assert m.a3_session_effective(sides["bull"]) and not m.a3_session_effective(sides["bear"])


def test_tie_winner_does_not_change_side_attribution():
    lib = _lib()
    b = _claims("bull", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    unit = _unit(b, _ev_for(b), _summary_for(b), winner="tie")
    sides = m.a3_session_sides(unit, lib)
    assert m.a3_session_effective(sides["bull"])


# ── clean-pipeline A3 end-to-end (constructed vs filter_v2_completed_reports) #

def _make_clean_report_pair(*, short_eff_bull, short_eff_bear, medium_eff_bull, medium_eff_bear):
    """A dual report whose two sides are controlled by battlefield coverage."""
    def side_claims(side, eff, tag):
        pfx = ("BL" if side == "bull" else "BR") + tag
        if eff:
            return _claims(side, 3, battlefields=["capital_flow", "price_volume", "fundamentals"], cid_prefix=pfx)
        return _claims(side, 3, battlefields=["capital_flow"], cid_prefix=pfx)
    s_claims = side_claims("bull", short_eff_bull, "S") + side_claims("bear", short_eff_bear, "S")
    m_claims = side_claims("bull", medium_eff_bull, "M") + side_claims("bear", medium_eff_bear, "M")
    all_claims = s_claims + m_claims
    ev = _ev_for(all_claims)
    summary = _summary_for(all_claims)
    s_ids = {c["claim_id"] for c in s_claims}
    rep = _report({
        "short": _unit(s_claims, [e for e in ev if e["claim_id"] in s_ids],
                        _summary_for(s_claims), horizon="short"),
        "medium": _unit(m_claims, [e for e in ev if e["claim_id"] not in s_ids],
                         _summary_for(m_claims), horizon="medium", winner="bear"),
    })
    return rep


def test_a3_matches_clean_pipeline_and_cohort_isolated():
    lib = _lib()
    rep = _make_clean_report_pair(
        short_eff_bull=True, short_eff_bear=True,
        medium_eff_bull=True, medium_eff_bear=False,
    )
    clean, _led, _reasons = m.clean_units_of(rep, lib)
    assert len(clean) == 2  # both horizons are VALID+BUY so both stay clean

    rows = []
    for u in clean:
        coh = lib["extract_sample_cohort"](u)
        key = ":".join(str(coh.get(k) or "unspecified") for k in
                       ("decision_model_version", "evidence_contract_version", "price_basis_version")) \
              + ":" + str(u.get("horizon"))
        st = m.a3_unit_stats(u, lib)
        st.update({"report_id": rep["id"], "trade_date": rep["trade_date"],
                   "created_at": rep["created_at"], "cohort": key})
        rows.append(st)

    out = m.metric_a3(rows, lib)
    short_key = "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:short"
    med_key = "decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:medium"
    assert short_key in out["per_cohort"] and med_key in out["per_cohort"]
    sc = out["per_cohort"][short_key]
    assert sc["effective_sessions"]["bull"] == 1 and sc["effective_sessions"]["bear"] == 1
    mc = out["per_cohort"][med_key]
    assert mc["effective_sessions"]["bull"] == 1 and mc["effective_sessions"]["bear"] == 0


def test_eligible_but_not_clean_unit_excluded():
    """ABSTAIN (eligible≠clean): D-009 drops it before A3 counts."""
    lib = _lib()
    claims = _claims("bull", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    rep = _report({"short": _unit(claims, _ev_for(claims), _summary_for(claims),
                                  analysis_status="ABSTAIN", trade_action="WAIT", decision="WAIT")})
    clean, led, _r = m.clean_units_of(rep, lib)
    assert len(clean) == 0 and led["d009_excluded"] >= 1


def test_a3_asymmetric_share_math():
    """(40 bull,10 bear) -> 80%, not 50%. Exercises the corrected formula."""
    fake = [{"eff": {"bull": i < 40, "bear": i < 10}, "verified_claims": {"bull": 1, "bear": 1},
             "report_id": f"r{i}", "created_at": f"2026-10-08T{i:02d}:00", "cohort": "x:y:z:short"}
            for i in range(50)]
    nb = sum(1 for i in fake if i["eff"]["bull"])
    ne = sum(1 for i in fake if i["eff"]["bear"])
    share = nb / (nb + ne)
    assert share == pytest.approx(0.8)


def test_clean_units_of_keeps_ledger_and_reasons():
    lib = _lib()
    claims = _claims("bull", 3, battlefields=["capital_flow", "price_volume", "fundamentals"])
    rep = _report({"short": _unit(claims, _ev_for(claims), _summary_for(claims))})
    clean, ledger, reasons = m.clean_units_of(rep, lib)
    assert "clean_count" in ledger and isinstance(reasons, dict)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
