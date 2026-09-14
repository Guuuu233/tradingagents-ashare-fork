"""Unit tests for the deterministic analyst-report evidence summary extractor.

DAV-68 M2: adjudicators must be able to anchor verdicts on evidence strength.
``build_evidence_summary`` produces the bounded, fact-dense first-hand excerpts
that research_manager receives instead of full analyst reports.
"""
from __future__ import annotations

from tradingagents.agents.utils.evidence_summary import (
    DEFAULT_MAX_CHARS,
    MAX_SEVEN_SOURCE_TOTAL_CHARS,
    SEVEN_SOURCE_CAPS,
    SEVEN_SOURCE_SPECS,
    build_evidence_summary,
    build_seven_source_evidence_bundle,
    build_seven_source_evidence_summary,
    extract_verdict_direction,
    strip_machine_blocks,
)


def test_empty_report_returns_empty_string():
    # Empty input must produce an empty summary (not a placeholder), so callers
    # can conditionally omit the evidence line instead of injecting a
    # misleading "no content" marker for an analyst that did not run.
    assert build_evidence_summary("") == ""
    assert build_evidence_summary(None) == ""
    assert build_evidence_summary("   \n  ") == ""


def test_direction_extracted_from_verdict_block():
    report = (
        "一些叙述。\n"
        '<!-- VERDICT: {"direction": "偏空", "reason": "资金流出"} -->'
    )
    assert extract_verdict_direction(report) == "偏空"


def test_direction_missing_when_no_verdict():
    assert extract_verdict_direction("没有机读块") == ""


def test_strip_machine_blocks_removes_protocol_blocks():
    report = (
        "正文内容。\n"
        '<!-- VERDICT: {"direction": "看多", "reason": "x"} -->\n'
        '<!-- DEBATE_STATE: {"new_claims": []} -->\n'
    )
    cleaned = strip_machine_blocks(report)
    assert "VERDICT" not in cleaned
    assert "DEBATE_STATE" not in cleaned
    assert "正文内容" in cleaned


def test_summary_keeps_numbers_and_drops_boilerplate():
    report = (
        "# 分析报告标题\n"
        "本文基于公开数据对公司进行基本面分析。\n"
        "净利润同比增长 15%，毛利率 45%。\n"
        "经营现金流为正，资产负债率 38%。\n"
        '<!-- VERDICT: {"direction": "中性", "reason": "估值合理"} -->'
    )
    summary = build_evidence_summary(report)
    assert "净利润同比增长 15%" in summary
    assert "毛利率 45%" in summary
    assert "资产负债率 38%" in summary
    # Boilerplate prose without numbers is not evidence.
    assert "本文基于公开数据" not in summary
    # Direction is prefixed as a labeled fact.
    assert "[分析师结论：中性]" in summary
    # Machine block is gone.
    assert "VERDICT" not in summary


def test_markdown_table_rows_are_flattened():
    report = (
        "指标 | 当前信号 | 交易含义\n"
        "---|--- |--- \n"
        "RSI | 48.2 | 中性\n"
        "MACD | 金叉 | 偏多\n"
        '<!-- VERDICT: {"direction": "偏多", "reason": "技术向好"} -->'
    )
    summary = build_evidence_summary(report)
    assert "RSI" in summary
    assert "48.2" in summary
    # The header row and separator row are dropped, and non-numeric table rows
    # (pure qualitative labels) are not treated as evidence.
    assert "当前信号" not in summary
    assert "---|---" not in summary
    assert "MACD" not in summary
    assert "[分析师结论：偏多]" in summary


def test_summary_respects_char_cap():
    report = "数字填充。" + ("业绩数据 12% 增长 34% 波动 56% 扩张 78% 回落。") * 20
    summary = build_evidence_summary(report)
    assert len(summary) <= DEFAULT_MAX_CHARS + len("[分析师结论：]")
    assert summary.endswith("…")


def test_fallback_excerpt_for_numberless_report():
    report = (
        "该标的缺乏量化数据。\n"
        "分析师观察到情绪偏暖但无法量化。\n"
        '<!-- VERDICT: {"direction": "看多", "reason": "定性看好"} -->'
    )
    summary = build_evidence_summary(report)
    # Even with no numbers, the adjudicator sees the analyst's core content.
    assert "缺乏量化数据" in summary or "情绪偏暖" in summary
    assert "[分析师结论：看多]" in summary


def test_duplicate_lines_deduped():
    report = (
        "净利润同比增长 15%。\n"
        "净利润同比增长 15%。\n"
        '<!-- VERDICT: {"direction": "中性", "reason": "x"} -->'
    )
    summary = build_evidence_summary(report)
    assert summary.count("净利润同比增长 15%") == 1


