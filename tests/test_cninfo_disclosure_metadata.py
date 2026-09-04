"""Tests for C-05a: Cninfo disclosure and IR metadata structured ingestion.

Validates:
1. Mock IR survey DF: title, time, announcementId query param, cutoff eligibility.
2. Mock disclosure DF: 601138 repurchase and interim reports pinned by title/time/ID.
3. Mock disclosure DF: 002415 equity distribution report.
4. Mock AKShare KeyError on empty category: classified as provider_failure, NOT confirmed_empty.
5. Missing announcementId in column and URL: canonical_event_id is None, no fallback hash.
6. Missing or unparseable time: row discarded, no wall-clock fallback.
7. Confirmed empty vs provider failure on schema mismatch.
8. AKSHARE_CALL_LOCK concurrency lock wrapping.
"""
from __future__ import annotations

from datetime import datetime
import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tradingagents.dataflows.cninfo_disclosure import (
    DISCLAIMER_TEXT,
    SOURCE_TYPE_ANNOUNCEMENT,
    SOURCE_TYPE_IR_SURVEY,
    STATUS_CONFIRMED_EMPTY,
    STATUS_OK,
    STATUS_PROVIDER_FAILURE,
    CninfoDisclosureEnvelope,
    CninfoDisclosureRecord,
    extract_announcement_id,
    parse_cninfo_disclosure_df,
)
from tradingagents.dataflows.providers.cn_akshare_provider import (
    AKSHARE_CALL_LOCK,
    CnAkshareProvider,
)


def _sample_ir_df() -> pd.DataFrame:
    """Mock IR survey DataFrame matching ak.stock_zh_a_disclosure_relation_cninfo output."""
    return pd.DataFrame(
        [
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "2026年7月26日至8月21日投资者关系活动记录表",
                "公告时间": "2026-08-21 07:28:15",
                "公告链接": (
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=000001&announcementId=1225488095&"
                    "orgId=gssz0000001&announcementTime=2026-08-21"
                ),
            }
        ]
    )


def _sample_disclosure_df_601138() -> pd.DataFrame:
    """Mock announcement DataFrame matching ak.stock_zh_a_disclosure_report_cninfo output."""
    return pd.DataFrame(
        [
            {
                "代码": "601138",
                "简称": "工业富联",
                "公告标题": "关于以集中竞价交易方式回购公司股份的回购报告书",
                "公告时间": "2026-07-28 17:30:00",
                "公告链接": (
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=601138&announcementId=1220000001&"
                    "orgId=9900034873&announcementTime=2026-07-28"
                ),
            },
            {
                "代码": "601138",
                "简称": "工业富联",
                "公告标题": "2026年半年度报告",
                "公告时间": "2026-08-12 18:00:00",
                "公告链接": (
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=601138&announcementId=1220000002&"
                    "orgId=9900034873&announcementTime=2026-08-12"
                ),
            },
        ]
    )


def _sample_disclosure_df_002415() -> pd.DataFrame:
    """Mock announcement DataFrame for 002415 equity distribution."""
    return pd.DataFrame(
        [
            {
                "代码": "002415",
                "简称": "海康威视",
                "公告标题": "2025年年度权益分派实施公告",
                "公告时间": "2026-08-12 08:30:00",
                "公告链接": (
                    "http://www.cninfo.com.cn/new/disclosure/detail?"
                    "stockCode=002415&announcementId=1220000003&"
                    "orgId=9900012345&announcementTime=2026-08-12"
                ),
            }
        ]
    )


def test_red_1_mock_ir_survey_record():
    """RED 1: Mock 调研 DF: 标题、时间、链接含 announcementId、cutoff 判定."""
    df = _sample_ir_df()
    envelope = parse_cninfo_disclosure_df(
        df,
        source_type=SOURCE_TYPE_IR_SURVEY,
        cutoff="2026-08-24",
    )

    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 1
    rec = envelope.records[0]

    assert rec.source_type == "cninfo_ir_survey"
    assert rec.canonical_event_id == "cninfo:1225488095"
    assert rec.announcement_id == "1225488095"
    assert rec.cutoff_eligible is True
    assert rec.symbol == "000001"
    assert rec.title == "2026年7月26日至8月21日投资者关系活动记录表"
    assert rec.announced_at == "2026-08-21 07:28:15"
    assert "1225488095" in (rec.url or "")


