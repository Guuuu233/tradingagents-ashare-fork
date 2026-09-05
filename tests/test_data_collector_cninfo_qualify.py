"""Unit tests for DataCollector CNINFO qualify_cninfo_content integration (C-05 Slice 9 / DAV-663).

Enforces contracts:
1. _fetch_all(..., trade_date) calls qualify_cninfo_content on the shared
   _registry.get("cn_akshare") provider instance for each record in ok envelopes
   (cninfo_announcements and cninfo_ir_surveys) with cutoff=norm_trade_date.
   After _fetch_all, records in ok envelopes are no longer default 'not_attempted'
   (records with mock PDF bytes become 'hashed' with 64-hex sha256; records
   missing adjunct or id become 'unavailable').
2. When provider_failure envelope occurs, it must NOT be converted to ok,
   nor converted to empty success, nor produce '确认无公告'.
3. Zero real network calls to .cninfo.com.cn. Tests mock qualify_cninfo_content
   or the underlying download/fetch. At least: hashed 一条、缺 adjunct -> unavailable、
   provider_failure 不被 qualify 成空成功. All tests must execute through _fetch_all.
4. Tests do not contain PDF full text; assertions strictly check status and sha256.
5. Verification via:
   env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_data_collector_cninfo_qualify.py -q
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch
import pytest
import requests

from tradingagents.dataflows.cninfo_disclosure import (
    CONTENT_STATUS_HASHED,
    CONTENT_STATUS_NOT_ATTEMPTED,
    CONTENT_STATUS_UNAVAILABLE,
    CninfoDisclosureEnvelope,
    CninfoDisclosureRecord,
    STATUS_CONFIRMED_EMPTY,
    STATUS_OK,
    STATUS_PROVIDER_FAILURE,
    SOURCE_TYPE_ANNOUNCEMENT,
    SOURCE_TYPE_IR_SURVEY,
)
from tradingagents.dataflows.interface import _registry
from tradingagents.dataflows.news_event_evidence import cninfo_record_to_evidence
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
    while passing through cninfo calls to mocked provider methods.
    """
    mock_ind = MagicMock()
    mock_ind.get_industry_linkage.return_value = None
    cn_provider = _registry.get("cn_akshare")

    def mock_safe_dispatch(tool, payload):
        tool_str = str(tool).lower()
        if (
            tool == getattr(cn_provider, "get_cninfo_announcements", None)
            or "get_cninfo_announcements" in tool_str
            or "announcement" in tool_str
        ):
            if isinstance(getattr(cn_provider, "get_cninfo_announcements", None), MagicMock):
                return tool(**payload)
            return CninfoDisclosureEnvelope(
                status=STATUS_CONFIRMED_EMPTY,
                records=[],
                source_type=SOURCE_TYPE_ANNOUNCEMENT,
            )
        if (
            tool == getattr(cn_provider, "get_cninfo_ir_surveys", None)
            or "get_cninfo_ir_surveys" in tool_str
            or "cninfo_ir" in tool_str
        ):
            if isinstance(getattr(cn_provider, "get_cninfo_ir_surveys", None), MagicMock):
                return tool(**payload)
            return CninfoDisclosureEnvelope(
                status=STATUS_CONFIRMED_EMPTY,
                records=[],
                source_type=SOURCE_TYPE_IR_SURVEY,
            )
        return ""

    with patch.object(data_collector, "_safe", side_effect=mock_safe_dispatch), \
         patch.object(data_collector, "FETCH_ALL_TIMEOUT", 2), \
         patch.object(data_collector, "_DEFAULT_INDUSTRY_LINKAGE_PROVIDER", mock_ind):
        yield mock_ind


# ==============================================================================
# Contract 1 & 3: _fetch_all qualifies announcements to hashed & unavailable
# ==============================================================================

