"""A2: missing daily OHLCV must fail-closed — no directional manager winner."""

from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    extract_and_validate_manager_verdict,
    is_daily_ohlcv_unavailable,
)


def _bull_manager_block() -> str:
    return (
        "裁决：多头胜出。\n"
        '<!-- MANAGER_VERDICT: {'
        '"direction": "偏多", '
        '"winner": "bull", '
        '"reason": "技术面突破", '
        '"position_pct": 15, '
        '"entry": "100", '
        '"target": "110", '
        '"stop_loss": "95", '
        '"adopted_claim_ids": [], '
        '"partially_adopted_claims": [], '
        '"rejected_claim_ids": [], '
        '"excluded_evidence": [], '
        '"dispute_map": []'
        '} -->'
    )


def test_is_daily_ohlcv_unavailable_from_provenance_and_ledger():
    ctx = {
        "source_provenance": {
            "stock_data": {
                "status": "unavailable",
                "gap": "【数据获取失败】stock_data：688981.SH 在 2026-07-22 无有效完整日线数据",
            }
        }
    }
    assert is_daily_ohlcv_unavailable(ctx) is True

    ctx2 = {
        "data_failure_ledger": [
            {
                "source": "stock_data",
                "status": "unavailable",
                "gap": "【数据获取失败】stock_data：无有效完整日线数据",
            }
        ]
    }
    assert is_daily_ohlcv_unavailable(ctx2) is True

    assert is_daily_ohlcv_unavailable(
        {"source_provenance": {"stock_data": {"status": "available", "as_of": "2026-07-22"}}}
    ) is False
    # Compatible: caller omitted context entirely.
    assert is_daily_ohlcv_unavailable(None) is False
    # Production: empty / missing stock_data provenance is fail-closed.
    assert is_daily_ohlcv_unavailable({}) is True
    assert is_daily_ohlcv_unavailable({"source_provenance": {}}) is True
    assert is_daily_ohlcv_unavailable(
        {"source_provenance": {"news": {"status": "available", "as_of": "2026-07-22"}}}
    ) is True


def test_missing_ohlcv_forces_tie_not_directional_winner():
    """688981-class fixture: LLM says bull, but OHLCV missing → winner must be tie."""
    ctx = {
        "source_provenance": {
            "stock_data": {
                "status": "unavailable",
                "gap": "【数据获取失败】stock_data：688981.SH 在 2026-07-22 无有效完整日线数据",
            }
        },
        "data_failure_ledger": [
            {
                "source": "stock_data",
                "status": "unavailable",
                "gap": "【数据获取失败】stock_data：无有效完整日线数据",
            }
        ],
    }
    verdict = extract_and_validate_manager_verdict(
        _bull_manager_block(),
        market_data_context=ctx,
    )
    assert verdict["winner"] == "tie"
    assert verdict["direction"] in {"中性", "观望", "hold", "HOLD", "neutral", "NEUTRAL"}
    assert "OHLCV" in verdict["reason"] or "日线" in verdict["reason"]
    assert verdict.get("ohlcv_gate_applied") is True
    assert verdict["winner"] not in {"bull", "bear"}


def test_empty_context_forces_tie_when_explicitly_provided():
    """Provided empty context (production path) must fail-closed to tie."""
    verdict = extract_and_validate_manager_verdict(
        _bull_manager_block(),
        market_data_context={},
    )
    assert verdict["winner"] == "tie"
    assert verdict.get("ohlcv_gate_applied") is True


def test_omitted_context_keeps_compatible_bull_winner():
    """Legacy callers that omit market_data_context keep prior behavior."""
    verdict = extract_and_validate_manager_verdict(_bull_manager_block())
    assert verdict["winner"] == "bull"
    assert verdict.get("ohlcv_gate_applied") is not True


def test_available_ohlcv_keeps_bull_winner():
    ctx = {
        "source_provenance": {
            "stock_data": {"status": "available", "as_of": "2026-07-22"},
        }
    }
    verdict = extract_and_validate_manager_verdict(
        _bull_manager_block(),
        market_data_context=ctx,
    )
    assert verdict["winner"] == "bull"
    assert verdict.get("ohlcv_gate_applied") is not True


def test_is_daily_ohlcv_unavailable_for_unverified_or_future_as_of():
    """P0-2b: available_unverified_as_of or unverified provenance must fail-closed for OHLCV."""
    # 1. available_unverified_as_of with no as_of -> True
    ctx1 = {
        "source_provenance": {
            "stock_data": {
                "status": "available_unverified_as_of",
                "actual_as_of": None,
                "provenance_status": "unverified",
            }
        }
    }
    assert is_daily_ohlcv_unavailable(ctx1) is True

    # 2. available but no as_of / unverified -> True
    ctx2 = {
        "source_provenance": {
            "stock_data": {
                "status": "available",
                "actual_as_of": None,
                "provenance_status": "unverified",
            }
        }
    }
    assert is_daily_ohlcv_unavailable(ctx2) is True

    # 3. future status / provenance_status -> True
    ctx3 = {
        "source_provenance": {
            "stock_data": {
                "status": "future",
                "actual_as_of": "2026-08-25",
                "provenance_status": "future",
            }
        }
    }
    assert is_daily_ohlcv_unavailable(ctx3) is True


def test_evaluator_rejects_claims_quoting_unverified_fundamentals():
    """P0-2b: evidence quoting available_unverified_as_of source must not be verified."""
    from tradingagents.agents.utils.evidence_verifier import (
        EvidenceFactualTruthEvaluator,
        STATUS_VERIFIED,
        STATUS_SOURCE_UNAVAILABLE,
        STATUS_UNSUPPORTED,
    )

    evaluator = EvidenceFactualTruthEvaluator()

    # Fundamentals payload with numeric pairs but unverified as_of
    market_data_context = {
        "fundamentals": (
            "## Fundamentals for 688981.SH\n"
            "总资产 1234567890.12\n"
            "净资产 987654321.00\n"
            "归属于母公司所有者的净利润 11223344.55\n"
        ),
        "source_provenance": {
            "fundamentals": {
                "status": "available_unverified_as_of",
                "actual_as_of": None,
                "provenance_status": "unverified",
                "note": "有可解析财务字段与数值但缺少可验证 ISO 数据日期",
            },
            "stock_data": {
                "status": "available",
                "actual_as_of": "2026-07-22",
                "as_of": "2026-07-22",
                "provenance_status": "verified",
            },
        },
    }

    # Case A: claim quotes fundamentals by name and numeric value
    res_a = evaluator.evaluate_single_evidence(
        raw_evidence="根据 fundamentals，归属于母公司所有者的净利润 11223344.55",
        seven_reports={},
        market_data_context=market_data_context,
        claim_id="C-1",
    )
    assert res_a["status"] != STATUS_VERIFIED
    assert res_a["status"] in (STATUS_SOURCE_UNAVAILABLE, STATUS_UNSUPPORTED)

    # Case B: claim quotes the numeric string from market_data_context without explicit source name
    res_b = evaluator.evaluate_single_evidence(
        raw_evidence="归属于母公司所有者的净利润 11223344.55",
        seven_reports={},
        market_data_context=market_data_context,
        claim_id="C-2",
    )
    assert res_b["status"] != STATUS_VERIFIED
    assert res_b["status"] in (STATUS_SOURCE_UNAVAILABLE, STATUS_UNSUPPORTED)

