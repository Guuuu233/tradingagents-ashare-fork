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
    cluster_news_evidences,
    cninfo_record_to_evidence,
    compute_source_hash,
    extract_url_from_raw,
    format_event_coverage_summary,
    normalize_url,
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


def test_url_deduplication_different_source_and_differing_titles():
    """DAV-610 RED acceptance: same normalized URL, different sources, non-identical titles -> hit_count==1."""
    raw_items = [
        {
            "title": "东财快讯：重组取得重大进展",
            "published_at": "2026-07-29 10:00:00",
            "source": "东方财富",
            "url": "https://finance.example.com/article/12345#ref1",
            "summary": "重组进展详细内容...",
            "entity": "000001",
            "theme": "公司治理",
        },
        {
            "title": "突发！某公司资本运作迎来新突破",
            "published_at": "2026-07-29 11:00:00",
            "source": "新浪财经",
            "url": "https://finance.example.com/article/12345",
            "summary": "资本运作内容完全不一样的摘要说明...",
            "entity": "000001",
            "theme": "公司治理",
        },
    ]

    coverage = build_news_event_coverage(
        raw_items,
        cutoff="2026-07-30",
        requested_themes=["公司治理"],
    )

    assert coverage["hit_count"] == 1
    assert coverage["valid_evidence_count"] == 2
    assert len(coverage["clusters"]) == 1
    cluster = coverage["clusters"][0]
    assert cluster["evidence_count"] == 2
    # Ensure URL is properly normalized on evidences
    for ev in cluster["evidences"]:
        assert ev["url"] == "https://finance.example.com/article/12345"
        # DAV-610 forbidden: no canonical_event_id invented
        assert "canonical_event_id" not in ev
    assert "canonical_event_id" not in cluster
    # DAV-612: Cross-source hash alignment when normalized URL is present
    assert cluster["evidences"][0]["source_hash"] == cluster["evidences"][1]["source_hash"]
    assert len(cluster["source_hashes"]) == 1


def test_normalize_url_trim_and_strip_fragment():
    """DAV-610: URL normalization trims whitespace, strips fragments, and returns None on failure."""
    assert normalize_url("  https://example.com/news/100  ") == "https://example.com/news/100"
    assert normalize_url("https://example.com/news/100#comments") == "https://example.com/news/100"
    assert normalize_url("https://example.com/news?id=123&sort=desc#frag") == "https://example.com/news?id=123&sort=desc"
    # Unparseable / missing / falsy returns None, never empty string or current date
    assert normalize_url(None) is None
    assert normalize_url("") is None
    assert normalize_url("   ") is None
    assert normalize_url("nan") is None
    assert normalize_url("null") is None
    assert normalize_url("未知") is None
    assert normalize_url("http://[invalid-ipv6") is None
    assert normalize_url("http://example.com:999999/") is None


def test_extract_url_from_raw_column_keys():
    """DAV-610: extract_url_from_raw checks url/链接/link/新闻链接 and returns None if missing."""
    assert extract_url_from_raw({"url": "https://example.com/1"}) == "https://example.com/1"
    assert extract_url_from_raw({"链接": "https://example.com/2"}) == "https://example.com/2"
    assert extract_url_from_raw({"link": "https://example.com/3"}) == "https://example.com/3"
    assert extract_url_from_raw({"新闻链接": "https://example.com/4"}) == "https://example.com/4"


# ==============================================================================
# C-05c / DAV-625 Acceptance Tests: canonical_event_id cross-source clustering
# ==============================================================================

def test_c05c_red1_canonical_event_id_cross_source_same_id_one_cluster():
    """RED 1 (C-05c / DAV-625):
    两条 NewsEvidence：标题不同、URL 不同，但 canonical_event_id 同为 cninfo:1225488095 → 一个 cluster。
    """
    ev1 = NewsEvidence(
        title="东财：某公司签订战略合作框架协议",
        published_at="2026-07-29 10:00:00",
        source="东方财富",
        url="https://finance.eastmoney.com/a/202607290001.html",
        entity="000001",
        theme="重大合同",
        canonical_event_id="cninfo:1225488095",
    )
    ev2 = NewsEvidence(
        title="巨潮公告：关于签署重大项目投资意向书的提示性公告",
        published_at="2026-07-29 15:30:00",
        source="cninfo_announcement",
        url="http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1225488095",
        entity="000001",
        theme="公司治理",
        canonical_event_id="cninfo:1225488095",
    )
    clusters = cluster_news_evidences([ev1, ev2])
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.evidence_count == 2
    assert cluster.canonical_event_id == "cninfo:1225488095"
    assert cluster.to_dict()["canonical_event_id"] == "cninfo:1225488095"


