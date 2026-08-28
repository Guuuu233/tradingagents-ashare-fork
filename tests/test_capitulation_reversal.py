"""Tests for P1-2: Capitulation / Reversal candidate features and staged entry.

Verifies:
1. Sample insufficiency -> volume_regime=insufficient_data, reversal_state=insufficient_data.
2. High volume stagnation sequence -> volume_regime=high_volume_stagnation_candidate (not BUY).
3. Extreme volume capitulation sequence -> volume_regime=capitulation_candidate, reversal_unconfirmed;
   decision routing maps Bull direction to trade_action=WAIT (never BUY).
4. Follow-through appearing strictly after cutoff (T+1) MUST NOT lift state to reversal_confirmed (anti-lookahead).
5. Follow-through appearing before/at cutoff -> reversal_confirmed; allows staged entry with position_pct <= 10%.
6. Insufficient conditions -> WAIT (not permanent NO_TRADE/ABSTAIN on VALID analysis path).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.utils.decision_status import (
    ACTION_BUY,
    ACTION_HOLD,
    ACTION_NO_TRADE,
    ACTION_WAIT,
    ANALYSIS_ABSTAIN,
    ANALYSIS_INVALID_RUN,
    ANALYSIS_VALID,
    CONFIRM_CONFIRMED,
    CONFIRM_UNRESOLVED,
    DIRECTION_BULL,
    DIRECTION_NA,
    DIRECTION_NEUTRAL,
    DecisionStatus,
    is_calibration_eligible,
    is_non_executable_status,
    status_from_manager_verdict,
)
from tradingagents.graph.data_collector import (
    MAX_REVERSAL_STAGED_POSITION_PCT,
    REVERSAL_STATE_CONFIRMED,
    REVERSAL_STATE_INSUFFICIENT_DATA,
    REVERSAL_STATE_NONE,
    REVERSAL_STATE_UNCONFIRMED,
    VOLUME_REGIME_CAPITULATION,
    VOLUME_REGIME_HIGH_VOLUME_STAGNATION,
    VOLUME_REGIME_INSUFFICIENT_DATA,
    VOLUME_REGIME_NORMAL,
    compute_vpa_deterministic_features,
)


def _generate_ohlcv_series(
    n_bars: int = 30,
    base_price: float = 100.0,
    base_volume: float = 10000.0,
    start_date: str = "2026-07-01",
) -> pd.DataFrame:
    """Generate baseline normal OHLCV DataFrame."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    dates = [(start_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_bars)]

    # Stable baseline with small price drift and normal volume
    closes = [base_price + (i % 3 - 1) * 0.5 for i in range(n_bars)]
    opens = [c - 0.2 for c in closes]
    highs = [max(o, c) + 0.5 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.5 for o, c in zip(opens, closes)]
    volumes = [base_volume * (1.0 + (i % 5 - 2) * 0.05) for i in range(n_bars)]

    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# ── 1. 样本不足测试 ─────────────────────────────────────────────────────────────


def test_insufficient_data_returns_insufficient_data_status():
    # Empty DataFrame
    res_empty = compute_vpa_deterministic_features(pd.DataFrame(), cutoff="2026-08-01")
    assert res_empty["volume_regime"] == VOLUME_REGIME_INSUFFICIENT_DATA
    assert res_empty["reversal_state"] == REVERSAL_STATE_INSUFFICIENT_DATA

    # Missing OHLCV columns
    df_missing_cols = pd.DataFrame({
        "date": ["2026-08-01"],
        "close": [100.0],
    })
    res_missing = compute_vpa_deterministic_features(df_missing_cols, cutoff="2026-08-01")
    assert res_missing["volume_regime"] == VOLUME_REGIME_INSUFFICIENT_DATA
    assert res_missing["reversal_state"] == REVERSAL_STATE_INSUFFICIENT_DATA

    # Too few bars (< window + 5 = 25 bars)
    df_short = _generate_ohlcv_series(n_bars=15)
    res_short = compute_vpa_deterministic_features(df_short, cutoff=df_short["date"].iloc[-1])
    assert res_short["volume_regime"] == VOLUME_REGIME_INSUFFICIENT_DATA
    assert res_short["reversal_state"] == REVERSAL_STATE_INSUFFICIENT_DATA
    assert res_short["volume_regime"] != VOLUME_REGIME_NORMAL