# ── P1-D: Seven-source shared evidence summary tests ────────────────────────

SAMPLE_SEVEN_REPORTS = {
    "market_report": (
        "市场技术面：RSI 48.2，股价 1835.5 站上 50 日均线，MACD 柱状线金叉。\n"
        '<!-- VERDICT: {"direction": "偏多", "reason": "趋势向上"} -->'
    ),
    "news_report": (
        "新闻：行业政策利好落地，预计增速 12%，新产品订单同比翻倍。\n"
        '<!-- VERDICT: {"direction": "偏多", "reason": "政策驱动"} -->'
    ),
    "fundamentals_report": (
        "基本面：营收同比 +15%，毛利率 45%，资产负债率 38%。\n"
        '<!-- VERDICT: {"direction": "中性", "reason": "估值合理"} -->'
    ),
    "macro_report": (
        "宏观/板块：板块资金净流入 23 亿，美债利率下行。\n"
        '<!-- VERDICT: {"direction": "偏多", "reason": "政策与资金共振"} -->'
    ),
    "sentiment_report": "情绪：中性偏热，散户看多情绪占比 62%，无极端值。",
    "smart_money_report": "主力资金：超大单净流入 5.8 亿，机构席位净买入 3.2 亿。",
    "volume_price_report": "量价：放量突破 20 日均线，日成交量放大 35%。",
}


def test_seven_source_summary_fixed_order_and_labels():
    """All seven sources must be output in fixed order with their dedicated labels."""
    summary = build_seven_source_evidence_summary(SAMPLE_SEVEN_REPORTS)

    expected_labels = [
        "市场技术证据摘要：",
        "新闻证据摘要：",
        "基本面证据摘要：",
        "宏观/板块证据摘要：",
        "情绪证据摘要：",
        "主力资金证据摘要：",
        "量价证据摘要：",
    ]
    lines = summary.splitlines()
    assert len(lines) == 7

    last_pos = -1
    for label in expected_labels:
        pos = summary.find(label)
        assert pos != -1, f"Missing label {label} in summary"
        assert pos > last_pos, f"Label {label} out of order (pos={pos}, last_pos={last_pos})"
        last_pos = pos


def test_seven_source_summary_individual_hard_caps():
    """Each non-empty source entry must strictly obey its individual hard cap."""
    overflow_reports = {
        "market_report": "市场技术面数据 " + ("RSI 48.2 股价 1835.5 均线走平 ") * 30 + '\n<!-- VERDICT: {"direction": "偏多"} -->',
        "news_report": "新闻政策数据 " + ("行业增速 12% 订单放量 25% ") * 30 + '\n<!-- VERDICT: {"direction": "偏多"} -->',
        "fundamentals_report": "基本面数据 " + ("营收增长 15% 毛利率 45% 利润增长 20% ") * 30 + '\n<!-- VERDICT: {"direction": "中性"} -->',
        "macro_report": "宏观数据 " + ("资金流入 23 亿 货币乘数 4.2 利率波动 15bp ") * 30 + '\n<!-- VERDICT: {"direction": "偏多"} -->',
        "sentiment_report": "情绪数据 " + ("情绪指数 62% 贪婪值 75 热度 88 ") * 30,
        "smart_money_report": "主力资金数据 " + ("净流入 5.8 亿 席位买入 3.2 亿 仓位 45% ") * 30,
        "volume_price_report": "量价数据 " + ("放量 35% 换手率 8.5% 振幅 6.2% ") * 30,
    }

    summary = build_seven_source_evidence_summary(overflow_reports)
    lines = summary.splitlines()
    assert len(lines) == 7

    for source_key, label, cap in SEVEN_SOURCE_SPECS:
        matching = [ln for ln in lines if ln.startswith(label)]
        assert len(matching) == 1, f"Expected 1 line starting with {label}"
        line = matching[0]
        assert len(line) <= cap, (
            f"Source {source_key} length {len(line)} exceeded cap {cap}: {line[:80]}..."
        )


def test_seven_source_summary_total_hard_cap_2400():
    """Combined seven-source evidence summary must never exceed 2400 characters."""
    huge_reports = {
        key: f"数据填充 " + ("数值 123.45 百分比 67% 增长 89% 资金 99 亿 ") * 50
        for key, _, _ in SEVEN_SOURCE_SPECS
    }
    summary = build_seven_source_evidence_summary(huge_reports)
    assert len(summary) <= MAX_SEVEN_SOURCE_TOTAL_CHARS
    assert len(summary) <= 2400


