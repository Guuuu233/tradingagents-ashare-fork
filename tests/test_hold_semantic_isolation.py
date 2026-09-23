"""Tests for DAV-1139 Phase B (DAV-1218): Stage 3.5 HOLD semantic isolation.

Contract requirements:
1. ``hold_defensive``: HOLD + core market evidence insufficient/unavailable or
   violating the existing OHLCV freshness contract (ohlcv_gate_applied OR
   is_daily_ohlcv_unavailable semantics) → isolated from primary denominator.
   Anchor 8ba4d146 (601989.SH) must be hit by the generic rule.
2. ``hold_conflict``: HOLD + fund_flow_dispute_gate_applied=true → isolated,
   counted separately. Anchors a69babf8 / d125a11a / 96bb2bdc; 96bb2bdc also
   keeps its price_basis_contaminated reason (multi-reason auditability).
3. Analytical HOLD stays in the primary denominator.
4. Contract-era HOLD lacking all data-sufficiency structures fails closed to
   ``hold_unresolved``; legacy samples without contract markers stay eligible.
5. Legit previous-trading-day / verified freshness must NOT be misjudged
   defensive (no raw as_of arithmetic — existing provenance semantics reused).
6. Multi-reason ledger + union conservation:
   eligible == clean + |union(hold_semantic, price_basis)|.
7. D-009 excluded_counts and price-basis counters keep their original
   interface; hold_* reasons never leak into D-009 excluded_counts.

All tests use in-memory fixtures; no network, no production DB.
"""

from typing import Any, Mapping, Optional

import pytest

from tradingagents.agents.utils.price_basis_isolation import (
    REASON_CONTAMINATED,
)
from tradingagents.agents.utils.shadow_credit import (
    PROTOCOL_VERSION_V2_STRUCTURED,
    REASON_HOLD_CONFLICT,
    REASON_HOLD_DEFENSIVE,
    REASON_HOLD_UNRESOLVED,
    classify_hold_semantic_exclusion,
    classify_v2_report_d009_exclusion,
    collect_h1b_exclusion_reasons,
    collect_hold_semantic_reasons,
    filter_v2_completed_reports,
)

# Real Phase A adjudicated anchors (DAV-1139)
DEFENSIVE_ANCHOR = "8ba4d1463ba447ed8ab925f1d8700d3c"  # 601989.SH @2026-08-04
CONFLICT_ANCHOR_A = "a69babf84e73414a80a6feb89ae69fd3"  # 600519.SH @2026-09-04 (clean)
CONFLICT_ANCHOR_B = "d125a11a2063484884ca27b787bfcc9d"  # 600519.SH @2026-09-01 (clean)
CONFLICT_ANCHOR_PB = "96bb2bdcd2aa44a1ac90216b51602a50"  # 600547.SH @2026-07-30 (also contaminated)


def _available_mdc(as_of: str = "2026-09-04", requested: str = "2026-09-04") -> dict:
    """market_data_context with usable, verified daily OHLCV."""
    return {
        "analysis_baseline_date": requested,
        "source_provenance": {
            "stock_data": {
                "requested_as_of": requested,
                "actual_as_of": as_of,
                "as_of": as_of,
                "status": "available",
                "provenance_status": "verified",
            }
        },
        "daily": {"as_of": as_of, "completeness": "completed"},
    }


def _unavailable_mdc(as_of: str = "2025-08-12", requested: str = "2026-08-04") -> dict:
    """market_data_context mirroring the 601989 stale-OHLCV failure."""
    return {
        "analysis_baseline_date": requested,
        "source_provenance": {
            "stock_data": {
                "requested_as_of": requested,
                "actual_as_of": as_of,
                "as_of": as_of,
                "status": "unavailable",
                "provenance_status": "refused",
                "gap": f"【数据获取失败】stock_data：实际最新数据日 {as_of} 早于请求日期 {requested}",
            }
        },
        "daily": {"as_of": as_of, "completeness": "completed"},
    }


