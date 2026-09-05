"""Unit tests for DataCollector collateral provider integration (C-05 Slice 6 / DAV-657).

Enforces contracts:
1. _fetch_all(..., trade_date) passes the shared cn_akshare provider instance from
   tradingagents.dataflows.interface._registry.get("cn_akshare") to build_news_event_coverage,
   with default_entity=ticker and cutoff=trade_date.
2. Reuses existing _fetch_tushare_forecast / _fetch_tushare_repurchase / _fetch_tushare_disclosure_date;
   zero modifications to their implementations.
3. provider_failure enters event_coverage gaps, and must NOT be classified as successful empty.
4. collateral_empty strictly forbids '确认无公告' anywhere in output/coverage.
5. Collateral records strictly keep canonical_event_id=None; strictly forbids inventing cninfo: IDs.
6. All tests are fully mocked: no real gateway, no reading .env tokens, tests directly verify
   the _fetch_all pipeline path.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

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
    """Mock sub-tasks inside _fetch_all so external network calls are suppressed."""
    mock_ind = MagicMock()
    mock_ind.get_industry_linkage.return_value = None
    with patch.object(data_collector, "_safe", return_value=""), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 1), \
         patch.object(data_collector, "_DEFAULT_INDUSTRY_LINKAGE_PROVIDER", mock_ind):
        yield mock_ind


# ==============================================================================
# Contract 1: _fetch_all passes shared cn_akshare provider instance
# ==============================================================================

def test_fetch_all_passes_shared_cn_akshare_provider(mock_sub_tasks):
    """Contract 1: _fetch_all passes the exact _registry.get('cn_akshare') instance to build_news_event_coverage."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    with patch("tradingagents.graph.data_collector.build_news_event_coverage") as mock_build_cov:
        mock_build_cov.return_value = {"status": "mocked", "is_confirmed_empty": False}

        _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        assert mock_build_cov.called
        call_kwargs = mock_build_cov.call_args.kwargs
        assert call_kwargs.get("provider") is cn_provider
        assert call_kwargs.get("default_entity") == "600519.SH"
        assert call_kwargs.get("cutoff") == "2025-01-25"


# ==============================================================================
# Contract 2 & 5: Soft alignment hit & canonical_event_id preservation
# ==============================================================================

def test_fetch_all_forecast_collateral_soft_alignment_hit(mock_sub_tasks):
    """Contract 2: Mock forecast collateral soft-aligns to primary announcement in _fetch_all."""
    cn_provider = _registry.get("cn_akshare")

    news_md = (
        "### 贵州茅台关于2024年度业绩预告的公告 [发布时间：2025-01-20 18:00:00] (source: cninfo)\n"
        "预计归母净利润大幅增长。"
    )

    def mock_safe_with_news(tool, payload):
        tool_name = getattr(tool, "name", str(tool))
        if "get_news" in tool_name or "news" in str(tool):
            return news_md
        return ""

    mock_forecast_recs = [
        {
            "symbol": "600519.SH",
            "ann_date": "2025-01-20",
            "source_type": "tushare_forecast",
            "collateral_id": "tushare:forecast:600519.SH:20250120:20241231",
            "payload": {"type": "预增", "p_change_min": 45.0, "net_profit_min": 15000.0},
            "canonical_event_id": None,
        }
    ]

    with patch.object(data_collector, "_safe", side_effect=mock_safe_with_news), \
         patch.object(cn_provider, "_fetch_tushare_forecast", return_value=(mock_forecast_recs, None, None)), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        assert event_cov["attached_collateral_count"] >= 1
        assert len(event_cov["collateral_records"]) >= 1

        # Check clusters in event_coverage & market_data_context
        clusters = event_cov["clusters"]
        assert len(clusters) == 1
        matched_cluster = clusters[0]
        assert "业绩预告" in matched_cluster["title"]
        assert len(matched_cluster["collateral_records"]) >= 1

        col_in_cluster = matched_cluster["collateral_records"][0]
        assert col_in_cluster["collateral_id"] == "tushare:forecast:600519.SH:20250120:20241231"
        # Contract 5: canonical_event_id must strictly be None
        assert col_in_cluster["canonical_event_id"] is None

        # Verify market_data_context consistency
        assert res["market_data_context"]["event_coverage"] is event_cov


# ==============================================================================
# Contract 3 & 5: Unattached collateral retained independently
# ==============================================================================

def test_fetch_all_repurchase_collateral_unattached_independent_item(mock_sub_tasks):
    """Contract 3 & 5: Unattached repurchase collateral retained as independent [结构化旁证] item with canonical_event_id=None."""
    cn_provider = _registry.get("cn_akshare")

    mock_repurchase_recs = [
        {
            "symbol": "600519.SH",
            "ann_date": "2025-01-20",
            "source_type": "tushare_repurchase",
            "collateral_id": "tushare:repurchase:600519.SH:20250120",
            "payload": {"proc": "实施中", "amount": 5000.0, "vol": 100.0},
            "canonical_event_id": None,
        }
    ]

    with patch.object(cn_provider, "_fetch_tushare_forecast", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=(mock_repurchase_recs, None, None)), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        assert event_cov["independent_collateral_count"] >= 1
        assert len(event_cov["collateral_records"]) >= 1

        clusters = event_cov["clusters"]
        assert len(clusters) >= 1
        ind_cluster = clusters[0]
        assert "[结构化旁证]" in ind_cluster["title"]
        assert "股票回购" in ind_cluster["title"]
        # Contract 5: canonical_event_id must strictly be None
        assert ind_cluster.get("canonical_event_id") is None

        # Evidences inside the cluster must also have canonical_event_id=None
        for ev in ind_cluster.get("evidences", []):
            assert ev.get("canonical_event_id") is None
            assert not str(ev.get("canonical_event_id") or "").startswith("cninfo:")


