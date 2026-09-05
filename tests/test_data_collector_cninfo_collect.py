"""Unit tests for DataCollector CNINFO announcements integration (C-05 Slice 7 / DAV-659).

Enforces contracts:
1. _fetch_all(..., trade_date) calls get_cninfo_announcements on the shared
   _registry.get("cn_akshare") provider instance, with symbol=ticker,
   start_date=(trade_date - lookback [LONG_DAYS=90]), end_date=norm_trade_date,
   cutoff=norm_trade_date.
   _fetch_all output results["cninfo_announcements"] is always an envelope (has status/records),
   never a missing key.
2. When status=ok and records have canonical_event_id, event_coverage sees this primary
   source in clusters and evidences; collateral attachment preserves primary canonical_event_id
   without modifying it.
3. When provider_failure envelope occurs, event_coverage recall_status reports failure/gap,
   and must NOT be classified as confirmed_empty.
4. When records are empty (confirmed_empty or ok with 0 rows), coverage/context text
   strictly forbids containing '确认无公告'.
5. All tests mock get_cninfo_announcements with zero real network calls to .cninfo.com.cn.
   Tests directly execute through _fetch_all.
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

    def mock_safe_dispatch(tool, payload):
        tool_str = str(tool).lower()
        # Pass through cninfo calls, matching _safe error handling
        if "cutoff" in payload or "cninfo" in tool_str or "get_cninfo_announcements" in tool_str:
            try:
                return tool(**payload)
            except Exception as exc:
                return f"get_cninfo_announcements 调用失败：{type(exc).__name__}: {exc}"
        return ""

    with patch.object(data_collector, "_safe", side_effect=mock_safe_dispatch), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 2), \
         patch.object(data_collector, "_DEFAULT_INDUSTRY_LINKAGE_PROVIDER", mock_ind):
        yield mock_ind


# ==============================================================================
# Contract 1: _fetch_all calls get_cninfo_announcements with exact args and returns envelope
# ==============================================================================

def test_fetch_all_calls_get_cninfo_announcements_with_exact_args(mock_sub_tasks):
    """Contract 1: _fetch_all calls get_cninfo_announcements on shared cn_akshare provider
    with symbol, start_date (LONG_DAYS lookback), end_date, cutoff, and populates envelope."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )
    mock_get = MagicMock(return_value=mock_env)

    with patch.object(cn_provider, "get_cninfo_announcements", mock_get):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        # 1. Method called on shared provider
        assert mock_get.called
        kwargs = mock_get.call_args.kwargs
        assert kwargs.get("symbol") == "600519.SH"
        assert kwargs.get("start_date") == "2024-10-27"  # 2025-01-25 - 90 days
        assert kwargs.get("end_date") == "2025-01-25"
        assert kwargs.get("cutoff") == "2025-01-25"

        # 2. results["cninfo_announcements"] is the envelope
        assert "cninfo_announcements" in res
        ann = res["cninfo_announcements"]
        assert hasattr(ann, "status") and hasattr(ann, "records")
        assert ann.status == STATUS_OK
        assert len(ann.records) == 1
        assert ann.records[0].canonical_event_id == "cninfo:1221849382"


# ==============================================================================
# Contract 2: status=ok with canonical_event_id visible in event_coverage & preserved
# ==============================================================================

def test_fetch_all_normal_record_canonical_event_id_in_event_coverage(mock_sub_tasks):
    """Contract 2: status=ok with canonical_event_id enters event_coverage cluster and evidences."""
    cn_provider = _registry.get("cn_akshare")

    rec = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )
    mock_get = MagicMock(return_value=mock_env)

    with patch.object(cn_provider, "get_cninfo_announcements", mock_get):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res.get("event_coverage", {})
        assert event_cov is not None
        clusters = event_cov.get("clusters", [])
        assert len(clusters) == 1

        matched_cluster = clusters[0]
        assert matched_cluster.get("canonical_event_id") == "cninfo:1221849382"
        assert "业绩预告" in matched_cluster.get("title", "")

        # Also verified in evidences
        evidences = matched_cluster.get("evidences", [])
        assert any(e.get("canonical_event_id") == "cninfo:1221849382" for e in evidences)


