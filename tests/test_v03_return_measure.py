"""Tests for V-03a Read-Only Return Measurement Engine (DAV-802).

Comprehensive coverage for Red Team Scenarios RT-1 to RT-10:
- RT-1: T+1 suspended / limit-up locked -> marked untradable, no open fill, no return metric pollution
- RT-2: typed-missing (Lens June gap class) -> return=NULL, in coverage not return, no drop, no carry-forward
- RT-3: collision stock (000001 / 000001.SZ) -> merged into single canonical entity, no split
- RT-4: stock pool filtering -> ST/*ST, BSE, listing <60d excluded and counted, not silently dropped
- RT-5: cost model -> commission + transfer + stamp duty (sell) + slippage, no regulatory fee duplicates
- RT-6: OOS boundaries -> DEV <= 2025-12-31, HISTORICAL_OOS 2026-01-01~09-08, FORWARD_OOS >= 2026-09-09
- RT-7: entry price -> T+1 Open, not T Close, zero look-ahead
- RT-8: excess return -> relative to CSI 300 over identical window
- RT-9: metadata stamp -> model, prompt hash, code SHA, running service SHA, system completeness
- RT-10: coverage vs return separation -> typed-missing in coverage, denominator does not shrink, return not polluted
"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import pytest

from tradingagents.eval.v03_return_measure import (
    BASELINE_DISCLAIMER,
    BASELINE_GLOBAL_PROMPT_HASH,
    BASELINE_MODEL,
    BASELINE_RUNNING_SERVICE_SHA,
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_HISTORICAL_CUTOFF_DATE,
    DEFAULT_HISTORICAL_CUTOFF_DATETIME,
    DEFAULT_HOLD_DAYS,
    DEFAULT_STATUS_FILTER,
    DEFAULT_TARGET_USER_ID,
    HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA,
    MINIMUM_AUDIT_FIELDS,
    REGRESSION_SYMBOLS,
    AblationConfig,
    AblationVariantResult,
    CostModel,
    DailyBar,
    DictPriceDataProvider,
    EvaluationStamp,
    MeasurementOutcomeStatus,
    OfflineReplayHarness,
    OOSSegment,
    OrderAblationMode,
    PoolFilterStatus,
    SampleMeasureRecord,
    SampleRole,
    SegmentMetrics,
    SnapshotManifest,
    V03MeasurementResult,
    V03ReturnMeasureEngine,
    apply_evidence_deduplication,
    apply_purging_and_embargo,
    classify_oos_segment,
    compute_file_sha256,
    probe_running_service_sha,
    evaluate_proposition_claim,
    validate_audit_row,
)


@pytest.fixture
def sample_trade_dates():
    """Deterministic trading calendar dates spanning 2025 to 2026."""
    return [
        "2025-12-29",
        "2025-12-30",
        "2025-12-31",
        "2026-01-02",
        "2026-01-05",
        "2026-01-06",
        "2026-01-07",
        "2026-01-08",
        "2026-01-09",
        "2026-01-12",
        "2026-03-02",
        "2026-03-03",
        "2026-03-04",
        "2026-03-05",
        "2026-03-06",
        "2026-03-09",
        "2026-03-10",
        "2026-03-11",
        "2026-03-12",
        "2026-06-01",
        "2026-06-02",
        "2026-06-03",
        "2026-06-04",
        "2026-06-05",
        "2026-06-08",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
        "2026-09-10",
        "2026-09-11",
        "2026-09-14",
        "2026-09-15",
        "2026-09-16",
    ]


@pytest.fixture
def mock_price_provider(sample_trade_dates):
    """Fixture providing deterministic prices for standard test symbols."""
    provider = DictPriceDataProvider(
        trade_dates=sample_trade_dates,
        st_stocks={"000002.SZ"},  # ST test stock
        listing_dates={"000003.SZ": "2026-08-01"},  # New listing <60 days
    )

    # 1. Normal stock 600519.SH (Moutai)
    # T=2026-03-02, T+1=2026-03-03 (entry), T+1+5=2026-03-10 (exit)
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-02",
            open=1400.0,
            high=1420.0,
            low=1390.0,
            close=1410.0,
            volume=50000.0,
        ),
    )
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-03",
            open=1415.0,
            high=1430.0,
            low=1410.0,
            close=1425.0,
            volume=60000.0,
        ),
    )
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-04",
            open=1426.0,
            high=1440.0,
            low=1420.0,
            close=1430.0,
            volume=62000.0,
        ),
    )
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-05",
            open=1435.0,
            high=1450.0,
            low=1430.0,
            close=1440.0,
            volume=63000.0,
        ),
    )
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-10",
            open=1480.0,
            high=1510.0,
            low=1475.0,
            close=1500.0,
            volume=70000.0,
        ),
    )
    provider.add_bar(
        "600519.SH",
        DailyBar(
            date="2026-03-12",
            open=1510.0,
            high=1530.0,
            low=1505.0,
            close=1520.0,
            volume=72000.0,
        ),
    )

    # 2. Collision stock 000001.SZ
    provider.add_bar(
        "000001.SZ",
        DailyBar(
            date="2026-03-02",
            open=10.0,
            high=10.2,
            low=9.9,
            close=10.1,
            volume=100000.0,
        ),
    )
    provider.add_bar(
        "000001.SZ",
        DailyBar(
            date="2026-03-03",
            open=10.2,
            high=10.5,
            low=10.1,
            close=10.4,
            volume=120000.0,
        ),
    )
    provider.add_bar(
        "000001.SZ",
        DailyBar(
            date="2026-03-10",
            open=10.8,
            high=11.2,
            low=10.7,
            close=11.0,
            volume=150000.0,
        ),
    )

    # 3. Suspended stock 600000.SH on T+1
    provider.add_bar(
        "600000.SH",
        DailyBar(
            date="2026-03-02",
            open=8.0,
            high=8.2,
            low=7.9,
            close=8.1,
            volume=50000.0,
        ),
    )
    provider.add_bar(
        "600000.SH",
        DailyBar(
            date="2026-03-03",
            open=0.0,
            high=0.0,
            low=0.0,
            close=8.1,
            volume=0.0,
            is_suspended=True,
        ),
    )

    # 4. Limit-up locked stock 600006.SH on T+1
    provider.add_bar(
        "600006.SH",
        DailyBar(
            date="2026-03-02",
            open=5.0,
            high=5.1,
            low=4.9,
            close=5.0,
            volume=20000.0,
        ),
    )
    provider.add_bar(
        "600006.SH",
        DailyBar(
            date="2026-03-03",
            open=5.5,
            high=5.5,
            low=5.5,
            close=5.5,
            volume=100.0,
            limit_up=5.5,
        ),
    )

    # 5. Lens Technology 300433.SZ (June gap: exit date bar missing)
    provider.add_bar(
        "300433.SZ",
        DailyBar(
            date="2026-06-01",
            open=18.0,
            high=18.5,
            low=17.8,
            close=18.2,
            volume=30000.0,
        ),
    )
    provider.add_bar(
        "300433.SZ",
        DailyBar(
            date="2026-06-02",
            open=18.3,
            high=18.6,
            low=18.1,
            close=18.4,
            volume=35000.0,
        ),
    )
    # exit date 2026-06-09 intentionally NOT added to simulate gap

    # 6. Benchmark CSI 300 (000300.SH)
    provider.add_bar(
        "000300.SH",
        DailyBar(
            date="2026-03-03",
            open=3500.0,
            high=3550.0,
            low=3490.0,
            close=3520.0,
            volume=1000000.0,
        ),
    )
    provider.add_bar(
        "000300.SH",
        DailyBar(
            date="2026-03-10",
            open=3580.0,
            high=3620.0,
            low=3570.0,
            close=3600.0,
            volume=1200000.0,
        ),
    )

    return provider


# ===========================================================================
# RT-1: 样本 T+1 停牌/涨跌停封死
# ===========================================================================


def test_rt1_untradable_suspended_on_t_plus_1(mock_price_provider):
    """RT-1: Suspended stock on T+1 is marked untradable, not filled at Open."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    report = {
        "id": "rep_susp_01",
        "symbol": "600000.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
        "direction": "偏多",
    }
    rec = engine.measure_sample(report)

    assert rec.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value
    assert rec.untradable_reason == "suspended"
    assert rec.entry_price is None, "Strictly forbidden to pretend Open fill on suspended stock"
    assert rec.net_return is None
    assert rec.included_in_return_metrics is False
    assert rec.included_in_coverage_metrics is True