# ── 2. 高量滞涨序列测试 ─────────────────────────────────────────────────────────


def test_high_volume_stagnation_candidate_feature_and_decision_routing():
    df = _generate_ohlcv_series(n_bars=30, base_price=100.0, base_volume=10000.0)
    cutoff = df["date"].iloc[-1]

    # Modify the last bar to be extreme high volume with tiny price move (stagnation)
    df.loc[df.index[-1], "volume"] = 30000.0  # 3x volume (volume_ratio >= 1.8, z-score >= 1.5)
    df.loc[df.index[-1], "open"] = 100.0
    df.loc[df.index[-1], "high"] = 100.4
    df.loc[df.index[-1], "low"] = 99.8
    df.loc[df.index[-1], "close"] = 100.1  # +0.1% change, narrow spread <= 0.015

    vpa = compute_vpa_deterministic_features(df, cutoff=cutoff)
    assert vpa["volume_regime"] == VOLUME_REGIME_HIGH_VOLUME_STAGNATION
    assert vpa["features"]["volume_ratio"] >= 1.8
    assert abs(vpa["features"]["pct_change"]) <= 0.015
    assert vpa["as_of"] == cutoff

    # Decision status wiring: manager verdict with BULL direction must NOT produce BUY
    manager_verdict = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 60,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    status = status_from_manager_verdict(
        manager_verdict,
        vpa_context=vpa,
        claims_verification=[],
    )
    # High volume stagnation candidate blocks BUY -> routes to WAIT
    assert status.analysis_status == ANALYSIS_VALID
    assert status.trade_action == ACTION_WAIT
    assert status.trade_action != ACTION_BUY
    assert any("stagnation" in c for c in status.reason_codes)


# ── 3. 极端放量下跌 capitulation 序列测试 ──────────────────────────────────────


def test_capitulation_candidate_unconfirmed_blocks_buy():
    df = _generate_ohlcv_series(n_bars=30, base_price=100.0, base_volume=10000.0)
    cutoff = df["date"].iloc[-1]

    # Modify last bar to extreme volume panic sell (capitulation)
    df.loc[df.index[-1], "volume"] = 40000.0  # 4x volume (z-score > 2.0)
    df.loc[df.index[-1], "open"] = 100.0
    df.loc[df.index[-1], "high"] = 100.5
    df.loc[df.index[-1], "low"] = 94.0
    df.loc[df.index[-1], "close"] = 95.0  # -5.0% drop

    vpa = compute_vpa_deterministic_features(df, cutoff=cutoff)
    assert vpa["volume_regime"] == VOLUME_REGIME_CAPITULATION
    assert vpa["reversal_state"] == REVERSAL_STATE_UNCONFIRMED
    assert vpa["features"]["volume_zscore"] >= 2.0
    assert vpa["features"]["pct_change"] <= -0.03

    # Decision status wiring: manager bull verdict must be mapped to WAIT, not BUY
    manager_verdict = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 50,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    status = status_from_manager_verdict(
        manager_verdict,
        vpa_context=vpa,
        claims_verification=[],
    )
    assert status.analysis_status == ANALYSIS_VALID
    assert status.trade_action == ACTION_WAIT
    assert status.trade_action != ACTION_BUY
    assert is_non_executable_status(status) is True
    assert any("capitulation" in c for c in status.reason_codes)


# ── 4. 防前视钉：cutoff 之后 (T+1) 的 follow-through 严禁提升状态 ──────────────


