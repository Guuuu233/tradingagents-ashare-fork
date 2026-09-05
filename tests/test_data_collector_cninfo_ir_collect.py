"""Unit tests for DataCollector CNINFO IR/surveys integration (C-05 Slice 8 / DAV-661).

Enforces contracts:
1. _fetch_all(..., trade_date) calls get_cninfo_ir_surveys on the shared
   _registry.get("cn_akshare") provider instance, with symbol=ticker,
   start_date=(trade_date - lookback [LONG_DAYS=90]), end_date=norm_trade_date,
   cutoff=norm_trade_date.
   _fetch_all output results["cninfo_ir_surveys"] is always an envelope (has status/records),
   never a missing key.
2. When status=ok and records have canonical_event_id, event_coverage sees this primary
   source in clusters and evidences; collateral attachment preserves primary canonical_event_id
   without modifying it.
3. When provider_failure envelope occurs, event_coverage recall_status reports failure/gap,
   and must NOT be classified as confirmed_empty.
4. When records are empty (confirmed_empty or ok with 0 rows), coverage/context text
   strictly forbids containing '确认无公告'.
5. All tests mock get_cninfo_ir_surveys with zero real network calls to .cninfo.com.cn.
   Tests directly execute through _fetch_all.
6. Empty string in results must downgrade to provider_failure envelope, never encapsulated as ok empty table.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from tradingagents.dataflows.cninfo_disclosure import (
    CninfoDisclosureEnvelope,
    CninfoDisclosureRecord,
    STATUS_CONFIRMED_EMPTY,
    STATUS_OK,
    STATUS_PROVIDER_FAILURE,
    SOURCE_TYPE_ANNOUNCEMENT,
    SOURCE_TYPE_IR_SURVEY,
)
from tradingagents.dataflows.interface import _registry
from tradingagents.graph import data_collector
from tradingagents.graph.data_collector import DataCollector, _fetch_all


@pytest.fixture(autouse=True)
def clean_env_tokens(monkeypatch):
    """Ensure tests never read real tokens or hit real networks."""
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)


@pytest.fixture
def mock_sub_tasks():
    """Mock sub-tasks inside _fetch_all so external network calls are suppressed,
    while passing through cninfo calls to the mocked provider method.
    """
    mock_ind = MagicMock()
    mock_ind.get_industry_linkage.return_value = None
    cn_provider = _registry.get("cn_akshare")

    def mock_safe_dispatch(tool, payload):
        tool_str = str(tool).lower()
        # Pass through IR surveys calls to the mocked provider method
        if tool == getattr(cn_provider, "get_cninfo_ir_surveys", None) or "get_cninfo_ir_surveys" in tool_str or "cninfo_ir" in tool_str:
            try:
                return tool(**payload)
            except Exception as exc:
                return f"get_cninfo_ir_surveys 调用失败：{type(exc).__name__}: {exc}"
        # Keep announcements quiet so tests isolate IR behavior
        if tool == getattr(cn_provider, "get_cninfo_announcements", None) or "get_cninfo_announcements" in tool_str or "announcement" in tool_str:
            return CninfoDisclosureEnvelope(
                status=STATUS_CONFIRMED_EMPTY,
                records=[],
                source_type=SOURCE_TYPE_ANNOUNCEMENT,
            )
        return ""

    with patch.object(data_collector, "_safe", side_effect=mock_safe_dispatch), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 2), \
         patch.object(data_collector, "_DEFAULT_INDUSTRY_LINKAGE_PROVIDER", mock_ind):
        yield mock_ind


# ==============================================================================
# Contract 1: _fetch_all calls get_cninfo_ir_surveys with exact args and returns envelope
# ==============================================================================

def test_fetch_all_calls_get_cninfo_ir_surveys_with_exact_args(mock_sub_tasks):
    """Contract 1: _fetch_all calls get_cninfo_ir_surveys on shared cn_akshare provider
    with symbol, start_date (LONG_DAYS lookback), end_date, cutoff, and populates envelope."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年7月26日至8月21日投资者关系活动记录表",
        announced_at="2026-08-21 07:28:15",
        url="http://cninfo.com.cn/ir/1",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )
    mock_get = MagicMock(return_value=mock_env)

    with patch.object(cn_provider, "get_cninfo_ir_surveys", mock_get):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        # 1. Method called on shared provider
        assert mock_get.called
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("symbol") == "000001"
        assert kwargs.get("start_date") == "2026-05-27"  # 2026-08-25 - 90 days
        assert kwargs.get("end_date") == "2026-08-25"
        assert kwargs.get("cutoff") == "2026-08-25"

        # 2. results["cninfo_ir_surveys"] is the envelope
        assert "cninfo_ir_surveys" in res
        ir = res["cninfo_ir_surveys"]
        assert hasattr(ir, "status") and hasattr(ir, "records")
        assert ir.status == STATUS_OK
        assert len(ir.records) == 1
        assert ir.records[0].canonical_event_id == "cninfo:1225488095"
        assert ir.records[0].source_type == SOURCE_TYPE_IR_SURVEY