def make_hold_report(
    *,
    report_id: str = "rep-hold-test",
    symbol: str = "600519.SH",
    trade_action: Optional[str] = "HOLD",
    verdict_extra: Optional[Mapping[str, Any]] = None,
    market_data_context: Any = "__default__",
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a qualifying v2 VALID/HOLD report fixture."""
    verdict: dict[str, Any] = {"winner": "tie", "direction": "中性"}
    if verdict_extra:
        verdict.update(verdict_extra)
    rep: dict[str, Any] = {
        "id": report_id,
        "symbol": symbol,
        "trade_date": "2026-09-04",
        "status": "completed",
        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        "analysis_status": "VALID",
        "manager_verdict": verdict,
        "claims": [
            {
                "claim_id": "c1",
                "speaker": "Bull Researcher",
                "stance": "bullish",
                "status": "verified",
                "claim": "Bull argument",
            }
        ],
    }
    if trade_action is not None:
        rep["trade_action"] = trade_action
    if market_data_context != "__default__":
        if market_data_context is not None:
            rep["market_data_context"] = market_data_context
    else:
        rep["market_data_context"] = _available_mdc()
    if extra_fields:
        rep.update(extra_fields)
    return rep


# ── hold_defensive ────────────────────────────────────────────────────────────


class TestHoldDefensive:
    def test_anchor_8ba4d146_hit_by_generic_rule(self):
        """601989 anchor: D-009 eligible but Stage 3.5 isolates as hold_defensive."""
        rep = make_hold_report(
            report_id=DEFENSIVE_ANCHOR,
            symbol="601989.SH",
            verdict_extra={"ohlcv_gate_applied": True, "fund_flow_dispute_gate_applied": False},
            market_data_context=_unavailable_mdc(),
        )
        # D-009 §5 semantics unchanged: still eligible at Stage 3
        assert classify_v2_report_d009_exclusion(rep) is None
        assert classify_hold_semantic_exclusion(rep) == REASON_HOLD_DEFENSIVE

    def test_defensive_via_provenance_status_without_gate_flag(self):
        """Structured provenance alone (no gate flag) catches defensive HOLD."""
        rep = make_hold_report(
            report_id="rep-def-prov",
            verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": False},
            market_data_context=_unavailable_mdc(),
        )
        assert classify_hold_semantic_exclusion(rep) == REASON_HOLD_DEFENSIVE

    def test_buy_with_gate_flag_not_isolated(self):
        """Stage 3.5 applies to HOLD only."""
        rep = make_hold_report(
            report_id="rep-buy",
            trade_action="BUY",
            verdict_extra={
                "winner": "bull",
                "ohlcv_gate_applied": True,
                "fund_flow_dispute_gate_applied": True,
            },
        )
        assert collect_hold_semantic_reasons(rep) == []
        assert classify_hold_semantic_exclusion(rep) is None


# ── hold_conflict ─────────────────────────────────────────────────────────────


class TestHoldConflict:
    @pytest.mark.parametrize("rid", [CONFLICT_ANCHOR_A, CONFLICT_ANCHOR_B, CONFLICT_ANCHOR_PB])
    def test_conflict_anchors(self, rid):
        rep = make_hold_report(
            report_id=rid,
            verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": True},
        )
        assert classify_hold_semantic_exclusion(rep) == REASON_HOLD_CONFLICT

    def test_multi_reason_96bb2bdc_keeps_price_basis_reason(self):
        """96bb2bdc is both hold_conflict AND price_basis_contaminated — all
        reasons must be readable, not overwritten."""
        rep = make_hold_report(
            report_id=CONFLICT_ANCHOR_PB,
            verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": True},
        )
        reasons = collect_h1b_exclusion_reasons(rep)
        assert REASON_HOLD_CONFLICT in reasons
        assert REASON_CONTAMINATED in reasons


# ── analytical HOLD / freshness guardrails ───────────────────────────────────


class TestAnalyticalHold:
    def test_analytical_hold_stays_in_denominator(self):
        rep = make_hold_report(
            verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": False},
        )
        assert collect_hold_semantic_reasons(rep) == []
        filtered = filter_v2_completed_reports([rep])
        assert len(filtered) == 1

    def test_verified_previous_trading_day_not_defensive(self):
        """Upstream marked stock_data available+verified on the prior trading
        day (pre-market / unclosed session) — must NOT be re-judged stale by
        raw as_of arithmetic."""
        rep = make_hold_report(
            verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": False},
            market_data_context=_available_mdc(as_of="2026-09-03", requested="2026-09-04"),
        )
        assert collect_hold_semantic_reasons(rep) == []


# ── hold_unresolved fail-close ───────────────────────────────────────────────


class TestHoldUnresolved:
    def test_contract_era_hold_missing_structures_fails_closed(self):
        """New-contract HOLD with neither gate fields nor market_data_context:
        fail-close to hold_unresolved, never guessed from text."""
        rep = make_hold_report(
            report_id="rep-unresolved",
            verdict_extra=None,  # verdict has winner only, no gate keys
            market_data_context=None,
            extra_fields={
                "decision_model_version": "decision_model.v1",
                "evidence_contract_version": "evidence_contract.v1",
            },
        )
        assert classify_hold_semantic_exclusion(rep) == REASON_HOLD_UNRESOLVED
        filtered = filter_v2_completed_reports([rep])
        assert filtered == []

    def test_legacy_hold_without_structures_not_unresolved(self):
        """Legacy sample (no contract markers) lacking the structures stays
        eligible — fail-close applies to contract-era samples only."""
        rep = make_hold_report(
            report_id="rep-legacy-hold",
            market_data_context=None,
        )
        assert collect_hold_semantic_reasons(rep) == []
        assert len(filter_v2_completed_reports([rep])) == 1


# ── ledger accounting & union conservation ───────────────────────────────────


class TestLedgerAccounting:
    def _pool(self) -> list[dict[str, Any]]:
        return [
            # defensive anchor (not in price-basis manifest pools)
            make_hold_report(
                report_id=DEFENSIVE_ANCHOR,
                symbol="601989.SH",
                verdict_extra={"ohlcv_gate_applied": True, "fund_flow_dispute_gate_applied": False},
                market_data_context=_unavailable_mdc(),
            ),
            # conflict anchors
            make_hold_report(
                report_id=CONFLICT_ANCHOR_A,
                verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": True},
            ),
            make_hold_report(
                report_id=CONFLICT_ANCHOR_B,
                verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": True},
            ),
            # conflict + price_basis_contaminated (overlap)
            make_hold_report(
                report_id=CONFLICT_ANCHOR_PB,
                verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": True},
            ),
            # analytical HOLD stays
            make_hold_report(
                report_id="rep-analytical-hold",
                verdict_extra={"ohlcv_gate_applied": False, "fund_flow_dispute_gate_applied": False},
            ),
        ]

    def test_union_conservation_and_overlap(self):
        qualifying, excluded_counts, ledger, reasons = filter_v2_completed_reports(
            self._pool(), return_ledger=True, return_exclusion_reasons=True
        )
        assert ledger["eligible_count"] == 5
        assert ledger["hold_defensive"] == 1
        assert ledger["hold_conflict"] == 3
        assert ledger["hold_semantic_isolated"] == 4
        assert ledger["prediction_eligible_count"] == 1
        assert ledger["price_basis_isolated"] == 1
        assert ledger["hold_price_basis_overlap"] == 1
        # union(4, 1) - overlap 1 = 4 isolated total → clean = 5 - 4 = 1
        assert ledger["clean_count"] == 1
        assert len(qualifying) == 1
        assert ledger["eligible_count"] == ledger["clean_count"] + (
            ledger["hold_semantic_isolated"] + ledger["price_basis_isolated"] - ledger["hold_price_basis_overlap"]
        )
        # multi-reason auditability
        assert set(reasons[CONFLICT_ANCHOR_PB]) == {REASON_HOLD_CONFLICT, REASON_CONTAMINATED}
        assert reasons[DEFENSIVE_ANCHOR] == [REASON_HOLD_DEFENSIVE]

    def test_d009_excluded_counts_not_polluted(self):
        """hold_* reasons must NEVER enter D-009 excluded_counts."""
        _, excluded_counts, ledger = filter_v2_completed_reports(self._pool(), return_ledger=True)
        assert set(excluded_counts.keys()) == {
            "legacy_null",
            "abstain",
            "invalid_run",
            "data_error",
            "no_trade",
            "wait",
        }
        assert sum(excluded_counts.values()) == 0
        assert ledger["d009_excluded"] == 0

    def test_backwards_compatible_return_shapes(self):
        pool = self._pool()
        only = filter_v2_completed_reports(pool)
        assert isinstance(only, list)
        two = filter_v2_completed_reports(pool, return_excluded_counts=True)
        assert len(two) == 2
        three = filter_v2_completed_reports(pool, return_ledger=True)
        assert len(three) == 3