def test_anti_lookahead_follow_through_after_cutoff_is_ignored():
    # 32 bars total: bar 29 (index 29) is capitulation on 2026-08-01 (cutoff)
    # bar 30 (index 30) is T+1 on 2026-08-02 with strong rebound
    # bar 31 (index 31) is T+2 on 2026-08-03
    df = _generate_ohlcv_series(n_bars=32, base_price=100.0, base_volume=10000.0, start_date="2026-07-03")

    cutoff_date = df["date"].iloc[29]  # 2026-08-01

    # Bar 29: Capitulation drop on cutoff date
    df.loc[29, "volume"] = 40000.0
    df.loc[29, "open"] = 98.0
    df.loc[29, "high"] = 98.5
    df.loc[29, "low"] = 92.0
    df.loc[29, "close"] = 93.0  # -5% drop

    # Bar 30 (T+1): Strong rebound AFTER cutoff
    df.loc[30, "volume"] = 25000.0
    df.loc[30, "open"] = 94.0
    df.loc[30, "high"] = 100.0
    df.loc[30, "low"] = 93.5
    df.loc[30, "close"] = 99.0  # +6.4% rebound

    # Bar 31 (T+2): Continued rise
    df.loc[31, "volume"] = 20000.0
    df.loc[31, "open"] = 99.0
    df.loc[31, "high"] = 103.0
    df.loc[31, "low"] = 98.5
    df.loc[31, "close"] = 102.0

    # Test at cutoff date (bar 29): Must ONLY see bars <= cutoff_date
    vpa_at_cutoff = compute_vpa_deterministic_features(df, cutoff=cutoff_date)
    assert vpa_at_cutoff["volume_regime"] == VOLUME_REGIME_CAPITULATION
    assert vpa_at_cutoff["reversal_state"] == REVERSAL_STATE_UNCONFIRMED
    assert vpa_at_cutoff["reversal_state"] != REVERSAL_STATE_CONFIRMED
    assert vpa_at_cutoff["as_of"] == cutoff_date

    # Test later on T+1 date: now bar 30 is within cutoff -> reversal can be confirmed
    t1_date = df["date"].iloc[30]
    vpa_at_t1 = compute_vpa_deterministic_features(df, cutoff=t1_date)
    assert vpa_at_t1["volume_regime"] == VOLUME_REGIME_CAPITULATION
    assert vpa_at_t1["reversal_state"] == REVERSAL_STATE_CONFIRMED
    assert vpa_at_t1["as_of"] == t1_date


# ── 5. cutoff 前已有确认 bars -> reversal_confirmed 与分层小仓上限 ──────────────


def test_reversal_confirmed_allows_staged_entry_with_position_cap():
    df = _generate_ohlcv_series(n_bars=32, base_price=100.0, base_volume=10000.0, start_date="2026-07-03")

    # Bar 28 (3 days before cutoff): Capitulation drop
    df.loc[28, "volume"] = 40000.0
    df.loc[28, "open"] = 98.0
    df.loc[28, "high"] = 98.5
    df.loc[28, "low"] = 92.0
    df.loc[28, "close"] = 93.0  # -5% drop

    # Bar 29 (2 days before cutoff): Bottom stabilization / higher low
    df.loc[29, "volume"] = 22000.0
    df.loc[29, "open"] = 93.5
    df.loc[29, "high"] = 96.0
    df.loc[29, "low"] = 93.0
    df.loc[29, "close"] = 95.5

    # Bar 30 (1 day before cutoff): Confirmation follow-through
    df.loc[30, "volume"] = 20000.0
    df.loc[30, "open"] = 95.8
    df.loc[30, "high"] = 98.0
    df.loc[30, "low"] = 95.0
    df.loc[30, "close"] = 97.5

    # Bar 31 (cutoff date): Continued confirmation
    df.loc[31, "volume"] = 18000.0
    df.loc[31, "open"] = 97.5
    df.loc[31, "high"] = 99.5
    df.loc[31, "low"] = 97.0
    df.loc[31, "close"] = 99.0

    cutoff_date = df["date"].iloc[31]
    vpa = compute_vpa_deterministic_features(df, cutoff=cutoff_date)
    assert vpa["volume_regime"] == VOLUME_REGIME_CAPITULATION
    assert vpa["reversal_state"] == REVERSAL_STATE_CONFIRMED

    # Wire to manager verdict: BUY is allowed as staged entry, but position_pct capped <= 10%
    manager_verdict = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 50,  # Manager requested 50%
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    status = status_from_manager_verdict(
        manager_verdict,
        vpa_context=vpa,
        claims_verification=[],
    )
    assert status.analysis_status == ANALYSIS_VALID
    assert status.trade_action == ACTION_BUY
    assert any("reversal_confirmed" in c for c in status.reason_codes)

    # Position cap assertion
    from tradingagents.agents.utils.decision_status import resolve_staged_entry_position
    capped_pos = resolve_staged_entry_position(requested_position_pct=50, vpa_context=vpa)
    assert capped_pos <= MAX_REVERSAL_STAGED_POSITION_PCT
    assert capped_pos == 10.0