# ==============================================================================
# Contract 2: status=ok with canonical_event_id visible in event_coverage & preserved
# ==============================================================================

def test_fetch_all_normal_ir_record_canonical_event_id_in_event_coverage(mock_sub_tasks):
    """Contract 2: status=ok with canonical_event_id enters event_coverage cluster and evidences."""
    cn_provider = _registry.get("cn_akshare")

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年7月26日至8月21日投资者关系活动记录表",
        announced_at="2026-08-21 07:28:15",
        url="http://cninfo.com.cn/ir/1",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )
    mock_get = MagicMock(return_value=mock_env)

    with patch.object(cn_provider, "get_cninfo_ir_surveys", mock_get):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        event_cov = res.get("event_coverage", {})
        assert event_cov is not None
        clusters = event_cov.get("clusters", [])
        assert len(clusters) == 1

        matched_cluster = clusters[0]
        assert matched_cluster.get("canonical_event_id") == "cninfo:1225488095"
        assert "投资者关系" in matched_cluster.get("title", "")

        # Also verified in evidences
        evidences = matched_cluster.get("evidences", [])
        assert any(e.get("canonical_event_id") == "cninfo:1225488095" for e in evidences)


def test_fetch_all_collateral_does_not_overwrite_ir_canonical_event_id(mock_sub_tasks):
    """Contract 2: When collateral attaches to primary CNINFO evidence, primary canonical_event_id
    is strictly preserved and collateral maintains canonical_event_id=None."""
    cn_provider = _registry.get("cn_akshare")

    primary_rec = CninfoDisclosureRecord(
        symbol="000001",
        title="关于2026年半年度业绩预告的投资者关系活动记录表",
        announced_at="2026-08-21 07:28:15",
        url="http://cninfo.com.cn/ir/1",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[primary_rec],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    mock_forecast = [
        {
            "symbol": "000001.SZ",
            "ann_date": "2026-08-21",
            "source_type": "tushare_forecast",
            "collateral_id": "tushare:forecast:000001.SZ:20260821:20260630",
            "payload": {"type": "预增", "p_change_min": 15.0},
            "canonical_event_id": None,
        }
    ]

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=mock_env), \
         patch.object(cn_provider, "_fetch_tushare_forecast", return_value=(mock_forecast, None, None)), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        clusters = event_cov.get("clusters", [])
        assert len(clusters) == 1

        cluster = clusters[0]
        # Primary canonical_event_id is NOT overwritten
        assert cluster.get("canonical_event_id") == "cninfo:1225488095"

        # Collateral attached
        col_records = cluster.get("collateral_records", [])
        assert len(col_records) == 1
        assert col_records[0].get("collateral_id") == "tushare:forecast:000001.SZ:20260821:20260630"
        assert col_records[0].get("canonical_event_id") is None