def test_c05c_red2_differing_canonical_event_ids_two_clusters():
    """RED 2 (C-05c / DAV-625):
    两条标题极相似、时间接近，id 分别为 cninfo:1 与 cninfo:2 → 两个 cluster。
    不等的 id 不得因标题模糊匹配并成一簇。
    """
    ev1 = NewsEvidence(
        title="某公司关于重大合同的公告",
        published_at="2026-07-29 10:00:00",
        source="东方财富",
        entity="000001",
        theme="重大合同",
        canonical_event_id="cninfo:1",
    )
    ev2 = NewsEvidence(
        title="某公司关于重大合同的公告（更新）",
        published_at="2026-07-29 10:05:00",
        source="证券时报",
        entity="000001",
        theme="重大合同",
        canonical_event_id="cninfo:2",
    )
    clusters = cluster_news_evidences([ev1, ev2])
    assert len(clusters) == 2
    c_ids = {c.canonical_event_id for c in clusters}
    assert c_ids == {"cninfo:1", "cninfo:2"}
    for c in clusters:
        assert c.evidence_count == 1
        assert c.to_dict()["canonical_event_id"] in ("cninfo:1", "cninfo:2")


def test_c05c_red3_parse_news_markdown_canonical_event_id_none():
    """RED 3 (C-05c / DAV-625):
    markdown 解析路径仍无 canonical_event_id 键或值为 None。
    DAV-610 测试「parse_news_markdown 不得发明 id」仍成立：解析媒体 markdown 不得用标题/source_hash 填 id。
    """
    markdown_text = """### 东方财富：某公司签订重大战略合作协议 [发布时间：2026-07-29 10:00:00] (source: 东方财富)
公司签署重大合作。
Link: https://finance.eastmoney.com/a/202607290001.html
"""
    evidences, unparseable = parse_news_markdown_to_evidences(markdown_text, default_entity="000001")
    assert len(evidences) == 1
    ev = evidences[0]
    # Dataclass attribute is None
    assert ev.canonical_event_id is None
    # to_dict() has no canonical_event_id key or value is None
    ev_dict = ev.to_dict()
    assert "canonical_event_id" not in ev_dict or ev_dict["canonical_event_id"] is None

    # Clustering markdown-only evidences: cluster echo is also absent / None
    clusters = cluster_news_evidences(evidences)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.canonical_event_id is None
    c_dict = c.to_dict()
    assert "canonical_event_id" not in c_dict or c_dict["canonical_event_id"] is None


def test_c05c_contract4_one_side_has_id_merges_without_inventing_id():
    """Contract 4 (C-05c / DAV-625):
    仅一侧有 id：不得为另一侧编造 id；仍可走现有 URL/source_hash/标题规则。
    """
    ev_media = NewsEvidence(
        title="关于某公司重大投资的公告",
        published_at="2026-07-29 10:00:00",
        source="新浪财经",
        url="https://finance.example.com/item/999",
        entity="000001",
        theme="公司治理",
        canonical_event_id=None,
    )
    ev_cninfo = NewsEvidence(
        title="关于某公司重大投资的公告",
        published_at="2026-07-29 10:02:00",
        source="cninfo_announcement",
        url="http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1225488095",
        entity="000001",
        theme="公司治理",
        canonical_event_id="cninfo:1225488095",
    )
    clusters = cluster_news_evidences([ev_media, ev_cninfo])
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.evidence_count == 2
    assert cluster.canonical_event_id == "cninfo:1225488095"

    # Verify no id was invented for ev_media
    media_ev = next(e for e in cluster.evidences if e.source == "新浪财经")
    assert media_ev.canonical_event_id is None
    assert "canonical_event_id" not in media_ev.to_dict()

    cninfo_ev = next(e for e in cluster.evidences if e.source == "cninfo_announcement")
    assert cninfo_ev.canonical_event_id == "cninfo:1225488095"
    assert cninfo_ev.to_dict()["canonical_event_id"] == "cninfo:1225488095"


def test_c05c_cninfo_record_to_evidence_copies_verbatim():
    """Contract 2 (C-05c / DAV-625):
    只有调用方把巨潮 CninfoDisclosureRecord.canonical_event_id 原样拷到 evidence 时才有值。
    """
    from tradingagents.dataflows.cninfo_disclosure import CninfoDisclosureRecord

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="平安银行：2026年半年度报告",
        announced_at="2026-07-28 17:00:00",
        url="http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1225488095",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
        adjunct_url="http://static.cninfo.com.cn/finalpage/2026-07-28/1225488095.PDF",
    )
    ev = cninfo_record_to_evidence(rec, default_entity="000001")
    assert ev is not None
    assert ev.canonical_event_id == "cninfo:1225488095"
    assert ev.title == "平安银行：2026年半年度报告"
    assert ev.published_at == "2026-07-28 17:00:00"
    assert ev.source == "cninfo_announcement"
    assert ev.entity == "000001"
    assert ev.url == "http://www.cninfo.com.cn/new/disclosure/detail?announcementId=1225488095"