def test_fetch_all_qualifies_announcements_hashed_and_unavailable(mock_sub_tasks):
    """Contract 1, 3, 4: ok announcements envelope records are qualified via _fetch_all.
    - Valid PDF mock -> content_status='hashed', 64-hex sha256.
    - Missing adjunct_url -> content_status='unavailable', sha256 is None.
    - Missing announcement_id -> content_status='unavailable', sha256 is None.
    - No record remains 'not_attempted'.
    - cninfo_record_to_evidence reflects qualified status and sha256.
    """
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    mock_pdf_bytes = b"%PDF-1.4\nmock-bytes-for-sha256-qualification-001\n%%EOF"
    expected_sha256 = hashlib.sha256(mock_pdf_bytes).hexdigest()
    assert len(expected_sha256) == 64

    rec_hashed = CninfoDisclosureRecord(
        symbol="000001",
        title="关于重大事项进展的公告",
        announced_at="2026-08-20 09:30:00",
        url="http://cninfo.com.cn/ann/1",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000001",
        canonical_event_id="cninfo:1225000001",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225000001.pdf",
    )
    rec_missing_adjunct = CninfoDisclosureRecord(
        symbol="000001",
        title="关于董事会决议的公告",
        announced_at="2026-08-20 10:00:00",
        url="http://cninfo.com.cn/ann/2",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000002",
        canonical_event_id="cninfo:1225000002",
        adjunct_url=None,
    )
    rec_missing_id = CninfoDisclosureRecord(
        symbol="000001",
        title="关于临时股东大会的通知",
        announced_at="2026-08-20 10:30:00",
        url="http://cninfo.com.cn/ann/3",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id=None,
        canonical_event_id=None,
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225000003.pdf",
    )

    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec_hashed, rec_missing_adjunct, rec_missing_id],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = mock_pdf_bytes

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch("requests.get", return_value=mock_resp):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)

        ann = res.get("cninfo_announcements")
        assert ann is not None
        assert ann.status == STATUS_OK
        assert len(ann.records) == 3

        # 1. Hashed record: status='hashed', 64-hex SHA256
        assert ann.records[0].content_status == CONTENT_STATUS_HASHED
        assert ann.records[0].content_sha256 == expected_sha256

        # 2. Missing adjunct: status='unavailable', sha256=None
        assert ann.records[1].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[1].content_sha256 is None

        # 3. Missing native id: status='unavailable', sha256=None
        assert ann.records[2].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[2].content_sha256 is None

        # Contract 1: None of the records remain default 'not_attempted'
        for r in ann.records:
            assert r.content_status != CONTENT_STATUS_NOT_ATTEMPTED

        # Verify cninfo_record_to_evidence preserves qualified status and sha256
        ev = cninfo_record_to_evidence(ann.records[0], default_entity="000001")
        assert ev is not None
        assert ev.raw_item.get("content_status") == CONTENT_STATUS_HASHED
        assert ev.raw_item.get("content_sha256") == expected_sha256

        ev_unavail = cninfo_record_to_evidence(ann.records[1], default_entity="000001")
        assert ev_unavail is not None
        assert ev_unavail.raw_item.get("content_status") == CONTENT_STATUS_UNAVAILABLE
        assert ev_unavail.raw_item.get("content_sha256") is None


# ==============================================================================
# Contract 1 & 3: _fetch_all qualifies IR surveys to hashed & unavailable
# ==============================================================================

def test_fetch_all_qualifies_ir_surveys_hashed_and_unavailable(mock_sub_tasks):
    """Contract 1, 3, 4: ok IR surveys envelope records are qualified via _fetch_all.
    - Valid PDF mock -> content_status='hashed', 64-hex sha256.
    - Missing adjunct_url -> content_status='unavailable', sha256 is None.
    - No record remains 'not_attempted'.
    """
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    mock_pdf_bytes = b"%PDF-1.4\nmock-ir-survey-bytes-sha256-002\n%%EOF"
    expected_sha256 = hashlib.sha256(mock_pdf_bytes).hexdigest()
    assert len(expected_sha256) == 64

    ir_rec_hashed = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年8月投资者关系活动记录表",
        announced_at="2026-08-20 15:00:00",
        url="http://cninfo.com.cn/ir/10",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488999",
        canonical_event_id="cninfo:1225488999",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225488999.pdf",
    )
    ir_rec_no_adjunct = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年7月投资者关系调研说明",
        announced_at="2026-08-19 16:00:00",
        url="http://cninfo.com.cn/ir/11",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225488998",
        canonical_event_id="cninfo:1225488998",
        adjunct_url=None,
    )

    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[ir_rec_hashed, ir_rec_no_adjunct],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = mock_pdf_bytes

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=mock_env), \
         patch("requests.get", return_value=mock_resp):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)

        ir = res.get("cninfo_ir_surveys")
        assert ir is not None
        assert ir.status == STATUS_OK
        assert len(ir.records) == 2

        assert ir.records[0].content_status == CONTENT_STATUS_HASHED
        assert ir.records[0].content_sha256 == expected_sha256

        assert ir.records[1].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ir.records[1].content_sha256 is None

        for r in ir.records:
            assert r.content_status != CONTENT_STATUS_NOT_ATTEMPTED