# ==============================================================================
# Contract 3: provider_failure envelope -> coverage/recall_status failure/gap, not confirmed_empty
# ==============================================================================

def test_fetch_all_provider_failure_envelope_enters_gaps_not_confirmed_empty(mock_sub_tasks):
    """Contract 3: provider_failure envelope enters event_coverage suspected_gaps,
    recall_status is provider_failure, and is_confirmed_empty is strictly False."""
    cn_provider = _registry.get("cn_akshare")

    fail_env = CninfoDisclosureEnvelope(
        status=STATUS_PROVIDER_FAILURE,
        records=[],
        error="KeyError: 'announcementId'",
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=fail_env):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        # 1. results["cninfo_ir_surveys"] has provider_failure envelope
        assert "cninfo_ir_surveys" in res
        ir = res["cninfo_ir_surveys"]
        assert hasattr(ir, "status")
        assert ir.status == STATUS_PROVIDER_FAILURE
        assert ir.is_failure is True

        # 2. event_coverage recall_status is failure, not confirmed_empty
        event_cov = res.get("event_coverage", {})
        assert event_cov.get("recall_status") == "provider_failure"
        assert event_cov.get("has_gap") is True
        assert event_cov.get("is_confirmed_empty") is False

        # 3. Gap logged for cninfo_ir_survey
        suspected_gaps = event_cov.get("suspected_gaps", [])
        ir_gaps = [g for g in suspected_gaps if "cninfo_ir" in str(g.get("source", "")) or "ir" in str(g.get("source", "")).lower()]
        assert len(ir_gaps) >= 1
        assert ir_gaps[0].get("status") == "provider_failure"

        # 4. Text strictly forbids "确认无公告"
        cov_str = json.dumps(event_cov, ensure_ascii=False)
        assert "确认无公告" not in cov_str


# ==============================================================================
# Contract 4: Empty records (confirmed_empty or ok) strictly forbids "确认无公告"
# ==============================================================================

def test_fetch_all_confirmed_empty_records_forbids_confirmed_empty_notice(mock_sub_tasks):
    """Contract 4: confirmed_empty envelope output must never contain '确认无公告'."""
    cn_provider = _registry.get("cn_akshare")

    empty_env = CninfoDisclosureEnvelope(
        status=STATUS_CONFIRMED_EMPTY,
        records=[],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=empty_env):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        assert res["cninfo_ir_surveys"].status == STATUS_CONFIRMED_EMPTY
        assert len(res["cninfo_ir_surveys"].records) == 0

        cov_str = json.dumps(res.get("event_coverage", {}), ensure_ascii=False)
        assert "确认无公告" not in cov_str

        market_ctx_str = json.dumps(res.get("market_data_context", {}), ensure_ascii=False)
        assert "确认无公告" not in market_ctx_str


def test_fetch_all_ok_with_zero_records_forbids_confirmed_empty_notice(mock_sub_tasks):
    """Contract 4: status=ok with empty records must never contain '确认无公告'."""
    cn_provider = _registry.get("cn_akshare")

    ok_empty_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=ok_empty_env):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        assert res["cninfo_ir_surveys"].status == STATUS_OK
        assert len(res["cninfo_ir_surveys"].records) == 0

        cov_str = json.dumps(res.get("event_coverage", {}), ensure_ascii=False)
        assert "确认无公告" not in cov_str

        market_ctx_str = json.dumps(res.get("market_data_context", {}), ensure_ascii=False)
        assert "确认无公告" not in market_ctx_str


# ==============================================================================
# Contract 5 & 1 Fallback: Unhandled exception / timeout safely produces provider_failure envelope
# ==============================================================================