def test_fetch_all_disclosure_date_collateral_independent_item(mock_sub_tasks):
    """Contract 3 & 5: Unattached disclosure_date collateral retained as independent [结构化旁证] item."""
    cn_provider = _registry.get("cn_akshare")

    mock_dd_recs = [
        {
            "symbol": "600519.SH",
            "ann_date": "2025-01-20",
            "source_type": "tushare_disclosure_date",
            "collateral_id": "tushare:disclosure_date:600519.SH:20250120:20241231",
            "payload": {"pre_date": "2025-04-20", "end_date": "20241231"},
            "canonical_event_id": None,
        }
    ]

    with patch.object(cn_provider, "_fetch_tushare_forecast", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=(mock_dd_recs, None, None)):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        assert event_cov["independent_collateral_count"] >= 1
        assert len(event_cov["clusters"]) >= 1

        cluster = event_cov["clusters"][0]
        assert "[结构化旁证]" in cluster["title"]
        assert "定期报告披露计划" in cluster["title"]
        assert cluster.get("canonical_event_id") is None


# ==============================================================================
# Contract 3: provider_failure enters gaps and is not empty success
# ==============================================================================

def test_fetch_all_collateral_provider_failure_enters_gap(mock_sub_tasks):
    """Contract 3: provider_failure (403/permission_denied) enters event_coverage gaps and is not empty success."""
    cn_provider = _registry.get("cn_akshare")

    with patch.object(cn_provider, "_fetch_tushare_forecast", return_value=(
        [],
        "tushare.forecast:provider_failure(403_forbidden)",
        "provider_failure",
    )), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        assert event_cov["recall_status"] == "provider_failure"
        assert event_cov["has_gap"] is True
        assert event_cov["is_confirmed_empty"] is False

        # Verify failure gap is present
        suspected_gaps = event_cov.get("suspected_gaps", [])
        failure_gaps = [g for g in suspected_gaps if g.get("status") == "provider_failure"]
        assert len(failure_gaps) >= 1
        fg = failure_gaps[0]
        assert fg.get("source") == "tushare_forecast"
        assert "403" in fg.get("reason", "") or "provider_failure" in fg.get("reason", "")
        assert "不可验证" in fg.get("message", "")


# ==============================================================================
# Contract 4: collateral_empty never produces 「确认无公告」
# ==============================================================================

def test_fetch_all_collateral_empty_does_not_produce_confirmed_empty(mock_sub_tasks):
    """Contract 4: When collateral tables return empty (0 rows), output must NEVER contain '确认无公告'."""
    cn_provider = _registry.get("cn_akshare")

    with patch.object(cn_provider, "_fetch_tushare_forecast", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")):

        res = _fetch_all("600519.SH", "2025-01-25", industry_provider=mock_sub_tasks)

        event_cov = res["event_coverage"]
        assert event_cov["is_confirmed_empty"] is False

        # Strictly verify '确认无公告' does not appear anywhere in serialized coverage or market data context
        cov_str = json.dumps(event_cov, ensure_ascii=False)
        assert "确认无公告" not in cov_str

        market_ctx_str = json.dumps(res.get("market_data_context", {}), ensure_ascii=False)
        assert "确认无公告" not in market_ctx_str


# ==============================================================================
# Integration: DataCollector.collect() end-to-end with collateral
# ==============================================================================

def test_data_collector_collect_end_to_end_with_collateral(mock_sub_tasks):
    """End-to-end integration: DataCollector.collect() successfully includes collateral in market_data_context."""
    cn_provider = _registry.get("cn_akshare")

    mock_forecast_recs = [
        {
            "symbol": "600519.SH",
            "ann_date": "2025-01-20",
            "source_type": "tushare_forecast",
            "collateral_id": "tushare:forecast:600519.SH:20250120:20241231",
            "payload": {"type": "预增", "p_change_min": 50.0},
            "canonical_event_id": None,
        }
    ]

    collector = DataCollector()
    with patch.object(cn_provider, "_fetch_tushare_forecast", return_value=(mock_forecast_recs, None, None)), \
         patch.object(cn_provider, "_fetch_tushare_repurchase", return_value=([], None, "collateral_empty")), \
         patch.object(cn_provider, "_fetch_tushare_disclosure_date", return_value=([], None, "collateral_empty")), \
         patch.object(collector, "_fetch_social_context", return_value={}):

        result = collector.collect("600519.SH", "2025-01-25")

        market_ctx = result.get("market_data_context", {})
        cov = market_ctx.get("event_coverage", {})
        assert cov is not None
        assert len(cov.get("collateral_records", [])) == 1
        assert cov["collateral_records"][0]["collateral_id"] == "tushare:forecast:600519.SH:20250120:20241231"
        assert cov["collateral_records"][0]["canonical_event_id"] is None