def test_seven_source_summary_deterministic_and_no_llm():
    """Calling the combinator on identical input must produce byte-identical output."""
    res1 = build_seven_source_evidence_summary(SAMPLE_SEVEN_REPORTS)
    for _ in range(5):
        res2 = build_seven_source_evidence_summary(SAMPLE_SEVEN_REPORTS)
        assert res1 == res2


def test_seven_source_summary_machine_blocks_stripped():
    """All machine protocol blocks (VERDICT / DEBATE_STATE / RISK_* / MANAGER_VERDICT) must be stripped."""
    dirty_reports = {
        "market_report": (
            "市场技术面：RSI 48.2，股价 1835.5。\n"
            '<!-- VERDICT: {"direction": "偏多", "reason": "秘密原因"} -->\n'
            '<!-- DEBATE_STATE: {"new_claims": ["CLM-1"]} -->\n'
            '<!-- RISK_STATE: {"responded_claim_ids": []} -->\n'
            '<!-- RISK_JUDGE: {"verdict": "pass"} -->\n'
            '<!-- MANAGER_VERDICT: {"winner": "bull"} -->'
        ),
    }
    summary = build_seven_source_evidence_summary(dirty_reports)
    assert "DEBATE_STATE" not in summary
    assert "RISK_STATE" not in summary
    assert "RISK_JUDGE" not in summary
    assert "MANAGER_VERDICT" not in summary
    assert "秘密原因" not in summary
    assert "CLM-1" not in summary
    assert "RSI 48.2" in summary


def test_seven_source_summary_preserves_numbers_zeros_negatives_percentages_dates():
    """0, negative numbers, percentages, currency units, and dates must be preserved verbatim."""
    report = {
        "fundamentals_report": (
            "基本面财务指标：净利润同比 -15.4%，毛利率 45.0%，"
            "不良资产拨备率为 0%，统计基准日 2026-09-14，净流出 -3.2 亿元。\n"
            '<!-- VERDICT: {"direction": "中性"} -->'
        )
    }
    summary = build_seven_source_evidence_summary(report)
    assert "-15.4%" in summary
    assert "45.0%" in summary
    assert "0%" in summary
    assert "2026-09-14" in summary
    assert "-3.2 亿元" in summary


def test_seven_source_summary_missing_and_failed_sources():
    """Empty sources become missing_sources; failure stubs become failed_sources.

    Neither generates pseudo-facts like '已确认无数据' or turns placeholders into evidence.
    """
    mixed_reports = {
        "market_report": "市场技术面：RSI 48.2，股价 1835.5 站上均线。",
        "news_report": "",  # missing
        "fundamentals_report": None,  # missing
        "macro_report": "分析报告生成失败：API 连接超时",  # failed
        "sentiment_report": "生成异常（输出退化），本项不可用",  # failed
        "smart_money_report": "【数据缺失】主力席位数据未获取到",  # status placeholder / failed
        "volume_price_report": "量价：放量突破 20 日均线。",
    }

    bundle = build_seven_source_evidence_bundle(mixed_reports)
    text = bundle.text

    # Missing sources tracking
    assert "news_report" in bundle.missing_sources
    assert "fundamentals_report" in bundle.missing_sources
    # Failed sources tracking
    assert "macro_report" in bundle.failed_sources
    assert "sentiment_report" in bundle.failed_sources
    assert "smart_money_report" in bundle.failed_sources

    # Neither missing nor failed sources generate pseudo-facts or placeholder lines
    assert "已确认无数据" not in text
    assert "确认没有数据" not in text
    assert "分析报告生成失败" not in text
    assert "生成异常" not in text
    assert "【数据缺失】" not in text
    assert "新闻证据摘要" not in text
    assert "基本面证据摘要" not in text
    assert "宏观/板块证据摘要" not in text
    assert "情绪证据摘要" not in text
    assert "主力资金证据摘要" not in text

    # Valid sources are present
    assert "市场技术证据摘要：" in text
    assert "RSI 48.2" in text
    assert "量价证据摘要：" in text
    assert "放量突破" in text


def test_seven_source_bundle_and_summary_consistency():
    """build_seven_source_evidence_summary must equal str(build_seven_source_evidence_bundle)."""
    bundle = build_seven_source_evidence_bundle(SAMPLE_SEVEN_REPORTS)
    summary = build_seven_source_evidence_summary(SAMPLE_SEVEN_REPORTS)
    assert summary == bundle.text
    assert summary == str(bundle)
    assert len(summary) == len(bundle)
    assert bool(bundle) is True
    assert bundle.missing_sources == ()
    assert bundle.failed_sources == ()