def test_fetch_all_unhandled_exception_safely_falls_back_to_provider_failure(mock_sub_tasks):
    """Contract 1 & 5: If get_cninfo_ir_surveys raises an unhandled exception,
    _fetch_all ensures results['cninfo_ir_surveys'] is a provider_failure envelope,
    not a raw error string or missing key."""
    cn_provider = _registry.get("cn_akshare")

    mock_get = MagicMock(side_effect=RuntimeError("remote gateway crashed"))

    with patch.object(cn_provider, "get_cninfo_ir_surveys", mock_get):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        assert "cninfo_ir_surveys" in res
        ir = res["cninfo_ir_surveys"]
        assert hasattr(ir, "status") and hasattr(ir, "records")
        assert ir.status == STATUS_PROVIDER_FAILURE
        assert ir.records == []


def test_fetch_all_empty_string_downgrades_to_provider_failure_not_ok():
    """Contract 1 & Rule: 不要把空串封装成 ok 空表。超时/异常字符串降级为 provider_failure envelope."""
    # When results['cninfo_ir_surveys'] is an empty string, it must NOT be converted to status=ok
    def mock_safe_returns_empty(tool, payload):
        return ""

    mock_ind = MagicMock()
    mock_ind.get_industry_linkage.return_value = None

    with patch.object(data_collector, "_safe", side_effect=mock_safe_returns_empty), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 2), \
         patch.object(data_collector, "_DEFAULT_INDUSTRY_LINKAGE_PROVIDER", mock_ind):

        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_ind)

        assert "cninfo_ir_surveys" in res
        ir = res["cninfo_ir_surveys"]
        assert hasattr(ir, "status") and hasattr(ir, "records")
        # Strictly provider_failure, NEVER ok!
        assert ir.status == STATUS_PROVIDER_FAILURE
        assert ir.records == []


def test_fetch_all_timeout_string_downgrades_to_provider_failure(mock_sub_tasks):
    """Contract 1 & Rule: 超时字符串降级为 provider_failure envelope，不得静默缺键。"""
    cn_provider = _registry.get("cn_akshare")

    def mock_safe_timeout(tool, payload):
        tool_str = str(tool).lower()
        if "get_cninfo_ir_surveys" in tool_str or "cninfo_ir" in tool_str:
            return "cninfo_ir_surveys 数据拉取超时（>2s），本次分析跳过该数据源"
        if "get_cninfo_announcements" in tool_str or "announcement" in tool_str:
            return CninfoDisclosureEnvelope(
                status=STATUS_CONFIRMED_EMPTY,
                records=[],
                source_type=SOURCE_TYPE_ANNOUNCEMENT,
            )
        return ""

    with patch.object(data_collector, "_safe", side_effect=mock_safe_timeout):
        res = _fetch_all("000001", "2026-08-25", industry_provider=mock_sub_tasks)

        assert "cninfo_ir_surveys" in res
        ir = res["cninfo_ir_surveys"]
        assert hasattr(ir, "status") and hasattr(ir, "records")
        assert ir.status == STATUS_PROVIDER_FAILURE
        assert ir.records == []
        assert "超时" in (ir.error or "")


# ==============================================================================
# Integration: DataCollector.collect() end-to-end with CNINFO IR envelope
# ==============================================================================

def test_data_collector_collect_end_to_end_with_cninfo_ir(mock_sub_tasks):
    """End-to-end integration: DataCollector.collect() completes and returns cninfo_ir_surveys in context."""
    cn_provider = _registry.get("cn_akshare")

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年7月26日至8月21日投资者关系活动记录表",
        announced_at="2026-08-21 07:28:15",
        url="http://cninfo.com.cn/ir/1",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    collector = DataCollector(industry_linkage_provider=mock_sub_tasks)

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=mock_env):
        res = collector.collect("000001", "2026-08-25")

        assert "cninfo_ir_surveys" in res
        assert res["cninfo_ir_surveys"].status == STATUS_OK
        assert "event_coverage" in res.get("market_data_context", {})
        cov = res["market_data_context"]["event_coverage"]
        assert len(cov.get("clusters", [])) == 1
        assert cov["clusters"][0].get("canonical_event_id") == "cninfo:1225488095"