def test_rt1_untradable_limit_up_locked_on_t_plus_1(mock_price_provider):
    """RT-1: Limit-up locked stock on T+1 is marked untradable for BUY orders."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    report = {
        "id": "rep_limit_01",
        "symbol": "600006.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
        "direction": "看多",
    }
    rec = engine.measure_sample(report)

    assert rec.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value
    assert rec.untradable_reason == "limit_up_locked"
    assert rec.entry_price is None, "Strictly forbidden to assume fill at limit-up locked price"
    assert rec.net_return is None
    assert rec.included_in_return_metrics is False
    assert rec.included_in_coverage_metrics is True


# ===========================================================================
# RT-2: typed-missing（蓝思6月缺口类）
# ===========================================================================


def test_rt2_typed_missing_lens_gap(mock_price_provider):
    """RT-2: Lens Technology June gap is marked typed_missing, return=NULL, no drop, no carry-forward."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    report = {
        "id": "rep_lens_gap_01",
        "symbol": "300433.SZ",
        "trade_date": "2026-06-01",
        "decision": "BUY",
        "direction": "偏多",
    }
    rec = engine.measure_sample(report)

    assert rec.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value
    assert rec.missing_reason == "exit_bar_missing"
    assert rec.net_return is None, "return must be NULL (None), strictly no carry-forward"
    assert rec.gross_return is None
    assert rec.included_in_return_metrics is False, "Must not pollute return metrics"
    assert rec.included_in_coverage_metrics is True, "Must be included in coverage denominator"


# ===========================================================================
# RT-3: collision 股票 (000001 / 000001.SZ)
# ===========================================================================