def test_red_2_mock_disclosure_pinned_no_positional_slice():
    """RED 2: 工业富联 601138 含 2026-07-28 回购报告与 2026-08-12 半年报（钉住标题/时间/ID，不用位置切片）."""
    df = _sample_disclosure_df_601138()
    envelope = parse_cninfo_disclosure_df(
        df,
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff="2026-08-01",
    )

    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 2

    # Pinned by title and ID — strictly no positional slicing (records[0]/records[1])
    buyback_records = [
        r for r in envelope.records
        if "回购报告书" in r.title and r.announcement_id == "1220000001"
    ]
    assert len(buyback_records) == 1
    r_buyback = buyback_records[0]
    assert r_buyback.symbol == "601138"
    assert r_buyback.announced_at == "2026-07-28 17:30:00"
    assert r_buyback.canonical_event_id == "cninfo:1220000001"
    assert r_buyback.source_type == "cninfo_announcement"
    assert r_buyback.cutoff_eligible is True  # 2026-07-28 <= 2026-08-01 23:59:59.999999

    interim_records = [
        r for r in envelope.records
        if "半年度报告" in r.title and r.announcement_id == "1220000002"
    ]
    assert len(interim_records) == 1
    r_interim = interim_records[0]
    assert r_interim.symbol == "601138"
    assert r_interim.announced_at == "2026-08-12 18:00:00"
    assert r_interim.canonical_event_id == "cninfo:1220000002"
    assert r_interim.source_type == "cninfo_announcement"
    assert r_interim.cutoff_eligible is False  # 2026-08-12 > 2026-08-01


def test_red_3_mock_disclosure_equity_distribution():
    """RED 3: 海康 002415、分类权益分派、2026-08-12 权益分派实施公告."""
    df = _sample_disclosure_df_002415()
    envelope = parse_cninfo_disclosure_df(
        df,
        source_type=SOURCE_TYPE_ANNOUNCEMENT,
        cutoff="2026-08-20",
    )

    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 1
    rec = envelope.records[0]
    assert rec.symbol == "002415"
    assert "权益分派" in rec.title
    assert rec.announced_at == "2026-08-12 08:30:00"
    assert rec.canonical_event_id == "cninfo:1220000003"
    assert rec.announcement_id == "1220000003"
    assert rec.cutoff_eligible is True


def test_red_4_keyerror_is_provider_failure_not_confirmed_empty():
    """RED 4: Mock AKShare 在无结果分类抛 KeyError -> 不是 confirmed_empty，而是 provider_failure."""
    ak = MagicMock()
    ak.stock_zh_a_disclosure_report_cninfo.side_effect = KeyError(
        "['代码', '简称', '公告标题', '公告时间', 'announcementId', 'orgId'] not in index"
    )

    provider = CnAkshareProvider()
    provider._ak = lambda: ak

    envelope = provider.get_cninfo_announcements(
        symbol="000001",
        start_date="2026-07-01",
        end_date="2026-08-01",
        category="配股",
    )

    assert envelope.status == STATUS_PROVIDER_FAILURE
    assert envelope.status != STATUS_CONFIRMED_EMPTY
    assert len(envelope.records) == 0
    assert "KeyError" in (envelope.error or "")


def test_red_5_missing_announcement_id_canonical_event_id_is_none():
    """RED 5: 无 announcementId 且 URL 也解析不出 -> canonical_event_id is None，严禁用 hash 填."""
    df = pd.DataFrame(
        [
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "关于某事项的说明公告",
                "公告时间": "2026-08-01 09:00:00",
                "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=000001",
            }
        ]
    )
    envelope = parse_cninfo_disclosure_df(df, source_type=SOURCE_TYPE_ANNOUNCEMENT)
    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 1
    rec = envelope.records[0]
    assert rec.announcement_id is None
    assert rec.canonical_event_id is None