# ==============================================================================
# Contract 2: provider_failure envelope must NOT be qualified into empty success
# ==============================================================================

def test_fetch_all_provider_failure_envelope_not_qualified_to_empty_success(mock_sub_tasks):
    """Contract 2: provider_failure envelope must NOT be changed to ok,
    must NOT be converted to empty success, and must NOT produce '确认无公告'.
    """
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    fail_rec = CninfoDisclosureRecord(
        symbol="000001",
        title="未完成抓取的公告",
        announced_at="2026-08-20 09:00:00",
        url="http://cninfo.com.cn/ann/fail",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225999999",
        canonical_event_id="cninfo:1225999999",
        adjunct_url="http://static.cninfo.com.cn/finalpage/fail.pdf",
    )
    fail_env = CninfoDisclosureEnvelope(
        status=STATUS_PROVIDER_FAILURE,
        records=[fail_rec],
        error="cninfo gateway 502 Bad Gateway",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=fail_env), \
         patch.object(cn_provider, "qualify_cninfo_content") as mock_qualify:
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)

        ann = res.get("cninfo_announcements")
        assert ann is not None
        # Must retain STATUS_PROVIDER_FAILURE, never ok or confirmed_empty
        assert ann.status == STATUS_PROVIDER_FAILURE
        assert ann.is_failure is True
        assert not ann.ok
        assert not ann.is_confirmed_empty
        assert ann.error == "cninfo gateway 502 Bad Gateway"

        # qualify_cninfo_content must NOT be called for provider_failure
        assert not mock_qualify.called

        # Records inside provider_failure must not be hashed into fake success
        assert ann.records[0].content_status == CONTENT_STATUS_NOT_ATTEMPTED

        # Strictly forbids containing '确认无公告'
        event_cov = res.get("event_coverage", {})
        recall_text = str(event_cov.get("recall_status", ""))
        assert "确认无公告" not in recall_text


def test_fetch_all_ir_surveys_provider_failure_not_modified(mock_sub_tasks):
    """Contract 2: IR surveys provider_failure envelope is untouched and retains failure."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    fail_env = CninfoDisclosureEnvelope(
        status=STATUS_PROVIDER_FAILURE,
        records=[],
        error="cninfo IR timeout",
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    with patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=fail_env), \
         patch.object(cn_provider, "qualify_cninfo_content") as mock_qualify:
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)

        ir = res.get("cninfo_ir_surveys")
        assert ir is not None
        assert ir.status == STATUS_PROVIDER_FAILURE
        assert ir.is_failure is True
        assert ir.error == "cninfo IR timeout"
        assert not mock_qualify.called


# ==============================================================================
# Contract 3: Missing adjunct_url -> unavailable
# ==============================================================================

def test_fetch_all_missing_adjunct_becomes_unavailable(mock_sub_tasks):
    """Contract 3: Missing adjunct_url strictly results in content_status='unavailable'."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="无附件公告",
        announced_at="2026-08-20 10:00:00",
        url="http://cninfo.com.cn/ann/none",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000088",
        canonical_event_id="cninfo:1225000088",
        adjunct_url=None,  # missing adjunct
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)
        ann = res.get("cninfo_announcements")
        assert ann.records[0].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[0].content_sha256 is None


# ==============================================================================
# Contract 3 & 4: HTTP failure / non-2xx / timeout downgrades to unavailable
# ==============================================================================

def test_fetch_all_qualify_http_403_downgrades_to_unavailable(mock_sub_tasks):
    """Contract 3: HTTP 403 on PDF download downgrades content_status to 'unavailable'."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="受保护公告",
        announced_at="2026-08-20 10:00:00",
        url="http://cninfo.com.cn/ann/403",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000403",
        canonical_event_id="cninfo:1225000403",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225000403.pdf",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    mock_403 = MagicMock()
    mock_403.status_code = 403
    mock_403.content = b"Access Denied"

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch("requests.get", return_value=mock_403):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)
        ann = res.get("cninfo_announcements")
        assert ann.records[0].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[0].content_sha256 is None


def test_fetch_all_qualify_timeout_downgrades_to_unavailable(mock_sub_tasks):
    """Contract 3: Timeout on PDF download downgrades content_status to 'unavailable'."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="下载超时公告",
        announced_at="2026-08-20 10:00:00",
        url="http://cninfo.com.cn/ann/timeout",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000504",
        canonical_event_id="cninfo:1225000504",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225000504.pdf",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch("requests.get", side_effect=requests.Timeout("Connection timed out")):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)
        ann = res.get("cninfo_announcements")
        assert ann.records[0].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[0].content_sha256 is None