# ── 6. 条件不足维持 WAIT（不是永久 NO_TRADE）──────────────────────────────────


def test_insufficient_confirmation_maintains_wait_not_no_trade():
    df = _generate_ohlcv_series(n_bars=30, base_price=100.0, base_volume=10000.0)
    cutoff = df["date"].iloc[-1]

    # Capitulation on cutoff bar
    df.loc[df.index[-1], "volume"] = 40000.0
    df.loc[df.index[-1], "open"] = 100.0
    df.loc[df.index[-1], "high"] = 100.5
    df.loc[df.index[-1], "low"] = 94.0
    df.loc[df.index[-1], "close"] = 94.5

    vpa = compute_vpa_deterministic_features(df, cutoff=cutoff)
    assert vpa["reversal_state"] == REVERSAL_STATE_UNCONFIRMED

    manager_verdict = {
        "direction": "看多",
        "winner": "bull",
        "position_pct": 30,
        "consistency_check_passed": True,
        "failed_checks": [],
    }
    status = status_from_manager_verdict(
        manager_verdict,
        vpa_context=vpa,
        claims_verification=[],
    )
    # Crucial assertion: analysis_status is VALID, trade_action is WAIT (NOT NO_TRADE, NOT ABSTAIN)
    assert status.analysis_status == ANALYSIS_VALID
    assert status.trade_action == ACTION_WAIT
    assert status.trade_action != ACTION_NO_TRADE
    assert status.analysis_status != ANALYSIS_ABSTAIN
    assert status.analysis_status != ANALYSIS_INVALID_RUN
    # In calibration / directional backtest, WAIT is non-directional
    assert is_calibration_eligible({"analysis_status": status.analysis_status, "trade_action": status.trade_action, "probability": 0.6}) is False


# ── 7. Trader 节点集成测试 ───────────────────────────────────────────────────────


def test_trader_node_respects_vpa_wait_state():
    state = {
        "company_of_interest": "002241.SZ",
        "trade_date": "2026-08-01",
        "investment_plan": "建议买入歌尔股份",
        "market_report": "量价出现恐慌抛售候选，尚未确认反转",
        "sentiment_report": "情绪中性",
        "news_report": "无重大负面",
        "fundamentals_report": "基本面稳健",
        "fund_flow_consensus_guard": {"blocked": False, "direction_allowed": True},
        "decision_status": {
            "analysis_status": ANALYSIS_VALID,
            "direction": DIRECTION_BULL,
            "trade_action": ACTION_WAIT,
            "risk_status": "OK",
            "confirmation_state": CONFIRM_UNRESOLVED,
            "reason_codes": ["vpa_capitulation_unconfirmed_wait"],
        },
    }

    mock_llm = MagicMock()
    mock_memory = MagicMock()
    mock_memory.get_memories.return_value = []

    trader_node = create_trader(mock_llm, mock_memory)
    result = asyncio.run(trader_node(state))

    # Trader must short-circuit without calling LLM to invent BUY orders
    mock_llm.invoke.assert_not_called()
    assert result["trade_action"] == ACTION_WAIT
    assert result["analysis_status"] == ANALYSIS_VALID
    assert "上游决策状态为" in result["trader_investment_plan"]
    assert "不得生成方向性交易计划" in result["trader_investment_plan"]
