"""Tests for NewsEvidence, EventCluster, and event_coverage structured semantics.

Implements D-009 / P1-1 requirements:
1. Strict publication timestamp verification (no ingest/today fallback).
2. Anti-lookahead cutoff enforcement (R2 nail: 7/29 visible, 8/11 future invisible).
3. Deduplication and clustering of near-duplicate news items.
4. Fail-closed data gap semantics (suspected_gaps must state "未检索到/不可验证",
   never "确认无相关新闻").
5. Integration with news_analyst state and compact prompt injection.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradingagents.dataflows.news_event_evidence import (
    EventCluster,
    NewsEvidence,
    build_news_event_coverage,
    format_event_coverage_summary,
    parse_news_markdown_to_evidences,
)
from tradingagents.prompts import get_prompt


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "news_events" / "r2_news_fixture.json"


def test_published_at_missing_or_unparseable_rejected_and_unverifiable():
    """Missing or invalid published_at must be rejected and counted as unverifiable."""
    raw_items = [
        {
            "title": "某未标注发布时间的新闻",
            "published_at": None,
            "theme": "行业政策",
            "entity": "000001",
            "source": "未知源",
            "summary": "无时间戳内容",
        },
        {
            "title": "格式混乱的发布时间新闻",
            "published_at": "bad-datetime-str",
            "theme": "行业政策",
            "entity": "000001",
            "source": "未知源",
            "summary": "时间戳不可解析",
        },
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=["行业政策"],
    )

    assert coverage["unverifiable_count"] == 2
    assert coverage["hit_count"] == 0
    assert len(coverage["hit_cluster_ids"]) == 0
    assert len(coverage["suspected_gaps"]) == 1
    gap = coverage["suspected_gaps"][0]
    assert gap["theme"] == "行业政策"
    assert "未检索到/不可验证" in gap["message"]
    assert "确认无相关新闻" not in gap["message"]
    assert "确认无新闻" not in gap["message"]


def test_first_seen_at_cannot_substitute_published_at():
    """first_seen_at is audit metadata only; must NOT qualify an item as historically known."""
    raw_items = [
        {
            "title": "仅有抓取时间无发布时间新闻",
            "published_at": None,
            "first_seen_at": "2026-07-29 10:00:00",
            "theme": "跨市场",
            "entity": "000001",
            "source": "采集器",
            "summary": "内容",
        }
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=["跨市场"],
    )

    assert coverage["unverifiable_count"] == 1
    assert coverage["hit_count"] == 0
    assert len(coverage["hit_cluster_ids"]) == 0


def test_future_published_at_after_cutoff_rejected():
    """Items published after cutoff (lookahead) must be strictly rejected."""
    raw_items = [
        {
            "title": "未来发布的半年报",
            "published_at": "2026-08-11 08:30:00",
            "theme": "财报",
            "entity": "000001",
            "source": "证券时报",
            "summary": "半年报业绩披露",
        }
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=["财报"],
    )

    assert coverage["hit_count"] == 0
    assert coverage["future_rejected_count"] == 1
    assert len(coverage["suspected_gaps"]) == 1
    assert "未检索到/不可验证" in coverage["suspected_gaps"][0]["message"]


def test_cutoff_prior_events_hit_and_duplicate_clustering():
    """Valid events <= cutoff are hits; near-duplicates collapse into a single cluster."""
    raw_items = [
        {
            "title": "公司签署重大战略合作协议",
            "published_at": "2026-07-29 14:00:00",
            "theme": "重大合同",
            "entity": "600519",
            "source": "上证报",
            "summary": "公司与某集团签署重大战略合作协议，合作金额达百亿。",
        },
        {
            "title": "公司签署重大战略合作协议（更新）",
            "published_at": "2026-07-29 14:30:00",
            "theme": "重大合同",
            "entity": "600519",
            "source": "证券时报",
            "summary": "公司与某集团签署重大战略合作协议，合作金额达百亿。",
        },
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=["重大合同"],
    )

    assert coverage["hit_count"] == 1
    assert len(coverage["hit_cluster_ids"]) == 1
    assert coverage["unverifiable_count"] == 0
    assert coverage["valid_evidence_count"] == 2
    assert len(coverage["clusters"]) == 1
    cluster = coverage["clusters"][0]
    assert cluster["evidence_count"] == 2
    assert cluster["theme"] == "重大合同"
    assert len(coverage["suspected_gaps"]) == 0


def test_r2_fixture_acceptance():
    """R2 nail acceptance test using offline JSON fixture.

    Nail criteria:
    - 2026-07-29 event: VISIBLE, enters coverage hit (2 near-duplicates -> 1 cluster)
    - 2026-08-11 event: INVISIBLE (future rejected)
    - Corrupted / null dates: UNVERIFIABLE (unverifiable_count == 2)
    - Requested themes without hit: marked as suspected_gaps ("未检索到/不可验证", NOT "确认无相关新闻")
    """
    assert FIXTURE_PATH.exists(), f"Fixture file not found: {FIXTURE_PATH}"
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        fixture_data = json.load(f)

    cutoff = fixture_data["cutoff"]
    window = fixture_data["window"]
    requested_themes = fixture_data["requested_themes"]
    items = fixture_data["items"]
    entity = fixture_data.get("entity", "")

    coverage = build_news_event_coverage(
        items,
        cutoff=cutoff,
        window=window,
        requested_themes=requested_themes,
        default_entity=entity,
    )

    assert coverage["cutoff"] == "2026-07-30"
    assert coverage["hit_count"] == 1
    assert len(coverage["hit_cluster_ids"]) == 1
    assert coverage["unverifiable_count"] == 2
    assert coverage["future_rejected_count"] == 1
    assert coverage["valid_evidence_count"] == 2

    # Check that the single hit cluster is for theme "跨市场"
    hit_cluster = coverage["clusters"][0]
    assert hit_cluster["theme"] == "跨市场"
    assert hit_cluster["evidence_count"] == 2

    # Check suspected_gaps: "财报" (only future news) and "行业政策" (0 items)
    gap_themes = [g["theme"] for g in coverage["suspected_gaps"]]
    assert "财报" in gap_themes
    assert "行业政策" in gap_themes
    assert "跨市场" not in gap_themes

    for gap in coverage["suspected_gaps"]:
        assert "未检索到/不可验证" in gap["message"]
        assert "确认无相关新闻" not in gap["message"]
        assert "确认无新闻" not in gap["message"]


def test_suspected_gaps_semantics_cannot_claim_confirmed_no_news():
    """Zero hits must be worded as '未检索到/不可验证', strictly forbidding '确认无相关新闻'."""
    coverage = build_news_event_coverage(
        [],
        cutoff="2026-07-30",
        requested_themes=["宏观政策", "行业新政"],
    )

    assert coverage["hit_count"] == 0
    assert len(coverage["suspected_gaps"]) == 2

    summary_text = format_event_coverage_summary(coverage)
    assert "未检索到/不可验证" in summary_text
    assert "确认无相关新闻" not in summary_text
    assert "确认无新闻" not in summary_text


def test_parse_news_markdown_to_evidences():
    """Test extracting NewsEvidence instances from markdown formatted news output."""
    markdown_text = """## 002167 新闻（2026-07-16 至 2026-07-30；最新发布时间：2026-07-29 15:00:00）：