def test_fetch_all_collateral_does_not_overwrite_primary_canonical_event_id(mock_sub_tasks):
    """Contract 2: When collateral attaches to primary CNINFO evidence, primary canonical_event_id
    is strictly preserved and collateral maintains canonical_event_id=None."""
    cn_provider = _registry.get("cn_akshare")

    primary_rec = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[primary_rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    mock_forecast = [
        {
            "symbol": "600519.SH",
            "ann_date": "2025-01-20",
            "source_type": "tushare_forecast",
            "collateral_id": "tushare:forecast:600519.SH:20250120:20241231",
            "payload": {"type": "预增", "p_change_min": 45.0},
            "canonical_event_id": None,
        }
    ]

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch.object(cn_provider, "_fetch_tushare_forecast", return_value=(mock_forecast, None, None)), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        clusters = event_cov.get("clusters", [])
        assert len(clusters) == 1

        cluster = clusters[0]
        # Primary canonical_event_id is NOT overwritten
        assert cluster.get("canonical_event_id") == "cninfo:1221849382"

        # Collateral attached
        col_records = cluster.get("collateral_records", [])
        assert len(col_records) == 1
        assert col_records[0].get("collateral_id") == "tushare:forecast:600519.SH:20250120:20241231"
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
        error="KeyError: 'category'",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=fail_env):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        # 1. results["cninfo_announcements"] has provider_failure envelope
        assert "cninfo_announcements" in res
        ann = res["cninfo_announcements"]
        assert hasattr(ann, "status")
        assert ann.status == STATUS_PROVIDER_FAILURE
        assert ann.is_failure is True

        # 2. event_coverage recall_status is failure, not confirmed_empty
        event_cov = res.get("event_coverage", {})
        assert event_cov.get("recall_status") == "provider_failure"
        assert event_cov.get("has_gap") is True
        assert event_cov.get("is_confirmed_empty") is False

        # 3. Gap logged for cninfo_announcement
        suspected_gaps = event_cov.get("suspected_gaps", [])
        cninfo_gaps = [g for g in suspected_gaps if "cninfo" in str(g.get("source", ""))]
        assert len(cninfo_gaps) >= 1
        assert cninfo_gaps[0].get("status") == "provider_failure"

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
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=empty_env):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        assert res["cninfo_announcements"].status == STATUS_CONFIRMED_EMPTY
        assert len(res["cninfo_announcements"].records) == 0

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
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=ok_empty_env):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        assert res["cninfo_announcements"].status == STATUS_OK
        assert len(res["cninfo_announcements"].records) == 0

        cov_str = json.dumps(res.get("event_coverage", {}), ensure_ascii=False)
        assert "确认无公告" not in cov_str

        market_ctx_str = json.dumps(res.get("market_data_context", {}), ensure_ascii=False)
        assert "确认无公告" not in market_ctx_str


# ==============================================================================
# Contract 5 & 1 Fallback: Unhandled exception safely produces provider_failure envelope
# ==============================================================================

def test_fetch_all_unhandled_exception_safely_falls_back_to_provider_failure(mock_sub_tasks):
    """Contract 1 & 5: If get_cninfo_announcements raises an unhandled exception,
    _fetch_all ensures results['cninfo_announcements'] is a provider_failure envelope,
    not a raw error string or missing key."""
    cn_provider = _registry.get("cn_akshare")

    mock_get = MagicMock(side_effect=RuntimeError("remote gateway crashed"))

    with patch.object(cn_provider, "get_cninfo_announcements", mock_get):
        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        assert "cninfo_announcements" in res
        ann = res["cninfo_announcements"]
        assert hasattr(ann, "status") and hasattr(ann, "records")
        assert ann.status == STATUS_PROVIDER_FAILURE
        assert ann.records == []


# ==============================================================================
# Integration: DataCollector.collect() end-to-end with CNINFO envelope
# ==============================================================================

def test_data_collector_collect_end_to_end_with_cninfo(mock_sub_tasks):
    """End-to-end integration: DataCollector.collect() completes and returns cninfo in context."""
    cn_provider = _registry.get("cn_akshare")

    rec = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    collector = DataCollector(industry_linkage_provider=mock_sub_tasks)

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env):
        res = collector.collect("600519.SH", "2025-01-25")

        assert "cninfo_announcements" in res
        assert res["cninfo_announcements"].status == STATUS_OK
        assert "event_coverage" in res.get("market_data_context", {})
        cov = res["market_data_context"]["event_coverage"]
        assert len(cov.get("clusters", [])) == 1
        assert cov["clusters"][0].get("canonical_event_id") == "cninfo:1221849382"