# ==============================================================================
# Contract 1: Cutoff date is strictly enforced by qualify
# ==============================================================================

def test_fetch_all_qualify_respects_cutoff_date(mock_sub_tasks):
    """Cutoff date passed from normalized trade_date marks future records unavailable."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    mock_pdf_bytes = b"%PDF-1.4\nmock-future-announcement\n%%EOF"

    rec_future = CninfoDisclosureRecord(
        symbol="000001",
        title="未来披露公告",
        announced_at="2026-08-25 09:00:00",  # past cutoff 2026-08-21
        url="http://cninfo.com.cn/ann/future",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000999",
        canonical_event_id="cninfo:1225000999",
        adjunct_url="http://static.cninfo.com.cn/finalpage/future.pdf",
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec_future],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = mock_pdf_bytes

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch("requests.get", return_value=mock_resp):
        res = _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)
        ann = res.get("cninfo_announcements")
        # Because announced_at > cutoff, qualify_cninfo_content sets cutoff_eligible=False & unavailable
        assert ann.records[0].cutoff_eligible is False
        assert ann.records[0].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ann.records[0].content_sha256 is None


# ==============================================================================
# Contract 1 & Shared Provider: Calls qualify on the shared provider instance
# ==============================================================================

def test_fetch_all_calls_qualify_on_shared_provider(mock_sub_tasks):
    """Verify _fetch_all calls cn_provider.qualify_cninfo_content with cutoff=norm_trade_date."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="测试公告",
        announced_at="2026-08-20 09:00:00",
        url="http://cninfo.com.cn/ann/test",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225000777",
        canonical_event_id="cninfo:1225000777",
        adjunct_url=None,
    )
    mock_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=mock_env), \
         patch.object(cn_provider, "qualify_cninfo_content", wraps=cn_provider.qualify_cninfo_content) as spy_qualify:
        _fetch_all("000001", "2026-08-21", industry_provider=mock_sub_tasks)

        assert spy_qualify.called
        kwargs = spy_qualify.call_args.kwargs
        assert kwargs.get("cutoff") == "2026-08-21"


# ==============================================================================
# End-to-end: DataCollector.collect integration
# ==============================================================================

def test_data_collector_collect_end_to_end_qualify(mock_sub_tasks):
    """DataCollector.collect carries qualified records and sha256 through to cache pool."""
    cn_provider = _registry.get("cn_akshare")
    assert cn_provider is not None

    mock_pdf = b"%PDF-1.4\nmock-e2e-pdf-bytes-sha256\n%%EOF"
    expected_sha256 = hashlib.sha256(mock_pdf).hexdigest()

    rec_ann = CninfoDisclosureRecord(
        symbol="000001",
        title="2026年半年度报告",
        announced_at="2026-08-20 08:30:00",
        url="http://cninfo.com.cn/ann/e2e",
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff_eligible=True,
        announcement_id="1225888001",
        canonical_event_id="cninfo:1225888001",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-08-20/1225888001.pdf",
    )
    ann_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec_ann],
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
    )

    rec_ir = CninfoDisclosureRecord(
        symbol="000001",
        title="投资者关系调研",
        announced_at="2026-08-20 14:00:00",
        url="http://cninfo.com.cn/ir/e2e",
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff_eligible=True,
        announcement_id="1225888002",
        canonical_event_id="cninfo:1225888002",
        adjunct_url=None,
    )
    ir_env = CninfoDisclosureEnvelope(
        status=STATUS_OK,
        records=[rec_ir],
        source_type=SOURCE_TYPE_IR_SURVEY,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = mock_pdf

    collector = DataCollector(industry_linkage_provider=mock_sub_tasks)

    with patch.object(cn_provider, "get_cninfo_announcements", return_value=ann_env), \
         patch.object(cn_provider, "get_cninfo_ir_surveys", return_value=ir_env), \
         patch("requests.get", return_value=mock_resp), \
         patch.object(collector, "_fetch_social_context", return_value={}):
        pool = collector.collect("000001", "2026-08-21")

        ann_res = pool.get("cninfo_announcements")
        assert ann_res is not None
        assert ann_res.records[0].content_status == CONTENT_STATUS_HASHED
        assert ann_res.records[0].content_sha256 == expected_sha256

        ir_res = pool.get("cninfo_ir_surveys")
        assert ir_res is not None
        assert ir_res.records[0].content_status == CONTENT_STATUS_UNAVAILABLE
        assert ir_res.records[0].content_sha256 is None