def test_red_6_missing_or_unparseable_announced_at_discarded_no_wallclock(caplog):
    """RED 6: 缺公告时间或无法解析 -> 丢弃该行，不填 wall-clock/today."""
    df = pd.DataFrame(
        [
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "有效时间公告",
                "公告时间": "2026-08-21 07:28:15",
                "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1001",
            },
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "缺失时间公告",
                "公告时间": None,
                "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1002",
            },
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "非法时间公告",
                "公告时间": "invalid_date_time_format",
                "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1003",
            },
            {
                "代码": "000001",
                "简称": "平安银行",
                "公告标题": "空字符串时间公告",
                "公告时间": "   ",
                "公告链接": "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1004",
            },
        ]
    )

    with caplog.at_level(logging.WARNING):
        envelope = parse_cninfo_disclosure_df(df, source_type=SOURCE_TYPE_ANNOUNCEMENT)

    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 1
    valid_rec = envelope.records[0]
    assert valid_rec.title == "有效时间公告"
    assert valid_rec.announcement_id == "1001"

    # None of the records have today's date substituted
    today_str = datetime.now().strftime("%Y-%m-%d")
    for r in envelope.records:
        assert r.announced_at == "2026-08-21 07:28:15"


def test_confirmed_empty_when_dataframe_has_expected_columns_but_no_rows():
    """带齐列名的空 DataFrame 确认为 confirmed_empty."""
    df = pd.DataFrame(columns=["代码", "简称", "公告标题", "公告时间", "公告链接"])
    envelope = parse_cninfo_disclosure_df(df, source_type=SOURCE_TYPE_ANNOUNCEMENT)
    assert envelope.status == STATUS_CONFIRMED_EMPTY
    assert len(envelope.records) == 0


def test_provider_failure_when_dataframe_missing_required_columns():
    """缺列或完全无列的空 DataFrame 为 provider_failure (adapter 结构异常)."""
    df_empty_no_cols = pd.DataFrame()
    envelope = parse_cninfo_disclosure_df(df_empty_no_cols, source_type=SOURCE_TYPE_ANNOUNCEMENT)
    assert envelope.status == STATUS_PROVIDER_FAILURE
    assert "missing required columns" in (envelope.error or "")

    df_missing_time = pd.DataFrame([{"代码": "000001", "公告标题": "测试公告"}])
    envelope2 = parse_cninfo_disclosure_df(df_missing_time, source_type=SOURCE_TYPE_ANNOUNCEMENT)
    assert envelope2.status == STATUS_PROVIDER_FAILURE


def test_disclaimer_and_existence_only_semantics():
    """标题级元数据只能证明事件存在，不得据此下财务或经营结论."""
    df = _sample_ir_df()
    envelope = parse_cninfo_disclosure_df(df, source_type=SOURCE_TYPE_IR_SURVEY)
    assert "只能证明事件存在" in envelope.disclaimer or "仅用于证明" in envelope.disclaimer
    assert "不得据此下财务或经营结论" in envelope.disclaimer


def test_provider_method_akshare_call_lock_wrapped():
    """验证 CnAkshareProvider 中接口调用由 AKSHARE_CALL_LOCK 保护."""
    ak = MagicMock()
    ak.stock_zh_a_disclosure_relation_cninfo.return_value = _sample_ir_df()

    provider = CnAkshareProvider()
    provider._ak = lambda: ak

    lock_entered = False
    orig_enter = AKSHARE_CALL_LOCK.__class__.__enter__

    def tracked_enter(self_lock):
        nonlocal lock_entered
        lock_entered = True
        return orig_enter(self_lock)

    with patch.object(AKSHARE_CALL_LOCK.__class__, "__enter__", tracked_enter):
        envelope = provider.get_cninfo_ir_surveys("000001", "2026-07-01", "2026-08-25")

    assert lock_entered is True
    assert envelope.status == STATUS_OK
    assert len(envelope.records) == 1