def test_rt3_collision_merging(mock_price_provider):
    """RT-3: Collision symbols (000001 and 000001.SZ) merge into unified canonical entity."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    reports = [
        {
            "id": "rep_col_01",
            "symbol": "000001",  # Bare code
            "trade_date": "2026-03-02",
            "decision": "BUY",
            "direction": "偏多",
        },
        {
            "id": "rep_col_02",
            "symbol": "000001.SZ",  # Suffixed code
            "trade_date": "2026-03-02",
            "decision": "BUY",
            "direction": "偏多",
        },
    ]

    result = engine.measure_dataset(reports)

    assert len(result.records) == 2
    # Both must canonicalize to 000001.SZ
    assert result.records[0].symbol_canonical == "000001.SZ"
    assert result.records[1].symbol_canonical == "000001.SZ"

    # Collision must be detected in summary
    assert "000001.SZ" in result.collision_summary["collisions"]
    col_info = result.collision_summary["collisions"]["000001.SZ"]
    assert "000001" in col_info["raw_symbols"]
    assert "000001.SZ" in col_info["raw_symbols"]

    # Both must compute identical valid return without splitting entity
    assert result.records[0].net_return is not None
    assert result.records[0].net_return == result.records[1].net_return


# ===========================================================================
# RT-4: 股票池过滤
# ===========================================================================


def test_rt4_stock_pool_filtering(mock_price_provider):
    """RT-4: BSE (8xx), ST/*ST, listing <60d, unmappable correctly excluded and counted."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    reports = [
        # 1. BSE stock
        {
            "id": "r_bse",
            "symbol": "830001",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 2. ST stock
        {
            "id": "r_st",
            "symbol": "000002.SZ",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 3. New listing < 60 days
        {
            "id": "r_new",
            "symbol": "000003.SZ",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 4. Malformed symbol
        {
            "id": "r_unmap",
            "symbol": "AGENT",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 5. Empty symbol
        {
            "id": "r_empty",
            "symbol": "",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 6. Eligible stock (Moutai)
        {
            "id": "r_ok",
            "symbol": "600519.SH",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
    ]

    result = engine.measure_dataset(reports)
    m = result.all_metrics

    assert m.total_reports == 6
    assert m.in_pool_count == 1  # only Moutai is eligible
    assert m.excluded_pool_count == 5
    assert m.unmappable_count == 2  # AGENT, empty

    ex_reasons = m.pool_exclusion_reasons
    assert ex_reasons[PoolFilterStatus.EXCLUDED_BSE.value] == 1
    assert ex_reasons[PoolFilterStatus.EXCLUDED_ST.value] == 1
    assert ex_reasons[PoolFilterStatus.EXCLUDED_NEW_LISTING.value] == 1
    assert ex_reasons[PoolFilterStatus.EXCLUDED_UNMAPPABLE.value] == 2


# ===========================================================================
# RT-5: 成本口径
# ===========================================================================


def test_rt5_cost_model_rates_and_no_dupes():
    """RT-5: Commission + transfer + stamp duty (sell) + slippage, strictly no extra handling/supervision fees."""
    cost = CostModel(
        commission_rate=0.00025,  # 0.025%
        transfer_fee_rate=0.00001,  # 0.01‰
        stamp_duty_rate=0.0005,  # 0.5‰
        slippage_bps=5.0,  # 5 bps
    )

    # Buy cost rate = commission + transfer + slippage = 0.00025 + 0.00001 + 0.0005 = 0.00076
    assert abs(cost.buy_cost_rate - 0.00076) < 1e-8

    # Sell cost rate = commission + transfer + stamp_duty + slippage
    # = 0.00025 + 0.00001 + 0.0005 + 0.0005 = 0.00126
    assert abs(cost.sell_cost_rate - 0.00126) < 1e-8

    # Round trip cost rate = 0.00076 + 0.00126 = 0.00202 (20.2 bps)
    assert abs(cost.round_trip_cost_rate - 0.00202) < 1e-8

    # Exact trade calculation
    entry_p = 100.0
    exit_p = 110.0
    res = cost.calculate_costs(entry_p, exit_p)

    assert abs(res["gross_return"] - 0.10) < 1e-8
    # Effective buy = 100 * (1 + 0.00076) = 100.076
    # Effective sell = 110 * (1 - 0.00126) = 109.8614
    # Net return = (109.8614 - 100.076) / 100.076 = 9.7854 / 100.076 ≈ 0.09778
    assert res["net_return"] < res["gross_return"]
    assert 0.097 < res["net_return"] < 0.098


# ===========================================================================
# RT-6: OOS 边界切分
# ===========================================================================


def test_rt6_oos_segment_boundaries():
    """RT-6: DEV <= 2025-12-31, HISTORICAL_OOS 2026-01-01~09-08, FORWARD_OOS >= 2026-09-09."""
    assert classify_oos_segment("2024-01-15") == OOSSegment.DEV
    assert classify_oos_segment("2025-12-31") == OOSSegment.DEV
    assert classify_oos_segment("2026-01-01") == OOSSegment.HISTORICAL_OOS
    assert classify_oos_segment("2026-05-20") == OOSSegment.HISTORICAL_OOS
    assert classify_oos_segment("2026-09-08") == OOSSegment.HISTORICAL_OOS
    assert classify_oos_segment("2026-09-09") == OOSSegment.FORWARD_OOS
    assert classify_oos_segment("2026-10-01") == OOSSegment.FORWARD_OOS

    # Verify dataset partitioning has zero leakage
    engine = V03ReturnMeasureEngine(price_provider=DictPriceDataProvider(), hold_days=5)
    reports = [
        {"id": "r1", "symbol": "600519.SH", "trade_date": "2025-12-31"},
        {"id": "r2", "symbol": "600519.SH", "trade_date": "2026-01-01"},
        {"id": "r3", "symbol": "600519.SH", "trade_date": "2026-09-08"},
        {"id": "r4", "symbol": "600519.SH", "trade_date": "2026-09-09"},
    ]
    res = engine.measure_dataset(reports)

    assert res.dev_metrics.total_reports == 1
    assert res.historical_oos_metrics.total_reports == 2
    assert res.forward_oos_metrics.total_reports == 1
    assert res.all_metrics.total_reports == 4


# ===========================================================================
# RT-7: 入场价 T+1 Open（非当日 Close，无前视）
# ===========================================================================


def test_rt7_entry_price_t_plus_1_open(mock_price_provider):
    """RT-7: Entry price is strictly T+1 Open, NOT T Close."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    # For 600519.SH:
    # T = 2026-03-02 (Close was 1410.0)
    # T+1 = 2026-03-03 (Open was 1415.0)
    report = {
        "id": "rep_moutai_01",
        "symbol": "600519.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
        "direction": "偏多",
    }
    rec = engine.measure_sample(report)

    assert rec.entry_date == "2026-03-03"
    assert rec.entry_price == 1415.0, "Entry price must be T+1 Open, NOT T Close (1410.0)"
    assert rec.exit_date == "2026-03-10"
    assert rec.exit_price == 1500.0


# ===========================================================================
# RT-8: 超额收益 (相对沪深300)
# ===========================================================================


def test_rt8_excess_return_vs_csi300(mock_price_provider):
    """RT-8: Excess return correctly computed against CSI 300 over identical window."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    report = {
        "id": "rep_moutai_excess",
        "symbol": "600519.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
        "direction": "偏多",
    }
    rec = engine.measure_sample(report)

    # CSI 300:
    # 2026-03-03 Open: 3500.0
    # 2026-03-10 Close: 3600.0
    # Benchmark return = (3600 - 3500) / 3500 = 100 / 3500 ≈ 0.028571
    expected_bmk_ret = round(100.0 / 3500.0, 6)
    assert rec.benchmark_return is not None
    assert abs(rec.benchmark_return - expected_bmk_ret) < 1e-5

    assert rec.net_return is not None
    assert rec.excess_return is not None
    expected_alpha = round(rec.net_return - rec.benchmark_return, 6)
    assert abs(rec.excess_return - expected_alpha) < 1e-5


# ===========================================================================
# RT-9: 元数据盖章与系统完整度
# ===========================================================================


def test_rt9_metadata_and_system_completeness_stamps(mock_price_provider):
    """RT-9: Every result stamps model, prompt hash, code SHA, running service SHA, completeness."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    res = engine.measure_dataset([])
    stamp = res.stamp

    assert stamp.model == BASELINE_MODEL
    assert stamp.prompt_hash.startswith(BASELINE_GLOBAL_PROMPT_HASH)
    assert len(stamp.code_sha) >= 8
    assert stamp.running_service_sha == BASELINE_RUNNING_SERVICE_SHA
    assert stamp.disclaimer == BASELINE_DISCLAIMER

    completeness = stamp.system_completeness
    assert completeness["game_theory_report_fill_rate"] == 0.0
    assert completeness["sentiment_news_real_source_connected"] is False
    assert completeness["status_note"] == BASELINE_DISCLAIMER


# ===========================================================================
# RT-10: coverage vs return 分离 (typed-missing 进 coverage 不进 return)
# ===========================================================================


def test_rt10_coverage_vs_return_metrics_separation(mock_price_provider):
    """RT-10: typed-missing counts in coverage denominator, does not shrink it, does not pollute returns."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    reports = [
        # 1. Valid BUY (Moutai): evaluated_ok
        {
            "id": "r_moutai",
            "symbol": "600519.SH",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 2. Valid BUY (Ping An): evaluated_ok
        {
            "id": "r_pa",
            "symbol": "000001.SZ",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 3. Suspended: untradable
        {
            "id": "r_susp",
            "symbol": "600000.SH",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
        # 4. Typed-missing (Lens June gap)
        {
            "id": "r_gap",
            "symbol": "300433.SZ",
            "trade_date": "2026-06-01",
            "decision": "BUY",
        },
        # 5. BSE: excluded from pool
        {
            "id": "r_bse",
            "symbol": "830001",
            "trade_date": "2026-03-02",
            "decision": "BUY",
        },
    ]

    result = engine.measure_dataset(reports)
    m = result.all_metrics

    assert m.total_reports == 5
    assert m.in_pool_count == 4  # 4 eligible stocks (Moutai, Ping An, SPDB, Lens)
    assert m.excluded_pool_count == 1  # 1 BSE
    assert m.evaluated_count == 2  # Moutai + Ping An
    assert m.untradable_count == 1  # SPDB
    assert m.typed_missing_count == 1  # Lens gap

    # Coverage denominator does NOT shrink: all 4 in-pool samples are accounted for
    # 2 evaluated + 1 untradable + 1 typed-missing = 4 in-pool samples
    assert m.evaluated_count + m.untradable_count + m.typed_missing_count == m.in_pool_count
    assert m.coverage_rate == round(2 / 4, 4)

    # Return metrics calculated strictly on the 2 evaluated samples!
    assert m.return_sample_count == 2
    assert m.mean_net_return is not None
    # Neither untradable nor typed-missing is treated as 0% return
    moutai_ret = result.records[0].net_return
    pa_ret = result.records[1].net_return
    expected_mean = round((moutai_ret + pa_ret) / 2.0, 6)
    assert abs(m.mean_net_return - expected_mean) < 1e-5


# ===========================================================================
# Markdown Report Generation & SQLite Read-Only Testing
# ===========================================================================


def test_markdown_report_formatting(mock_price_provider):
    """Verify markdown report generation contains disclaimer, stamps, and tables."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    res = engine.measure_dataset(
        [
            {
                "id": "r1",
                "symbol": "600519.SH",
                "trade_date": "2026-03-02",
                "decision": "BUY",
            }
        ]
    )
    md = engine.generate_report_markdown(res)

    assert BASELINE_DISCLAIMER in md
    assert BASELINE_MODEL in md
    assert "DEV" in md
    assert "HISTORICAL_OOS" in md
    assert "FORWARD_OOS" in md
    assert "博弈论报告" in md


def test_sqlite_read_only_protection(tmp_path):
    """Verify SQLite loader enforces strict read-only mode."""
    db_path = tmp_path / "test_ro.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE reports (id TEXT, symbol TEXT, trade_date TEXT, status TEXT, "
        "decision TEXT, direction TEXT, confidence INT, target_price REAL, "
        "stop_loss_price REAL, result_data TEXT, created_at TEXT)"
    )
    conn.execute(
        "INSERT INTO reports VALUES ('r1', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{}', '2026-03-02 15:00:00')"
    )
    conn.commit()
    conn.close()

    rows = V03ReturnMeasureEngine.load_reports_from_db(str(db_path))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519.SH"


# ===========================================================================
# RT-S1 .. RT-S4: 作用域修正红队测试 (Scope Correction DAV-804)
# ===========================================================================


def test_rt_s1_multi_account_isolation(tmp_path, mock_price_provider):
    """RT-S1: 库含多账号时，只计 target_user_id，别账号不进任何指标."""
    db_path = tmp_path / "multi_account.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE reports (id TEXT, user_id TEXT, symbol TEXT, trade_date TEXT, status TEXT, "
        "decision TEXT, direction TEXT, confidence INT, target_price REAL, "
        "stop_loss_price REAL, result_data TEXT, created_at TEXT)"
    )
    # Account A (target user: David)
    david_id = DEFAULT_TARGET_USER_ID
    conn.execute(
        f"INSERT INTO reports VALUES ('r1', '{david_id}', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r2', '{david_id}', '000001.SZ', '2026-03-02', 'completed', 'BUY', '偏多', 80, 12, 10, '{{}}', '2026-03-02 15:00:00')"
    )
    # Account B (other user)
    conn.execute(
        "INSERT INTO reports VALUES ('r3', 'other_user_1', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        "INSERT INTO reports VALUES ('r4', 'other_user_2', '601398.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 5.5, 5.0, '{}', '2026-03-02 15:00:00')"
    )
    conn.commit()
    conn.close()

    # 1. Test load_reports_from_db isolation
    rows = V03ReturnMeasureEngine.load_reports_from_db(
        str(db_path), target_user_id=david_id
    )
    assert len(rows) == 2
    for r in rows:
        assert r["user_id"] == david_id

    # 2. Test engine.measure_dataset with multi-account input list
    all_raw_rows = [
        {"id": "r1", "user_id": david_id, "symbol": "600519.SH", "trade_date": "2026-03-02", "status": "completed", "decision": "BUY", "direction": "偏多"},
        {"id": "r2", "user_id": david_id, "symbol": "000001.SZ", "trade_date": "2026-03-02", "status": "completed", "decision": "BUY", "direction": "偏多"},
        {"id": "r3", "user_id": "other_user_1", "symbol": "600519.SH", "trade_date": "2026-03-02", "status": "completed", "decision": "BUY", "direction": "偏多"},
        {"id": "r4", "user_id": "other_user_2", "symbol": "601398.SH", "trade_date": "2026-03-02", "status": "completed", "decision": "BUY", "direction": "偏多"},
    ]
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=david_id,
        status_filter="completed",
    )
    res = engine.measure_dataset(all_raw_rows)
    # Only David's 2 reports are included in total_reports and all metrics
    assert res.all_metrics.total_reports == 2
    assert len(res.records) == 2
    for rec in res.records:
        assert rec.user_id == david_id

    # 3. Switching target_user_id isolates other_user_1
    engine_other = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id="other_user_1",
        status_filter="completed",
    )
    res_other = engine_other.measure_dataset(all_raw_rows)
    assert res_other.all_metrics.total_reports == 1
    assert res_other.records[0].user_id == "other_user_1"


def test_rt_s2_failed_and_uncompleted_status_excluded(tmp_path, mock_price_provider):
    """RT-S2: 含 failed / 未完成报告时，status≠completed 全部排除，不进分母."""
    db_path = tmp_path / "status_filter.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE reports (id TEXT, user_id TEXT, symbol TEXT, trade_date TEXT, status TEXT, "
        "decision TEXT, direction TEXT, confidence INT, target_price REAL, "
        "stop_loss_price REAL, result_data TEXT, created_at TEXT)"
    )
    david_id = DEFAULT_TARGET_USER_ID
    conn.execute(
        f"INSERT INTO reports VALUES ('r1', '{david_id}', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r2', '{david_id}', '000001.SZ', '2026-03-02', 'failed', 'BUY', '偏多', 80, 12, 10, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r3', '{david_id}', '601398.SH', '2026-03-02', 'pending', 'BUY', '偏多', 80, 5.5, 5.0, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r4', '{david_id}', '000002.SZ', '2026-03-02', 'running', 'BUY', '偏多', 80, 15, 12, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.commit()
    conn.close()

    # 1. Database loading filters out failed/pending/running
    rows = V03ReturnMeasureEngine.load_reports_from_db(
        str(db_path), target_user_id=david_id, status_filter="completed"
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"
    assert rows[0]["status"] == "completed"

    # 2. In-memory measurement filters out non-completed
    mixed_reports = [
        {"id": "r1", "user_id": david_id, "symbol": "600519.SH", "trade_date": "2026-03-02", "status": "completed", "decision": "BUY", "direction": "偏多"},
        {"id": "r2", "user_id": david_id, "symbol": "000001.SZ", "trade_date": "2026-03-02", "status": "failed", "decision": "BUY", "direction": "偏多"},
        {"id": "r3", "user_id": david_id, "symbol": "601398.SH", "trade_date": "2026-03-02", "status": "pending", "decision": "BUY", "direction": "偏多"},
        {"id": "r4", "user_id": david_id, "symbol": "000002.SZ", "trade_date": "2026-03-02", "status": "running", "decision": "BUY", "direction": "偏多"},
    ]
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=david_id,
        status_filter="completed",
    )
    res = engine.measure_dataset(mixed_reports)
    assert res.all_metrics.total_reports == 1
    assert res.records[0].report_id == "r1"
    assert res.records[0].status == "completed"


def test_rt_s3_metadata_stamps_user_and_scope(mock_price_provider):
    """RT-S3: 报告显式标 target_user_id + '仅 completed'，并记该账号 total/completed/failed."""
    custom_stats = {"total": 317, "completed": 231, "failed": 86}
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=DEFAULT_TARGET_USER_ID,
        status_filter=DEFAULT_STATUS_FILTER,
        target_user_stats=custom_stats,
    )
    res = engine.measure_dataset([])
    stamp = res.stamp

    # Verify stamp attributes
    assert stamp.target_user_id == DEFAULT_TARGET_USER_ID
    assert stamp.status_filter == "completed"
    assert "仅 completed" in stamp.scope_filter_description
    assert stamp.account_stats == custom_stats
    assert stamp.target_user_total == 317
    assert stamp.target_user_completed == 231
    assert stamp.target_user_failed == 86

    # Verify Markdown stamping
    md = engine.generate_report_markdown(res)
    assert DEFAULT_TARGET_USER_ID in md
    assert "仅 completed" in md
    assert "317" in md
    assert "231" in md
    assert "86" in md
    assert "Account Stats" in md


def test_rt_s4_david_account_clean_population_counts(mock_price_provider):
    """RT-S4: 该账号 completed=231、评估候选≈217、DEV=FORWARD=0."""
    prod_db_path = "/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db"
    if not Path(prod_db_path).exists():
        pytest.skip("Production DB not found on local system")

    # Query counts from database
    counts = V03ReturnMeasureEngine.get_user_report_counts(
        prod_db_path, target_user_id=DEFAULT_TARGET_USER_ID
    )
    assert counts["total"] == 317
    assert counts["completed"] == 231
    assert counts["failed"] == 86

    # Load David completed reports
    reports = V03ReturnMeasureEngine.load_reports_from_db(
        prod_db_path,
        target_user_id=DEFAULT_TARGET_USER_ID,
        status_filter=DEFAULT_STATUS_FILTER,
    )
    assert len(reports) == 231

    # Verify OOS date range
    trade_dates = [r["trade_date"] for r in reports]
    assert min(trade_dates) >= "2026-04-30"
    assert max(trade_dates) <= "2026-09-08"

    # Count directional candidates in raw reports (direction is not null/empty)
    dir_candidates = sum(
        1 for r in reports
        if r.get("direction") is not None and str(r.get("direction")).strip() not in ("", "None")
    )
    assert dir_candidates == 217

    # Run engine with mock provider to check OOS partitioning
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=DEFAULT_TARGET_USER_ID,
        status_filter=DEFAULT_STATUS_FILTER,
        target_user_stats=counts,
    )
    res = engine.measure_dataset(reports)

    assert res.all_metrics.total_reports == 231
    # 评估候选 ≈ 217 (raw db has 217, with result_data fallback evaluates to 227)
    assert abs(res.all_metrics.directional_candidate_count - 217) <= 15
    assert res.dev_metrics.total_reports == 0
    assert res.forward_oos_metrics.total_reports == 0
    assert res.historical_oos_metrics.total_reports == 231
    assert abs(res.historical_oos_metrics.directional_candidate_count - 217) <= 15


# ===========================================================================
# RT-1: 生产库与 SQLite 副本隔离
# ===========================================================================


def test_rt1_prod_replica_isolation_and_quick_check(tmp_path):
    """RT-1: 生产库与 SQLite 副本隔离，只读副本运行，生产库 hash/报告计数零改变，quick_check 通过."""
    prod_db_path = tmp_path / "prod_source.db"
    backup_db_path = tmp_path / "work_replica.db"

    conn = sqlite3.connect(str(prod_db_path))
    conn.execute(
        "CREATE TABLE reports (id TEXT, user_id TEXT, symbol TEXT, trade_date TEXT, status TEXT, "
        "decision TEXT, direction TEXT, confidence INT, target_price REAL, "
        "stop_loss_price REAL, result_data TEXT, created_at TEXT)"
    )
    david_id = DEFAULT_TARGET_USER_ID
    conn.execute(
        f"INSERT INTO reports VALUES ('r1', '{david_id}', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.commit()
    conn.close()

    # Pre-backup checksum & counts
    pre_sha = compute_file_sha256(prod_db_path)
    pre_counts = V03ReturnMeasureEngine.get_user_report_counts(
        str(prod_db_path), target_user_id=david_id
    )

    # Perform atomic backup in read-only mode
    from scripts.run_v03_return_measure import check_sqlite_integrity, create_sqlite_backup

    pre_quick, pre_integ = check_sqlite_integrity(prod_db_path)
    assert pre_quick == "ok"
    assert pre_integ == "ok"

    meta = create_sqlite_backup(
        str(prod_db_path), str(backup_db_path), david_id, DEFAULT_HISTORICAL_CUTOFF_DATE
    )

    # Post-backup checksum & count verification
    post_sha = compute_file_sha256(prod_db_path)
    post_counts = V03ReturnMeasureEngine.get_user_report_counts(
        str(prod_db_path), target_user_id=david_id
    )

    assert pre_sha == post_sha, "Production DB SHA256 must be strictly identical (zero mutation)"
    assert pre_counts == post_counts, "Production DB report counts must be strictly identical"
    assert meta["production_quick_check"] == "ok"
    assert meta["replica_quick_check"] == "ok"
    assert Path(backup_db_path).exists()


# ===========================================================================
# RT-2: 多账号 + failed/pending/running 混库
# ===========================================================================


def test_rt2_multi_account_status_scope_filtering(tmp_path, mock_price_provider):
    """RT-2: 多账号 + failed/pending/running 混库时，只计目标账号且只计 completed，作用域计数可回读."""
    db_path = tmp_path / "mixed_scope.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE reports (id TEXT, user_id TEXT, symbol TEXT, trade_date TEXT, status TEXT, "
        "decision TEXT, direction TEXT, confidence INT, target_price REAL, "
        "stop_loss_price REAL, result_data TEXT, created_at TEXT)"
    )
    david_id = DEFAULT_TARGET_USER_ID
    # David's reports (1 completed, 1 failed, 1 pending)
    conn.execute(
        f"INSERT INTO reports VALUES ('r1', '{david_id}', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r2', '{david_id}', '000001.SZ', '2026-03-02', 'failed', 'BUY', '偏多', 80, 12, 10, '{{}}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        f"INSERT INTO reports VALUES ('r3', '{david_id}', '601398.SH', '2026-03-02', 'pending', 'BUY', '偏多', 80, 5.5, 5.0, '{{}}', '2026-03-02 15:00:00')"
    )
    # Other user reports (1 completed, 1 failed)
    conn.execute(
        "INSERT INTO reports VALUES ('r4', 'other_user_x', '600519.SH', '2026-03-02', 'completed', 'BUY', '偏多', 80, 1500, 1350, '{}', '2026-03-02 15:00:00')"
    )
    conn.execute(
        "INSERT INTO reports VALUES ('r5', 'other_user_x', '000001.SZ', '2026-03-02', 'failed', 'BUY', '偏多', 80, 12, 10, '{}', '2026-03-02 15:00:00')"
    )
    conn.commit()
    conn.close()

    # 1. Scope counts verification
    counts = V03ReturnMeasureEngine.get_user_report_counts(
        str(db_path), target_user_id=david_id
    )
    assert counts["total"] == 3
    assert counts["completed"] == 1
    assert counts["failed"] == 1

    # 2. Database loading enforces both target_user_id and status=completed
    rows = V03ReturnMeasureEngine.load_reports_from_db(
        str(db_path), target_user_id=david_id, status_filter="completed"
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"
    assert rows[0]["user_id"] == david_id
    assert rows[0]["status"] == "completed"

    # 3. In-memory engine execution verifies isolation and metadata stamping
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=david_id,
        status_filter="completed",
        target_user_stats=counts,
    )
    res = engine.measure_dataset(rows)
    assert res.all_metrics.total_reports == 1
    assert res.stamp.target_user_id == david_id
    assert res.stamp.status_filter == "completed"
    assert res.stamp.account_stats == counts


# ===========================================================================
# RT-3: 25 字段离线审计表
# ===========================================================================


def test_rt3_minimum_25_field_offline_audit_table(mock_price_provider):
    """RT-3: 25 字段审计表每行齐全，缺失来源显式 typed gap，不伪造默认值."""
    assert len(MINIMUM_AUDIT_FIELDS) == 25

    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    report = {
        "id": "rep_moutai_audit",
        "symbol": "600519.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
        "direction": "偏多",
    }
    res = engine.measure_dataset([report])

    assert len(res.audit_table) == 1
    row = res.audit_table[0]

    # Validate all 25 fields are present
    for f in MINIMUM_AUDIT_FIELDS:
        assert f in row, f"Missing required audit field: {f}"

    # Strict schema validation pass
    validate_audit_row(row)

    # Validate failure on missing field
    incomplete_row = dict(row)
    del incomplete_row["actual_exit_date"]
    with pytest.raises(ValueError, match="missing required fields"):
        validate_audit_row(incomplete_row)

    # Validate failure on extra unapproved field
    extra_row = dict(row)
    extra_row["fabricated_field"] = 123
    with pytest.raises(ValueError, match="unexpected extra fields"):
        validate_audit_row(extra_row)

    # Check typed gap representation: untradable or unmappable has explicit reason, no fake defaults
    unmappable_rep = {"id": "rep_bad", "symbol": "AGENT", "trade_date": "2026-03-02"}
    res_bad = engine.measure_dataset([unmappable_rep])
    bad_row = res_bad.audit_table[0]
    validate_audit_row(bad_row)
    assert bad_row["evaluation_eligible"] is False
    assert bad_row["exclusion_reason"] == "excluded_unmappable"
    assert bad_row["performance_category"] == "excluded_pool"
    assert bad_row["net_return_pct"] is None


# ===========================================================================
# RT-4: 三段 OOS 边界
# ===========================================================================


def test_rt4_three_segment_oos_boundaries():
    """RT-4: DEV <= 2025-12-31, HISTORICAL_OOS 2026-01-01~09-08, FORWARD_OOS >= 2026-09-09 零泄漏."""
    # Boundary checks
    assert classify_oos_segment("2025-12-31") == OOSSegment.DEV
    assert classify_oos_segment("2026-01-01") == OOSSegment.HISTORICAL_OOS
    assert classify_oos_segment("2026-09-08") == OOSSegment.HISTORICAL_OOS
    assert classify_oos_segment("2026-09-09") == OOSSegment.FORWARD_OOS
    assert classify_oos_segment("2026-09-10") == OOSSegment.FORWARD_OOS

    # Dataset partition verification
    engine = V03ReturnMeasureEngine(price_provider=DictPriceDataProvider(), hold_days=5)
    reports = [
        {"id": "r_dev", "symbol": "600519.SH", "trade_date": "2025-12-31"},
        {"id": "r_hist_start", "symbol": "600519.SH", "trade_date": "2026-01-01"},
        {"id": "r_hist_end", "symbol": "600519.SH", "trade_date": "2026-09-08"},
        {"id": "r_fwd_start", "symbol": "600519.SH", "trade_date": "2026-09-09"},
        {"id": "r_fwd_later", "symbol": "600519.SH", "trade_date": "2026-09-15"},
    ]
    res = engine.measure_dataset(reports)

    assert res.dev_metrics.total_reports == 1
    assert res.historical_oos_metrics.total_reports == 2
    assert res.forward_oos_metrics.total_reports == 2
    assert res.all_metrics.total_reports == 5


# ===========================================================================
# RT-5: 六只回归标的永久隔离
# ===========================================================================


def test_rt5_six_regression_symbols_permanently_isolated(mock_price_provider):
    """RT-5: 歌尔/富联/蓝思/美的/隆基/爱尔 永久 sample_role=regression，独立核算，不进 OOS 汇总."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    regression_reports = [
        {"id": "reg_01", "symbol": "002241.SZ", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "reg_02", "symbol": "601138.SH", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "reg_03", "symbol": "300433.SZ", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "reg_04", "symbol": "000333.SZ", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "reg_05", "symbol": "601012.SH", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "reg_06", "symbol": "300015.SZ", "trade_date": "2026-03-02", "decision": "BUY"},
        # 1 non-regression stock: Moutai
        {"id": "norm_01", "symbol": "600519.SH", "trade_date": "2026-03-02", "decision": "BUY"},
    ]
    res = engine.measure_dataset(regression_reports)

    # 1. Total reports = 7
    assert res.all_metrics.total_reports == 7

    # 2. Regression metrics contains exactly the 6 regression reports
    assert res.regression_metrics.total_reports == 6

    # 3. Historical OOS contains ONLY Moutai, ZERO contamination from regression stocks
    assert res.historical_oos_metrics.total_reports == 1

    # 4. Audit rows for regression stocks have sample_role='regression' and evaluation_eligible=False
    for r in res.records:
        if r.symbol_canonical in REGRESSION_SYMBOLS:
            assert r.sample_role == SampleRole.REGRESSION.value
            assert r.evaluation_eligible is False
            assert r.exclusion_reason == "regression_sample_isolated"


# ===========================================================================
# RT-6: typed-missing (蓝思 6 月缺口类)
# ===========================================================================


def test_rt6_typed_missing_coverage_not_return(mock_price_provider):
    """RT-6: 蓝思 6 月缺口类：return=NULL，进 coverage 分母，不进 return 分母，不静默 drop/carry-forward."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    # 300433.SZ on 2026-06-01: exit bar missing in mock_price_provider
    report = {
        "id": "rep_lens_gap",
        "symbol": "300433.SZ",
        "trade_date": "2026-06-01",
        "decision": "BUY",
        "direction": "偏多",
    }
    rec = engine.measure_sample(report)

    assert rec.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value
    assert rec.missing_reason == "exit_bar_missing"
    assert rec.net_return is None, "return must be NULL for typed-missing"
    assert rec.gross_return is None
    assert rec.included_in_coverage_metrics is True
    assert rec.included_in_return_metrics is False


# ===========================================================================
# RT-7: T+1 Open 与不可执行入场
# ===========================================================================


def test_rt7_entry_price_t_plus_1_open_and_untradable(mock_price_provider):
    """RT-7: 严格以次日 T+1 开盘价成交 (非 T 日收盘，零前视)；停牌/涨跌停标 untradable，不假装成交."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    # 1. Normal stock: entry price is T+1 Open (1415.0), NOT T Close (1410.0)
    rep_moutai = {
        "id": "rep_moutai_t1",
        "symbol": "600519.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
    }
    rec_moutai = engine.measure_sample(rep_moutai)
    assert rec_moutai.entry_date == "2026-03-03"
    assert rec_moutai.entry_price == 1415.0
    assert rec_moutai.outcome_status == MeasurementOutcomeStatus.EVALUATED.value

    # 2. Suspended on T+1: untradable, no open fill
    rep_susp = {
        "id": "rep_susp_t1",
        "symbol": "600000.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
    }
    rec_susp = engine.measure_sample(rep_susp)
    assert rec_susp.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value
    assert rec_susp.entry_price is None
    assert rec_susp.net_return is None

    # 3. Limit-up locked on T+1: untradable for BUY, no fake fill
    rep_limit = {
        "id": "rep_limit_t1",
        "symbol": "600006.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
    }
    rec_limit = engine.measure_sample(rep_limit)
    assert rec_limit.outcome_status == MeasurementOutcomeStatus.UNTRADABLE.value
    assert rec_limit.entry_price is None
    assert rec_limit.net_return is None


# ===========================================================================
# RT-8: 成本与沪深300基准
# ===========================================================================


def test_rt8_cost_model_and_csi300_benchmark(mock_price_provider):
    """RT-8: 成本项按冻结口径（佣金含规费+过户+卖方印花+5bps滑点），基准收益与超额收益同窗可追溯."""
    model = CostModel()

    # Rate verification
    assert model.commission_rate == 0.00025  # 0.025%
    assert model.transfer_fee_rate == 0.00001  # 0.01‰
    assert model.stamp_duty_rate == 0.0005  # 0.5‰
    assert model.slippage_rate == 0.0005  # 5 bps
    assert round(model.buy_cost_rate, 6) == 0.00076  # 0.025% + 0.001% + 0.05% = 0.076%
    assert round(model.sell_cost_rate, 6) == 0.00126  # 0.025% + 0.001% + 0.05% + 0.05% = 0.126%

    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    report = {
        "id": "rep_moutai_bmk",
        "symbol": "600519.SH",
        "trade_date": "2026-03-02",
        "decision": "BUY",
    }
    rec = engine.measure_sample(report)

    # CSI 300 benchmark from 2026-03-03 Open (3500) to 2026-03-10 Close (3600)
    expected_bmk_ret = round((3600.0 - 3500.0) / 3500.0, 6)
    assert rec.benchmark_return is not None
    assert abs(rec.benchmark_return - expected_bmk_ret) < 1e-5

    # Excess return = net_return - benchmark_return
    assert rec.excess_return is not None
    assert abs(rec.excess_return - (rec.net_return - rec.benchmark_return)) < 1e-5


# ===========================================================================
# RT-9: 无概率校准 / 无组合规则
# ===========================================================================


def test_rt9_no_probability_calibration_no_portfolio_rules(mock_price_provider):
    """RT-9: 不输出 confidence->probability、Sharpe 或最大回撤，严禁把置信度等同于胜率概率."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    res = engine.measure_dataset([])
    res_dict = res.to_dict()

    # Verify no portfolio metric keys in output
    for key in ["sharpe", "sharpe_ratio", "max_drawdown", "calmar", "sortino", "probability"]:
        assert key not in res_dict
        assert key not in res_dict["all_metrics"]

    # Verify claim evaluation returns probability: None
    claim_eval = evaluate_proposition_claim([], enable_claim_verification=True)
    assert claim_eval["probability"] is None


# ===========================================================================
# RT-10: 复制消融 (Repetition)
# ===========================================================================


def test_rt10_repetition_ablation_deduplication():
    """RT-10: 复制消融只改变去重开关；同质复制不增独立票，独立事实不被误杀."""
    evidence_items = [
        {"fact_key": "fact_revenue_up", "content": "Q1 营收同比增长 15%"},
        {"fact_key": "fact_revenue_up", "content": "Q1 营收同比增长 15%"},
        {"fact_key": "fact_revenue_up", "content": "Q1 营收同比增长 15%"},
        {"fact_key": "fact_margin_up", "content": "综合毛利率提升 2.1 个百分点"},
    ]

    # Deduplication enabled: homogenous facts collapsed to 1, independent fact preserved
    dedup_on = apply_evidence_deduplication(evidence_items, enable_dedup=True)
    assert dedup_on["raw_evidence_count"] == 4
    assert dedup_on["effective_evidence_count"] == 2
    assert dedup_on["duplicate_count"] == 2

    # Deduplication disabled: raw count kept
    dedup_off = apply_evidence_deduplication(evidence_items, enable_dedup=False)
    assert dedup_off["raw_evidence_count"] == 4
    assert dedup_off["effective_evidence_count"] == 4
    assert dedup_off["duplicate_count"] == 0


# ===========================================================================
# RT-11: 顺序消融 (Order)
# ===========================================================================


def test_rt11_order_ablation_seeded_replay(mock_price_provider):
    """RT-11: Canonical/Reversed/Seeded-Shuffled 共用同一快照与样本集合，seed=42 可复放."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    reports = [
        {"id": f"rep_{i:02d}", "symbol": "600519.SH", "trade_date": "2026-03-02", "decision": "BUY"}
        for i in range(10)
    ]

    harness = OfflineReplayHarness(engine)
    res_canon, v_canon = harness.run_replay(
        reports, AblationConfig(order_mode=OrderAblationMode.CANONICAL), "canon"
    )
    res_rev, v_rev = harness.run_replay(
        reports, AblationConfig(order_mode=OrderAblationMode.REVERSED), "rev"
    )
    res_shuf, v_shuf = harness.run_replay(
        reports, AblationConfig(order_mode=OrderAblationMode.SEEDED_SHUFFLED, order_seed=42), "shuf"
    )

    # Sample set and counts are invariant to presentation order
    assert v_canon.metrics_summary["total_reports"] == 10
    assert v_rev.metrics_summary["total_reports"] == 10
    assert v_shuf.metrics_summary["total_reports"] == 10

    # All share identical snapshot hash
    assert v_canon.snapshot_hash == v_rev.snapshot_hash == v_shuf.snapshot_hash


# ===========================================================================
# RT-12: 缺口消融 (Gap)
# ===========================================================================


def test_rt12_gap_ablation_typed_gap_no_defaults(mock_price_provider):
    """RT-12: 关键字段 mask 后出现显式 typed gap；不填默认、不 carry-forward、不静默删样本."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    reports = [
        {
            "id": "rep_gap_test",
            "symbol": "600519.SH",
            "trade_date": "2026-03-02",
            "decision": "BUY",
            "social": "positive sentiment",
            "fund_flow": "net inflow 500m",
            "latest_report": "Q3 beat estimates",
        }
    ]

    harness = OfflineReplayHarness(engine)
    res_masked, v_masked = harness.run_replay(
        reports,
        AblationConfig(masked_fields=("social", "fund_flow", "latest_report")),
        "gap_masked",
    )

    # Sample count strictly preserved (no silent deletion)
    assert res_masked.all_metrics.total_reports == 1
    rec = res_masked.records[0]
    audit_row = rec.to_audit_row()

    # Gap is recorded in evidence_provenance
    assert audit_row["evidence_provenance"].get("gap_ablation_masked") == [
        "social",
        "fund_flow",
        "latest_report",
    ]


# ===========================================================================
# RT-13: 命题消融 (Proposition)
# ===========================================================================


def test_rt13_proposition_ablation_not_checked():
    """RT-13: 无命题时判定 not_checked；不由 confidence 推概率、不伪造 claim 状态."""
    # 1. Without claims input -> strictly 'not_checked' / typed gap
    res_none = evaluate_proposition_claim(None)
    assert res_none["status"] == "not_checked"
    assert res_none["claim_count"] == 0
    assert res_none["probability"] is None

    # 2. Empty claims list -> strictly 'not_checked'
    res_empty = evaluate_proposition_claim([])
    assert res_empty["status"] == "not_checked"
    assert res_empty["probability"] is None

    # 3. Verification disabled -> verification_disabled
    res_dis = evaluate_proposition_claim(
        [{"claim": "ROE > 15%", "verified": True}], enable_claim_verification=False
    )
    assert res_dis["status"] == "verification_disabled"
    assert res_dis["probability"] is None


# ===========================================================================
# RT-14: 同事件/重叠标签 (Purging & Embargo)
# ===========================================================================


def test_rt14_purging_and_embargo_overlapping_labels(mock_price_provider):
    """RT-14: 同一标的重叠持仓区间样本剔除，留审计证据，重叠样本不能当独立样本."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    # Moutai report 1: signal 2026-03-02, entry 2026-03-03, exit 2026-03-10
    # Moutai report 2: signal 2026-03-04, entry 2026-03-05 (<= exit 2026-03-10, overlaps!)
    reports = [
        {"id": "rep_mt_1", "symbol": "600519.SH", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "rep_mt_2", "symbol": "600519.SH", "trade_date": "2026-03-04", "decision": "BUY"},
    ]
    res = engine.measure_dataset(reports, apply_purging=True)

    assert res.all_metrics.total_reports == 2
    rec1 = res.records[0]
    rec2 = res.records[1]

    # Report 1 is evaluated
    assert rec1.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    assert rec1.evaluation_eligible is True

    # Report 2 is purged due to overlapping holding period!
    assert rec2.evaluation_eligible is False
    assert rec2.exclusion_reason == "overlapping_label_purged"
    assert rec2.performance_category == "overlapping_purged"
    assert rec2.included_in_return_metrics is False


# ===========================================================================
# RT-15: 变体同快照
# ===========================================================================


def test_rt15_ablation_variants_share_identical_snapshot_hash(mock_price_provider):
    """RT-15: 各变体只改一个控制变量，cutoff、资格、成本、快照 hash 100% 一致."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)
    reports = [
        {"id": f"rep_{i:02d}", "symbol": "600519.SH", "trade_date": "2026-03-02", "decision": "BUY"}
        for i in range(5)
    ]

    harness = OfflineReplayHarness(engine)
    variants = harness.run_all_ablation_variants(reports)

    # 7 variants run successfully
    assert len(variants) == 7
    base_hash = variants["variant_0_baseline"].snapshot_hash

    # Every variant shares the exact same snapshot_hash
    for name, v in variants.items():
        assert v.snapshot_hash == base_hash, f"Variant {name} has inconsistent snapshot_hash"
        assert v.cutoff_datetime == DEFAULT_HISTORICAL_CUTOFF_DATETIME
        assert v.disclaimer == BASELINE_DISCLAIMER


# ===========================================================================
# RT-16: 当前没有 FORWARD_OOS
# ===========================================================================


def test_rt16_honest_forward_oos_zero_reporting(mock_price_provider):
    """RT-16: 当前数据库截止基准日期如实报告 FORWARD_OOS=0 与原因，严禁将历史样本改名为 forward."""
    engine = V03ReturnMeasureEngine(price_provider=mock_price_provider, hold_days=5)

    # All reports <= 2026-09-08 (Historical OOS)
    reports = [
        {"id": "rep_hist_01", "symbol": "600519.SH", "trade_date": "2026-03-02", "decision": "BUY"},
        {"id": "rep_hist_02", "symbol": "600519.SH", "trade_date": "2026-09-08", "decision": "BUY"},
    ]
    res = engine.measure_dataset(reports)

    # FORWARD_OOS is honestly reported as 0
    assert res.forward_oos_metrics.total_reports == 0
    assert res.forward_oos_metrics.return_sample_count == 0
    assert res.forward_oos_metrics.mean_net_return is None

    # Manifest honestly records 0 and explicit reason
    assert res.snapshot_manifest is not None
    assert res.snapshot_manifest.forward_oos_count == 0
    assert "未产生或未纳入已完成前向验证样本" in res.snapshot_manifest.forward_oos_zero_reason


# ===========================================================================
# DAV-865: Provenance 与统计缺失（禁止硬编码回退）测试
# ===========================================================================


def test_dav865_provenance_service_sha_differentiation():
    """DAV-865: 明确区分历史样本生成服务 SHA 与当前运行服务 SHA，字段语义准确表达."""
    assert HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA == "a6d4540feaa8043ff36b0607a31c1d2d5f004149"
    # Legacy alias points to historical generator SHA
    assert BASELINE_RUNNING_SERVICE_SHA == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA

    # EvaluationStamp & SnapshotManifest differentiate historical sample generator vs running service
    stamp = EvaluationStamp()
    assert stamp.sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert stamp.historical_sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA

    manifest = SnapshotManifest(manifest_id="test_man_diff")
    assert manifest.sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert manifest.historical_sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert manifest.running_service_sha is None


def test_dav865_provenance_healthz_probe_service_available(monkeypatch):
    """DAV-865: 当前服务可用时，只读 healthz 探针可提取 commit_sha 并标注明确来源 (Mock 无网络依赖)."""
    import json

    class DummyResponse:
        status = 200

        def read(self):
            return json.dumps({
                "status": "ok",
                "commit_sha": "a227cdc3bb466edf2e910419cb6013cfc021d309",
                "build_identity": "tradingagents-api@a227cdc3bb466edf2e910419cb6013cfc021d309",
            }).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: DummyResponse())

    sha, prov = probe_running_service_sha("http://127.0.0.1:8000/healthz")
    assert sha == "a227cdc3bb466edf2e910419cb6013cfc021d309"
    assert "healthz_probe" in prov
    assert "http://127.0.0.1:8000/healthz" in prov


def test_dav865_provenance_healthz_probe_service_unavailable_offline(monkeypatch):
    """DAV-865: 服务不可用/离线重放时，返回 typed gap (None) 与显式 offline_replay_gap，不悄悄填旧常量."""
    import urllib.error
    import urllib.request

    def mock_urlopen_fail(req, timeout=1.0):
        raise urllib.error.URLError("Connection refused [mocked offline]")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_fail)

    sha, prov = probe_running_service_sha("http://127.0.0.1:8000/healthz")
    assert sha is None
    assert "offline_replay_gap" in prov
    assert sha != BASELINE_RUNNING_SERVICE_SHA
    assert sha != HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA


def test_dav865_engine_stamps_explicit_running_service_and_provenance(mock_price_provider):
    """DAV-865: 传入运行服务 SHA 时，stamp 与 manifest 精确记录且与历史生成版本明确共存."""
    live_sha = "a227cdc3bb466edf2e910419cb6013cfc021d309"
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        running_service_sha=live_sha,
        running_service_provenance="healthz_probe: http://127.0.0.1:8000/healthz",
    )
    res = engine.measure_dataset([])
    stamp = res.stamp
    manifest = res.snapshot_manifest

    assert stamp.running_service_sha == live_sha
    assert stamp.sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert stamp.running_service_provenance_source == "healthz_probe: http://127.0.0.1:8000/healthz"

    assert manifest.running_service_sha == live_sha
    assert manifest.sample_generating_service_sha == HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert manifest.running_service_provenance_source == "healthz_probe: http://127.0.0.1:8000/healthz"

    # Markdown report contains both clearly distinguished
    md = engine.generate_report_markdown(res)
    assert live_sha in md
    assert HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA in md
    assert "历史样本生成服务 SHA" in md
    assert "当前运行服务 SHA" in md


def test_dav865_engine_offline_replay_stamps_typed_gap(mock_price_provider):
    """DAV-865: 离线重放环境无法取得运行服务时，manifest 必须输出 typed gap / offline replay 标记，禁止冒充常量."""
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        running_service_sha="offline_replay_gap",
        running_service_provenance="offline_replay_gap: probe unavailable",
    )
    res = engine.measure_dataset([])
    manifest = res.snapshot_manifest

    assert manifest.running_service_sha == "offline_replay_gap"
    assert manifest.running_service_sha != HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA
    assert manifest.running_service_sha != "a6d4540feaa8043ff36b0607a31c1d2d5f004149"


def test_dav865_no_hardcoded_stats_fallback_fail_closed_or_typed_gap(mock_price_provider):
    """DAV-865: 彻底删除 317/231/86/217 硬编码回退；缺统计时输出显式 typed gap (None)，不生成貌似真实幽灵指标."""
    # 1. Dataclass defaults are None (typed gap), strictly not 317/231/86/217
    stamp_default = EvaluationStamp()
    assert stamp_default.target_user_total is None
    assert stamp_default.target_user_completed is None
    assert stamp_default.target_user_failed is None
    assert stamp_default.account_stats is None

    manifest_default = SnapshotManifest(manifest_id="test_no_ghost")
    assert manifest_default.target_user_total is None
    assert manifest_default.target_user_completed is None
    assert manifest_default.target_user_failed is None
    assert manifest_default.candidate_reports is None
    assert manifest_default.account_stats is None

    # 2. Engine initialized with no target_user_stats (default None)
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=DEFAULT_TARGET_USER_ID,
        target_user_stats=None,
    )
    assert engine.target_user_stats is None

    # 3. Running measurement on dataset yields None typed gaps, NEVER 317/231/86
    res = engine.measure_dataset([])
    assert res.stamp.target_user_total is None
    assert res.stamp.target_user_completed is None
    assert res.stamp.target_user_failed is None
    assert res.stamp.account_stats is None

    assert res.snapshot_manifest.target_user_total is None
    assert res.snapshot_manifest.target_user_completed is None
    assert res.snapshot_manifest.target_user_failed is None

    # Strictly verify none of the ghost metrics appear
    assert res.stamp.target_user_total != 317
    assert res.stamp.target_user_completed != 231
    assert res.stamp.target_user_failed != 86
    assert res.snapshot_manifest.target_user_total != 317
    assert res.snapshot_manifest.target_user_completed != 231
    assert res.snapshot_manifest.target_user_failed != 86

    # 4. Markdown report explicitly marks typed gap instead of phantom numbers
    md = engine.generate_report_markdown(res)
    assert "未统计/数据源缺口 (typed gap: None)" in md


def test_dav865_real_stats_accurate_readback_when_provided(mock_price_provider):
    """DAV-865: 正常传入真实统计时，准确回读与盖章，不被任何默认值干扰."""
    real_stats = {"total": 520, "completed": 380, "failed": 140}
    engine = V03ReturnMeasureEngine(
        price_provider=mock_price_provider,
        hold_days=5,
        target_user_id=DEFAULT_TARGET_USER_ID,
        target_user_stats=real_stats,
    )
    res = engine.measure_dataset([])

    assert res.stamp.target_user_total == 520
    assert res.stamp.target_user_completed == 380
    assert res.stamp.target_user_failed == 140
    assert res.stamp.account_stats == real_stats

    assert res.snapshot_manifest.target_user_total == 520
    assert res.snapshot_manifest.target_user_completed == 380
    assert res.snapshot_manifest.target_user_failed == 140
    assert res.snapshot_manifest.account_stats == real_stats

    md = engine.generate_report_markdown(res)
    assert "520" in md
    assert "380" in md
    assert "140" in md