def test_c05c_build_news_event_coverage_with_cninfo_record():
    """build_news_event_coverage correctly integrates CninfoDisclosureRecord and clusters with media news."""
    from tradingagents.dataflows.cninfo_disclosure import CninfoDisclosureRecord

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="平安银行：关于重大合作的公告",
        announced_at="2026-07-29 10:00:00",
        url="http://www.cninfo.com.cn/detail?announcementId=1225488095",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    media_item = {
        "title": "平安银行：关于重大合作的公告",
        "published_at": "2026-07-29 10:05:00",
        "source": "东方财富",
        "url": "https://finance.eastmoney.com/a/123.html",
        "entity": "000001",
        "theme": "重大合同",
    }
    coverage = build_news_event_coverage(
        [rec, media_item],
        cutoff="2026-07-30",
        requested_themes=["重大合同"],
    )
    assert coverage["hit_count"] == 1
    assert coverage["valid_evidence_count"] == 2
    cluster = coverage["clusters"][0]
    assert cluster["evidence_count"] == 2
    assert cluster["canonical_event_id"] == "cninfo:1225488095"
    media_ev = next(e for e in cluster["evidences"] if e["source"] == "东方财富")
    assert "canonical_event_id" not in media_ev


def test_c05c_data_collector_cninfo_records_copied_to_event_coverage():
    """data_collector copies already-fetched cninfo records' canonical_event_id to event_coverage."""
    from tradingagents.dataflows.cninfo_disclosure import CninfoDisclosureRecord, CninfoDisclosureEnvelope

    rec = CninfoDisclosureRecord(
        symbol="000001",
        title="平安银行关于重大重组的公告",
        announced_at="2026-07-29 09:30:00",
        url="http://www.cninfo.com.cn/detail?announcementId=1225488095",
        source_type="cninfo_announcement",
        cutoff_eligible=True,
        announcement_id="1225488095",
        canonical_event_id="cninfo:1225488095",
    )
    envelope = CninfoDisclosureEnvelope(status="ok", records=[rec])

    mock_results = {
        "news": "### 东方财富：平安银行关于重大重组的公告 [发布时间：2026-07-29 10:00:00] (source: 东方财富)\n重组内容\n",
        "global_news": "",
        "cninfo_announcements": envelope,
    }
    ticker = "000001"
    trade_date = "2026-07-30"

    stock_evs, stock_unp = parse_news_markdown_to_evidences(mock_results["news"], default_entity=ticker)
    glob_evs, glob_unp = parse_news_markdown_to_evidences(mock_results["global_news"], default_entity="宏观/行业")
    cninfo_evs = [cninfo_record_to_evidence(r, default_entity=ticker) for r in envelope.records]
    cov = build_news_event_coverage(
        stock_evs + glob_evs + cninfo_evs + stock_unp + glob_unp,
        cutoff=trade_date,
        default_entity=ticker,
    )
    assert cov["hit_count"] == 1
    assert cov["clusters"][0]["canonical_event_id"] == "cninfo:1225488095"
    assert cov["clusters"][0]["evidence_count"] == 2
    assert extract_url_from_raw({"title": "无链接新闻"}) is None
    assert extract_url_from_raw({"url": "", "link": "nan"}) is None


def test_compute_source_hash_incorporates_url():
    """DAV-610: compute_source_hash incorporates normalized URL when present, keeps legacy without URL."""
    base_hash = compute_source_hash("东财", "标题", "2026-07-29 10:00:00", "摘要")
    # Calling without url or with None must yield exact legacy hash
    assert compute_source_hash("东财", "标题", "2026-07-29 10:00:00", "摘要", url=None) == base_hash
    assert compute_source_hash("东财", "标题", "2026-07-29 10:00:00", "摘要", url="") == base_hash

    # URL presence alters the hash
    hash_with_url = compute_source_hash("东财", "标题", "2026-07-29 10:00:00", "摘要", url="https://example.com/1")
    assert hash_with_url != base_hash

    # Fragment does not alter the hash because of normalization
    hash_with_frag = compute_source_hash("东财", "标题", "2026-07-29 10:00:00", "摘要", url="https://example.com/1#section")
    assert hash_with_frag == hash_with_url

    # DAV-612: Same normalized URL across different sources produces IDENTICAL source_hash
    hash_diff_src = compute_source_hash("新浪", "标题", "2026-07-29 10:00:00", "摘要", url="https://example.com/1")
    assert hash_diff_src == hash_with_url

    # DAV-612: Without URL, different sources still produce different hashes
    hash_no_url_diff_src = compute_source_hash("新浪", "标题", "2026-07-29 10:00:00", "摘要")
    assert hash_no_url_diff_src != base_hash


def test_parse_news_markdown_extracts_link_url():
    """DAV-610: parse_news_markdown_to_evidences extracts Link: as normalized url."""
    md = """### 标题1 [发布时间：2026-07-29 10:00:00] (source: 源1)
内容摘要
Link: https://example.com/item/1#frag

### 标题2 [发布时间：2026-07-29 11:00:00] (source: 源2)
内容摘要无链接
"""
    evidences, unparseable = parse_news_markdown_to_evidences(md)
    assert len(evidences) == 2
    assert evidences[0].url == "https://example.com/item/1"
    assert evidences[1].url is None