### 东方财富：某重大半导体合作落地 [发布时间：2026-07-29 10:00:00] (source: 东方财富)
东方财富公告称某重大半导体合作落地，协同全球供应链。
Link: https://example.com/news/1

### 证券时报：半年报净利润增长预告 [发布时间：2026-08-11 09:00:00] (source: 证券时报)
半年报预计净利润增长50%以上。

### 传闻信息 [发布时间：未知] (source: Unknown)
无明确时间信息的内容。
"""
    evidences, unparseable = parse_news_markdown_to_evidences(
        markdown_text,
        default_entity="002167",
    )

    assert len(evidences) == 2
    assert len(unparseable) == 1
    assert evidences[0].title == "东方财富：某重大半导体合作落地"
    assert evidences[0].published_at == "2026-07-29 10:00:00"
    assert evidences[0].source == "东方财富"
    assert evidences[0].entity == "002167"
    assert evidences[0].source_hash != ""

    assert evidences[1].published_at == "2026-08-11 09:00:00"


def test_news_analyst_returns_event_coverage_in_state():
    """news_analyst node must return event_coverage dict and include summary in prompt."""
    from tradingagents.agents.analysts.news_analyst import create_news_analyst

    mock_llm = MagicMock()
    mock_chunk = MagicMock()
    mock_chunk.content = "新闻分析报告正文\n<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"数据中性\"} -->"
    mock_chunk.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    async def mock_astream(messages):
        yield mock_chunk

    mock_llm.astream = mock_astream

    mock_collector = {
        ("002167", "2026-07-30"): {
            "news": "### 东方财富：重大合作 [发布时间：2026-07-29 10:00:00] (source: 东方财富)\n合作内容",
            "global_news": "### 全球财经快讯 [发布时间：2026-07-29 11:00:00] (source: 新浪)\n全球宏观动态",
            "_data_window": "14天",
        }
    }

    class MockCollectorObj:
        def get(self, ticker, date):
            return mock_collector.get((ticker, date))

    news_node = create_news_analyst(mock_llm, data_collector=MockCollectorObj())

    state = {
        "trade_date": "2026-07-30",
        "company_of_interest": "002167",
        "user_intent": {"focus_areas": ["跨市场", "财报"]},
    }

    with patch("tradingagents.agents.analysts.news_analyst.get_cn_stock_name", return_value="东方国信"), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_industry_context", return_value=(None, "【行业常识知识库】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_macro_event_context", return_value=(None, "【宏观事件传导图谱】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.log_llm_call"):
        result = asyncio.run(news_node(state))

    assert "news_report" in result
    assert "event_coverage" in result
    coverage = result["event_coverage"]
    assert isinstance(coverage, dict)
    assert coverage["cutoff"] == "2026-07-30"
    assert coverage["hit_count"] >= 1
    assert "suspected_gaps" in coverage


def test_prompt_contains_unverifiable_discipline_rule():
    """Prompt templates must include the D-009 discipline sentence."""
    zh_prompt = get_prompt("news_system_message", config={"prompt_language": "zh"})
    assert "event_coverage 未命中 ≠ 确认无新闻" in zh_prompt or "未命中 ≠ 确认无新闻" in zh_prompt
    assert "不可验证" in zh_prompt

    en_prompt = get_prompt("news_system_message", config={"prompt_language": "en"})
    assert "event_coverage" in en_prompt or "unverifiable" in en_prompt


def test_event_coverage_without_manifest_defaults_to_unknown_recall():
    """DAV-608 RED: requested_themes=None results in recall_status='unknown' and query_manifest=[], no fabricated 5 themes."""
    raw_items = [
        {
            "title": "公司签署重大战略合作协议",
            "published_at": "2026-07-29 14:00:00",
            "theme": "重大合同",
            "entity": "600519",
            "source": "上证报",
            "summary": "重大战略合作协议",
        }
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=None,
    )

    assert coverage["recall_status"] == "unknown"
    assert coverage["query_manifest"] == []
    assert coverage["requested_themes"] == []
    for default_theme in ("跨市场", "财报", "行业政策", "公司治理", "重大合同"):
        assert default_theme not in coverage["requested_themes"]
        assert default_theme not in coverage["query_manifest"]
    # Suspected gaps must not pretend default themes are gaps
    assert coverage["suspected_gaps"] == []

    summary = format_event_coverage_summary(coverage)
    assert "无明显主题缺失" not in summary
    assert "unknown" in summary or "未知" in summary


def test_format_event_coverage_summary_forbids_no_apparent_gap_message():
    """DAV-608 RED: format_event_coverage_summary must NEVER contain '无明显主题缺失' on any code path."""
    # Path 1: Unknown manifest, no gaps
    cov_unknown = build_news_event_coverage(
        [],
        cutoff="2026-07-30",
        requested_themes=None,
    )
    summary_unknown = format_event_coverage_summary(cov_unknown)
    assert "无明显主题缺失" not in summary_unknown

    # Path 2: Explicit manifest where all items hit, so suspected_gaps is empty
    items = [
        {
            "title": "重大合同公告",
            "published_at": "2026-07-29 10:00:00",
            "theme": "重大合同",
            "source": "东财",
            "summary": "重大合同内容",
        }
    ]
    cov_hit = build_news_event_coverage(
        items,
        cutoff="2026-07-30",
        requested_themes=["重大合同"],
    )
    assert len(cov_hit["suspected_gaps"]) == 0
    summary_hit = format_event_coverage_summary(cov_hit)
    assert "无明显主题缺失" not in summary_hit
    assert "全市场查全" not in summary_hit


def test_event_coverage_with_explicit_manifest_has_partial_vs_manifest_status():
    """DAV-608: With explicit requested_themes or query_manifest, recall_status is partial_vs_manifest."""
    items = [
        {
            "title": "半年报业绩披露",
            "published_at": "2026-07-29 10:00:00",
            "theme": "财报",
            "source": "东财",
            "summary": "半年报内容",
        }
    ]
    coverage = build_news_event_coverage(
        items,
        cutoff="2026-07-30",
        requested_themes=["财报", "行业政策"],
    )
    assert coverage["recall_status"] == "partial_vs_manifest"
    assert coverage["query_manifest"] == ["财报", "行业政策"]
    assert coverage["requested_themes"] == ["财报", "行业政策"]
    assert len(coverage["suspected_gaps"]) == 1
    gap = coverage["suspected_gaps"][0]
    assert gap["theme"] == "行业政策"
    assert gap["status"] == "unverified_or_not_found"
    assert "未检索到/不可验证" in gap["message"]
    assert "确认无新闻" not in gap["message"]
    assert "确认无相关新闻" not in gap["message"]


def test_news_analyst_without_focus_areas_does_not_inject_default_themes():
    """DAV-608 RED: news_analyst without focus_areas must not inject 5 themes into coverage manifest."""
    from tradingagents.agents.analysts.news_analyst import create_news_analyst

    mock_llm = MagicMock()
    mock_chunk = MagicMock()
    mock_chunk.content = "新闻分析报告正文\n<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"数据中性\"} -->"
    mock_chunk.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    async def mock_astream(messages):
        yield mock_chunk

    mock_llm.astream = mock_astream

    mock_collector = {
        ("002167", "2026-07-30"): {
            "news": "### 东方财富：重大合作 [发布时间：2026-07-29 10:00:00] (source: 东方财富)\n合作内容",
            "global_news": "### 全球财经快讯 [发布时间：2026-07-29 11:00:00] (source: 新浪)\n全球宏观动态",
            "_data_window": "14天",
        }
    }

    class MockCollectorObj:
        def get(self, ticker, date):
            return mock_collector.get((ticker, date))

    news_node = create_news_analyst(mock_llm, data_collector=MockCollectorObj())

    # user_intent with NO focus_areas
    state = {
        "trade_date": "2026-07-30",
        "company_of_interest": "002167",
        "user_intent": {},
    }

    with patch("tradingagents.agents.analysts.news_analyst.get_cn_stock_name", return_value="东方国信"), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_industry_context", return_value=(None, "【行业常识知识库】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_macro_event_context", return_value=(None, "【宏观事件传导图谱】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.log_llm_call"):
        result = asyncio.run(news_node(state))

    assert "event_coverage" in result
    coverage = result["event_coverage"]
    assert coverage["recall_status"] == "unknown"
    assert coverage["query_manifest"] == []
    assert coverage["requested_themes"] == []
    for default_theme in ("跨市场", "财报", "行业政策", "公司治理", "重大合同"):
        assert default_theme not in coverage["requested_themes"]
        assert default_theme not in coverage["query_manifest"]


def test_news_analyst_with_focus_areas_retains_manifest():
    """DAV-608: news_analyst with explicit focus_areas records query_manifest and partial_vs_manifest status."""
    from tradingagents.agents.analysts.news_analyst import create_news_analyst

    mock_llm = MagicMock()
    mock_chunk = MagicMock()
    mock_chunk.content = "新闻分析报告正文\n<!-- VERDICT: {\"direction\": \"中性\", \"reason\": \"数据中性\"} -->"
    mock_chunk.response_metadata = {"token_usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    async def mock_astream(messages):
        yield mock_chunk

    mock_llm.astream = mock_astream

    mock_collector = {
        ("002167", "2026-07-30"): {
            "news": "### 东方财富：重大合作 [发布时间：2026-07-29 10:00:00] (source: 东方财富)\n合作内容",
            "global_news": "### 全球财经快讯 [发布时间：2026-07-29 11:00:00] (source: 新浪)\n全球宏观动态",
            "_data_window": "14天",
        }
    }

    class MockCollectorObj:
        def get(self, ticker, date):
            return mock_collector.get((ticker, date))

    news_node = create_news_analyst(mock_llm, data_collector=MockCollectorObj())

    # user_intent with focus_areas
    state = {
        "trade_date": "2026-07-30",
        "company_of_interest": "002167",
        "user_intent": {"focus_areas": ["财报", "行业政策"]},
    }

    with patch("tradingagents.agents.analysts.news_analyst.get_cn_stock_name", return_value="东方国信"), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_industry_context", return_value=(None, "【行业常识知识库】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.resolve_macro_event_context", return_value=(None, "【宏观事件传导图谱】\n【知识库未命中】")), \
         patch("tradingagents.agents.analysts.news_analyst.log_llm_call"):
        result = asyncio.run(news_node(state))

    assert "event_coverage" in result
    coverage = result["event_coverage"]
    assert coverage["recall_status"] == "partial_vs_manifest"
    assert coverage["query_manifest"] == ["财报", "行业政策"]
    assert coverage["requested_themes"] == ["财报", "行业政策"]
