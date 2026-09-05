"""Unit tests for structured collateral attachment to CNINFO primary evidence (C-05 Slice 5 / DAV-646).

Enforces contracts from work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md §3.3:
1. canonical_event_id is ONLY from primary CNINFO; collateral_id uses tushare: namespace,
   and collateral canonical_event_id is ALWAYS None.
2. Soft alignment three elements:
   - Same symbol (normalized)
   - |primary.announced_at.date - collateral.ann_date| <= 1 day
   - Theme match: forecast <-> 预告/业绩/中报/年报; repurchase <-> 回购/股份变动;
     disclosure_date <-> 定期报告披露日期/变更披露日期
3. Unattached collaterals retained independently labeled '[结构化旁证]', never masquerading as full announcements.
4. provider_failure entered into gaps, not empty; collateral_empty strictly forbids '确认无公告'.
5. Mocks collateral and primary sources; tests hit attachment, date out-of-bounds, different symbol,
   actual_date in future still attaching, and canonical_event_id preservation. Strictly forbids real gateway.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from tradingagents.dataflows.cninfo_disclosure import (
    CninfoDisclosureEnvelope,
    CninfoDisclosureRecord,
)
from tradingagents.dataflows.news_event_evidence import (
    CollateralEnvelope,
    CollateralRecord,
    EventCluster,
    NewsEvidence,
    SOURCE_TYPE_TUSHARE_DISCLOSURE_DATE,
    SOURCE_TYPE_TUSHARE_FORECAST,
    SOURCE_TYPE_TUSHARE_REPURCHASE,
    attach_collateral_to_evidence,
    attach_collaterals_to_envelope,
    attach_collaterals_to_evidences,
    build_news_event_coverage,
    check_soft_alignment,
    check_theme_match,
    cluster_news_evidences,
    collateral_record_to_evidence,
    fetch_and_attach_tushare_collaterals,
    fetch_tushare_collaterals,
    format_event_coverage_summary,
    normalize_symbol,
)
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider


# ==============================================================================
# Contract 1: ID Isolation & Collateral Namespace
# ==============================================================================

def test_collateral_record_canonical_event_id_always_none():
    """Contract 1: canonical_event_id MUST be None for CollateralRecord; strictly forbids inventing cninfo ID."""
    col = CollateralRecord(
        symbol="600519.SH",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519.SH:20250120:20241231",
        payload={"type": "预增", "p_change_min": 45.0},
    )
    assert col.canonical_event_id is None
    assert col.collateral_id.startswith("tushare:")
    assert col.ann_date == "2025-01-20"

    d = col.to_dict()
    assert d["canonical_event_id"] is None
    assert d["collateral_id"] == "tushare:forecast:600519.SH:20250120:20241231"

    # Attempting to assign non-None canonical_event_id raises ValueError
    with pytest.raises(ValueError, match="CollateralRecord strictly forbids canonical_event_id"):
        CollateralRecord(
            symbol="600519.SH",
            ann_date="2025-01-20",
            source_type="tushare_forecast",
            collateral_id="tushare:forecast:600519.SH:20250120",
            canonical_event_id="cninfo:fake_id",  # Forbidden!
        )


def test_collateral_record_from_dict_enforces_none_canonical_id():
    """CollateralRecord.from_dict forces canonical_event_id to None even if forged in raw dict."""
    raw = {
        "symbol": "000001",
        "ann_date": "2025-01-20",
        "source_type": "tushare_repurchase",
        "collateral_id": "tushare:repurchase:000001:20250120",
        "canonical_event_id": "cninfo:forged_12345",  # Must be ignored/reset
        "payload": {"proc": "实施中", "amount": 5000.0},
    }
    col = CollateralRecord.from_dict(raw)
    assert col.canonical_event_id is None
    assert col.symbol == "000001"
    assert col.source_type == "tushare_repurchase"
    assert col.payload == {"proc": "实施中", "amount": 5000.0}


# ==============================================================================
# Contract 2 & 5: 软对齐三要素 - 命中挂载 (Hit and Attach)
# ==============================================================================

def test_soft_alignment_hit_and_attach_forecast():
    """Contract 2 & 5: 同标的, 日期容差 <= 1日, 标题命中业绩预告 -> 成功挂载, canonical_event_id 不被改写."""
    primary = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        source="cninfo_announcement",
        canonical_event_id="cninfo:1221849382",
    )
    col = CollateralRecord(
        symbol="600519.SH",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519.SH:20250120:20241231",
        payload={"type": "预增", "p_change_min": 50.0, "p_change_max": 55.0},
    )

    attached = attach_collateral_to_evidence(primary, col, tolerance_days=1)
    assert attached is True
    assert len(primary.collateral_records) == 1
    # Check attached record
    attached_col = primary.collateral_records[0]
    assert attached_col.collateral_id == "tushare:forecast:600519.SH:20250120:20241231"
    assert attached_col.canonical_event_id is None
    # Contract 5: primary canonical_event_id is NOT overwritten!
    assert primary.canonical_event_id == "cninfo:1221849382"


def test_soft_alignment_hit_and_attach_repurchase_next_day_tolerance():
    """Contract 2: 晚间公告次日入库(+1日容差)同标的命中股票回购 -> 成功挂载."""
    primary = NewsEvidence(
        title="关于以集中竞价交易方式回购公司股份的方案",
        published_at="2025-01-20 20:15:00",
        entity="000001",
        source="cninfo_announcement",
        canonical_event_id="cninfo:9876543210",
    )
    # Tushare collateral recorded on next trading day (+1 day tolerance)
    col = CollateralRecord(
        symbol="000001",
        ann_date="2025-01-21",
        source_type="tushare_repurchase",
        collateral_id="tushare:repurchase:000001:20250121:实施",
        payload={"proc": "实施", "vol": 1000000},
    )

    assert check_soft_alignment(primary, col, tolerance_days=1) is True
    assert attach_collateral_to_evidence(primary, col, tolerance_days=1) is True
    assert len(primary.collateral_records) == 1
    assert primary.canonical_event_id == "cninfo:9876543210"


def test_soft_alignment_hit_and_attach_disclosure_date_prior_day_tolerance():
    """Contract 2: 定期报告披露日期标题命中 (-1日容差) -> 成功挂载."""
    primary = NewsEvidence(
        title="关于变更定期报告披露日期的公告",
        published_at="2025-01-20 08:30:00",
        entity="600519",
        source="cninfo_announcement",
        canonical_event_id="cninfo:44556677",
    )
    # Tushare collateral has ann_date 2025-01-19 (-1 day tolerance)
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-19",
        source_type="tushare_disclosure_date",
        collateral_id="tushare:disclosure_date:600519:20250119:20241231",
        payload={"end_date": "20241231", "pre_date": "2025-04-18"},
    )

    assert check_soft_alignment(primary, col, tolerance_days=1) is True
    assert attach_collateral_to_evidence(primary, col, tolerance_days=1) is True
    assert len(primary.collateral_records) == 1


# ==============================================================================
# Contract 2 & 5: 日期越界不挂 (Date Out-of-Bounds Does Not Attach)
# ==============================================================================

def test_date_out_of_bounds_does_not_attach():
    """Contract 5: 日期容差越界（|diff| > 1 日）坚决不挂载."""
    primary = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:1221849382",
    )
    # Collateral date is 2025-01-22 (2 days later > 1 day tolerance)
    col_late = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-22",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250122",
        payload={"type": "预增"},
    )
    # Collateral date is 2025-01-18 (2 days earlier > 1 day tolerance)
    col_early = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-18",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250118",
        payload={"type": "预增"},
    )

    assert check_soft_alignment(primary, col_late, tolerance_days=1) is False
    assert check_soft_alignment(primary, col_early, tolerance_days=1) is False

    assert attach_collateral_to_evidence(primary, col_late, tolerance_days=1) is False
    assert attach_collateral_to_evidence(primary, col_early, tolerance_days=1) is False
    assert len(primary.collateral_records) == 0


# ==============================================================================
# Contract 2 & 5: 异标的不挂 (Different Symbol Does Not Attach)
# ==============================================================================

def test_different_symbol_does_not_attach():
    """Contract 5: 异标的坚决不挂载 (不同股票代码即使同日同主题也严格隔离)."""
    primary = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:1221849382",
    )
    col = CollateralRecord(
        symbol="000001.SZ",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:000001.SZ:20250120",
        payload={"type": "预增"},
    )

    assert check_soft_alignment(primary, col, tolerance_days=1) is False
    assert attach_collateral_to_evidence(primary, col, tolerance_days=1) is False
    assert len(primary.collateral_records) == 0


def test_theme_mismatch_does_not_attach():
    """Contract 2: 标题主题不对应不挂载 (对外投资公告不挂业绩预告旁证)."""
    primary = NewsEvidence(
        title="关于对外投资设立全资子公司的进展公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:998877",
    )
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增"},
    )

    assert check_theme_match(primary.title, col.source_type) is False
    assert check_soft_alignment(primary, col, tolerance_days=1) is False
    assert attach_collateral_to_evidence(primary, col, tolerance_days=1) is False
    assert len(primary.collateral_records) == 0


# ==============================================================================
# Contract 5: actual_date 未来仍可挂 (PIT 已在 fetch)
# ==============================================================================

def test_actual_date_in_future_still_attaches():
    """Contract 5: disclosure_date 的 actual_date 在未来仍可挂载 (因为 PIT 已在 fetch 按 ann_date 截断)."""
    primary = NewsEvidence(
        title="关于定期报告披露日期的公告",
        published_at="2025-01-20 18:00:00",
        entity="600519",
        canonical_event_id="cninfo:334455",
    )
    # actual_date is 2025-04-28 (3 months in the future), but ann_date is 2025-01-20 (PIT compliant)
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_disclosure_date",
        collateral_id="tushare:disclosure_date:600519:20250120:20241231",
        payload={
            "end_date": "20241231",
            "pre_date": "2025-04-15",
            "actual_date": "2025-04-28",  # Future date preserved as collateral payload
            "modify_date": None,
        },
    )

    # Soft alignment checks ann_date, not actual_date
    assert check_soft_alignment(primary, col, tolerance_days=1) is True
    attached = attach_collateral_to_evidence(primary, col, tolerance_days=1)
    assert attached is True
    assert len(primary.collateral_records) == 1
    assert primary.collateral_records[0].payload["actual_date"] == "2025-04-28"
    assert primary.canonical_event_id == "cninfo:334455"


# ==============================================================================
# Contract 1 & 5: canonical_event_id 不被旁证改写
# ==============================================================================

def test_canonical_event_id_never_overwritten_or_invented():
    """Contract 1 & 5: 巨潮 ID 不被旁证改写; 若主源原本无 ID，旁证也不得伪造 cninfo ID."""
    # Case A: primary has ID
    primary_with_id = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:1221849382",
    )
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增"},
    )
    attach_collateral_to_evidence(primary_with_id, col)
    assert primary_with_id.canonical_event_id == "cninfo:1221849382"
    assert primary_with_id.collateral_records[0].canonical_event_id is None

    # Case B: primary has NO ID
    primary_no_id = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id=None,
    )
    attach_collateral_to_evidence(primary_no_id, col)
    # Strictly None, never invented!
    assert primary_no_id.canonical_event_id is None
    assert primary_no_id.collateral_records[0].canonical_event_id is None


# ==============================================================================
# Contract 3: 主源未命中时旁证独立留存
# ==============================================================================

def test_unattached_collateral_retained_independently_labeled_structured_collateral():
    """Contract 3: 主源未命中时旁证可独立留存，标签必须是 [结构化旁证]，不得冒充全量公告."""
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120:20241231",
        payload={"type": "扭亏", "summary": "预计净利润扭亏为盈"},
    )

    ind_ev = collateral_record_to_evidence(col, default_entity="600519")
    assert "[结构化旁证]" in ind_ev.title
    assert "业绩预告" in ind_ev.title
    assert ind_ev.canonical_event_id is None  # Strictly None
    assert ind_ev.source == "tushare_forecast"
    assert ind_ev.published_at == "2025-01-20 00:00:00"
    assert len(ind_ev.collateral_records) == 1
    assert ind_ev.collateral_records[0] == col


def test_attach_collaterals_to_evidences_retains_unattached():
    """attach_collaterals_to_evidences: 匹配项成功挂载，未匹配项作为独立 [结构化旁证] 追加."""
    # Primary announcement is repurchase
    primary = NewsEvidence(
        title="关于以集中竞价交易方式回购股份的公告",
        published_at="2025-01-20 18:00:00",
        entity="600519",
        canonical_event_id="cninfo:112233",
    )
    # Collateral 1: matches primary repurchase
    col_repurchase = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_repurchase",
        collateral_id="tushare:repurchase:600519:20250120",
        payload={"proc": "预案"},
    )
    # Collateral 2: forecast (no primary announcement in this batch!)
    col_forecast = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增"},
    )

    ev_list, unattached = attach_collaterals_to_evidences(
        [primary], [col_repurchase, col_forecast], tolerance_days=1, retain_unattached=True
    )

    # Primary has col_repurchase attached
    assert len(primary.collateral_records) == 1
    assert primary.collateral_records[0].collateral_id == col_repurchase.collateral_id
    assert primary.canonical_event_id == "cninfo:112233"

    # col_forecast was unattached, so it is in unattached list and appended as independent evidence
    assert len(unattached) == 1
    assert unattached[0].collateral_id == col_forecast.collateral_id

    assert len(ev_list) == 2
    ind_ev = ev_list[1]
    assert "[结构化旁证]" in ind_ev.title
    assert ind_ev.canonical_event_id is None
    assert ind_ev.source == "tushare_forecast"


# ==============================================================================
# Contract 4: provider_failure 进 gap; collateral_empty 文本不含「确认无公告」
# ==============================================================================

def test_collateral_provider_failure_recorded_in_failure_gaps():
    """Contract 4: 私有网关 provider_failure (403/权限拒绝) 诚实记入 failure_gaps，禁止隐瞒或作为空数据."""
    env = CollateralEnvelope(
        status="provider_failure",
        source_type="tushare_forecast",
        records=[],
        error="403_forbidden",
        category="provider_failure",
    )

    coverage = build_news_event_coverage(
        items_or_evidences=[],
        cutoff="2025-01-25",
        collateral_envelopes=[env],
    )

    assert coverage["recall_status"] == "provider_failure"
    assert coverage["is_confirmed_empty"] is False
    assert len(coverage["suspected_gaps"]) >= 1
    gap = next(g for g in coverage["suspected_gaps"] if g.get("source") == "tushare_forecast")
    assert gap["status"] == "provider_failure"
    assert "403" in gap["message"] or "provider_failure" in gap["message"]
    assert "不可验证" in gap["message"]
    assert "确认无公告" not in gap["message"]


def test_collateral_empty_never_reports_confirmed_empty():
    """Contract 4: 旁证空表 (0行) 判定为 collateral_empty，绝对禁止写成「确认无公告」或「全市场无新闻」."""
    env = CollateralEnvelope(
        status="collateral_empty",
        source_type="tushare_repurchase",
        records=[],
    )

    coverage = build_news_event_coverage(
        items_or_evidences=[],
        cutoff="2025-01-25",
        collateral_envelopes=[env],
        query_manifest=["tushare_repurchase"],
    )

    assert coverage["is_confirmed_empty"] is False
    assert coverage.get("cninfo_status") != "confirmed_empty"

    # Gap message for collateral_empty
    assert len(coverage["suspected_gaps"]) >= 1
    gap = coverage["suspected_gaps"][0]
    assert "未检索到结构化旁证记录" in gap["message"]
    assert "确认无公告" not in gap["message"]
    assert "全市场无新闻" not in gap["message"]

    summary = format_event_coverage_summary(coverage)
    assert "确认无公告" not in summary
    assert "全市场无新闻" not in summary


# ==============================================================================
# Envelope & Clustering Integration
# ==============================================================================

def test_attach_collaterals_to_cninfo_envelope():
    """attach_collaterals_to_envelope attaches to matching record inside CninfoDisclosureEnvelope."""
    rec1 = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    rec2 = CninfoDisclosureRecord(
        symbol="600519",
        title="关于召开股东大会的通知",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/2",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849383",
        canonical_event_id="cninfo:1221849383",
    )
    env = CninfoDisclosureEnvelope(
        status="ok",
        records=[rec1, rec2],
        source_type="cninfo_announcement",
    )

    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增"},
    )

    updated_env, unattached = attach_collaterals_to_envelope(env, [col], tolerance_days=1)
    assert len(unattached) == 0
    # rec1 matched and has collateral attached
    assert hasattr(rec1, "collateral_records")
    assert len(rec1.collateral_records) == 1
    assert rec1.canonical_event_id == "cninfo:1221849382"
    # rec2 did not match
    assert not getattr(rec2, "collateral_records", None)


def test_event_cluster_aggregates_collaterals():
    """cluster_news_evidences collects collateral records into EventCluster.collateral_records."""
    ev = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:1221849382",
    )
    col = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增", "p_change_min": 40.0},
    )
    ev.attach_collateral(col)

    clusters = cluster_news_evidences([ev])
    assert len(clusters) == 1
    c = clusters[0]
    assert c.canonical_event_id == "cninfo:1221849382"
    assert len(c.collateral_records) == 1
    assert c.collateral_records[0].collateral_id == col.collateral_id

    d = c.to_dict()
    assert "collateral_records" in d
    assert d["collateral_records"][0]["collateral_id"] == col.collateral_id
    assert d["collateral_records"][0]["canonical_event_id"] is None


def test_format_event_coverage_summary_includes_collaterals():
    """format_event_coverage_summary correctly highlights attached and independent collateral counts."""
    coverage = {
        "cutoff": "2025-01-25",
        "window": "14天",
        "recall_status": "partial_vs_manifest",
        "source_manifest": ["cninfo_announcement", "tushare_forecast"],
        "query_manifest": ["财报"],
        "hit_count": 1,
        "collateral_records": [{"collateral_id": "tushare:1"}, {"collateral_id": "tushare:2"}],
        "attached_collateral_count": 1,
        "independent_collateral_count": 1,
    }
    summary = format_event_coverage_summary(coverage)
    assert "- 结构化旁证挂载（collateral_records）：共 2 条（主源挂载 1 条，独立留存 1 条；标签：[结构化旁证]）" in summary


# ==============================================================================
# Mock Provider Integration (No Real Gateway)
# ==============================================================================

def test_fetch_and_attach_tushare_collaterals_with_mock_provider():
    """Mock CnAkshareProvider methods to verify zero real-gateway traffic and complete workflow."""
    mock_provider = MagicMock(spec=CnAkshareProvider)
    mock_provider._fetch_tushare_forecast.return_value = (
        [
            {
                "canonical_event_id": None,
                "collateral_id": "tushare:forecast:600519.SH:2025-01-20",
                "source_type": "tushare_forecast",
                "symbol": "600519",
                "ts_code": "600519.SH",
                "ann_date": "2025-01-20",
                "payload": {"type": "预增"},
            }
        ],
        None,
        None,
    )
    mock_provider._fetch_tushare_repurchase.return_value = (
        [],
        "tushare.repurchase:collateral_empty",
        "collateral_empty",
    )
    mock_provider._fetch_tushare_disclosure_date.return_value = (
        [],
        "tushare.disclosure_date:provider_failure(403_forbidden)",
        "provider_failure",
    )

    primary = NewsEvidence(
        title="关于2024年年度业绩预告的公告",
        published_at="2025-01-20 18:30:00",
        entity="600519",
        canonical_event_id="cninfo:1221849382",
    )

    evs, unattached, gaps, envelopes = fetch_and_attach_tushare_collaterals(
        mock_provider,
        symbol="600519",
        evidences=[primary],
        as_of="2025-01-25",
    )

    # Forecast matched and attached
    assert len(primary.collateral_records) == 1
    assert primary.canonical_event_id == "cninfo:1221849382"
    assert len(unattached) == 0

    # Disclosure date provider_failure in gaps
    assert any(g.get("source") == "tushare_disclosure_date" and g.get("status") == "provider_failure" for g in gaps)

    # Repurchase collateral_empty envelope exists
    assert any(e.source_type == "tushare_repurchase" and e.status == "collateral_empty" for e in envelopes)


def test_build_news_event_coverage_full_collateral_pipeline():
    """End-to-end build_news_event_coverage pipeline with primary announcement, matched & unattached collaterals."""
    cninfo_rec = CninfoDisclosureRecord(
        symbol="600519",
        title="关于2024年年度业绩预告的公告",
        announced_at="2025-01-20 18:30:00",
        url="http://cninfo.com.cn/1",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1221849382",
        canonical_event_id="cninfo:1221849382",
    )
    cninfo_env = CninfoDisclosureEnvelope(
        status="ok",
        records=[cninfo_rec],
        source_type="cninfo_announcement",
    )

    col_forecast = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_forecast",
        collateral_id="tushare:forecast:600519:20250120",
        payload={"type": "预增"},
    )
    col_repurchase = CollateralRecord(
        symbol="600519",
        ann_date="2025-01-20",
        source_type="tushare_repurchase",
        collateral_id="tushare:repurchase:600519:20250120",
        payload={"proc": "实施中"},
    )

    coverage = build_news_event_coverage(
        items_or_evidences=[],
        cninfo_envelopes=[cninfo_env],
        collateral_records=[col_forecast, col_repurchase],
        cutoff="2025-01-25",
        default_entity="600519",
        query_manifest=["财报", "公司治理"],
    )

    assert coverage["hit_count"] == 2  # 1 cluster for primary+forecast, 1 cluster for independent repurchase
    assert coverage["attached_collateral_count"] == 1
    assert coverage["independent_collateral_count"] == 1
    assert coverage["is_confirmed_empty"] is False

    # Check clusters
    clusters = coverage["clusters"]
    forecast_cluster = next(c for c in clusters if c.get("canonical_event_id") == "cninfo:1221849382")
    assert len(forecast_cluster["collateral_records"]) == 1
    assert forecast_cluster["collateral_records"][0]["collateral_id"] == col_forecast.collateral_id

    repurchase_cluster = next(c for c in clusters if c.get("canonical_event_id") is None)
    assert "[结构化旁证]" in repurchase_cluster["title"]
    assert len(repurchase_cluster["collateral_records"]) == 1
    assert repurchase_cluster["collateral_records"][0]["collateral_id"] == col_repurchase.collateral_id
