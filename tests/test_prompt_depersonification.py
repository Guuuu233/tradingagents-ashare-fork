"""Unit tests for P0-5a: Depersonification of smart-money, volume-price, and research manager prompts.

Ensures that:
1. VWMA is treated strictly as volume-weighted average price, not "main force cost" / "主力成本".
2. High-volume stagnation is classified as an event candidate (high_volume_stagnation_candidate), not confirmed manipulation/wash.
3. Ownership attribution requires concrete account/seat evidence (default ownership_inference=false).
4. Volume-price prompt removes "follow insiders" dogmatism and frames Wyckoff phases as pedagogical/unverified hypotheses.
5. Research manager removes personified "main force true intent to accumulate/wash/distribute" formulations while keeping 7-analyst verdict overview and 5-step adjudication intact.
6. No parallel _v2 keys are introduced.
"""
from __future__ import annotations

import pytest

from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS
from tradingagents.prompts.en import PROMPTS as EN_PROMPTS


def test_smart_money_prompt_depersonification():
    """Verify smart_money_system_message removes personified main-force cost and wash assertions."""
    prompt = ZH_PROMPTS["smart_money_system_message"]

    # Negative assertions: Forbidden personification / unverified conclusions
    assert "主力成本区间" not in prompt, "smart_money must not promise main force cost range calculation"
    assert "吸筹价" not in prompt, "smart_money must not assert accumulation price as fact"
    assert "筹码成本区" not in prompt, "smart_money must not assert chip cost zones as fact"
    assert "假摔洗盘" not in prompt, "smart_money must not assert fake drop / wash as confirmed fact"
    assert "震仓洗盘信号（假摔洗盘）" not in prompt

    # Positive assertions: Depersonification contracts
    assert any(term in prompt for term in ("VWMA", "成交量加权价格", "成交量加权价")), "Must describe VWMA as volume-weighted price"
    assert any(term in prompt for term in ("ownership_inference", "订单分组", "分组统计")), "Must clarify order groupings as statistics"
    assert any(term in prompt for term in ("event_candidate", "待验证", "候选", "假设")), "Observable patterns must be framed as candidates/hypotheses"
    assert any(term in prompt for term in ("无席位", "无身份证据", "不得外推", "不得归因", "身份证据")), "Must prohibit attributing ownership without concrete seat evidence"

    # Core framework must remain intact
    assert "What" in prompt and "Why" in prompt and ("So What" in prompt or "SoWhat" in prompt) and ("What Next" in prompt or "WhatNext" in prompt)
    assert any(term in prompt for term in ("超大单", "大单", "中单", "小单"))
    assert any(term in prompt for term in ("龙虎榜", "机构专用席位", "游资席位"))
    assert "<!-- VERDICT:" in prompt


def test_volume_price_prompt_depersonification():
    """Verify volume_price_system_message removes insider control dogmatism and frames Wyckoff as hypotheses."""
    prompt = ZH_PROMPTS["volume_price_system_message"]

    # Negative assertions: Forbidden insider control dogmatism
    assert "跟随局内人（主力）" not in prompt, "Must not dictate following insiders as absolute trading command"
    assert "局内人是唯一能控制价格" not in prompt, "Must not claim insiders are the only group controlling price"
    assert "局内人在震仓洗盘" not in prompt, "Must not assert insiders are shaking out as fact"

    # Positive assertions: Candidate framing & pedagogical hypotheses
    assert any(term in prompt for term in ("high_volume_stagnation_candidate", "高位放量滞涨候选", "放量滞涨候选", "滞涨候选")), "High volume stagnation must be framed as candidate"
    assert any(term in prompt for term in ("教学假设", "理论分析模型", "待验证假设", "理论模型", "供求分析模型")), "Wyckoff phases must be framed as hypotheses / models"

    # Core data authenticity & fail-closed rules must remain strict
    assert "真实存在原则" in prompt
    assert "严禁从无 volume 推断" in prompt or "严禁在无成交量" in prompt or "从无 volume" in prompt
    assert "【数据缺失】" in prompt
    assert "fail-closed" in prompt or "数据缺失" in prompt

    # Phase 1 cross validation & What/Why/SoWhat/WhatNext framework
    assert "阶段一" in prompt or "阶段一分析师产物" in prompt
    assert "确认" in prompt and "冲突" in prompt and "无关" in prompt
    assert "What" in prompt and "Why" in prompt and ("So What" in prompt or "SoWhat" in prompt) and ("What Next" in prompt or "WhatNext" in prompt)
    assert "<!-- VERDICT:" in prompt


def test_research_manager_prompt_depersonification():
    """Verify research_manager_prompt removes personified main-force true intent while keeping adjudication intact."""
    prompt = ZH_PROMPTS["research_manager_prompt"]

    # Negative assertions
    assert "主力真实意图建仓/洗盘/派发" not in prompt, "Must remove personified main-force true intent clause"

    # Positive assertions: Observable expectation gap & no identity inference
    assert any(term in prompt for term in ("量价与资金流的可观察预期差", "可观察预期差", "资金流的可观察预期差", "不得当作身份结论")), "Must frame expectation gap as observable without identity inference"

    # Seven analysts overview intact (DAV-336)
    analysts = [
        "宏观板块",
        "市场（技术面）",
        "舆情（情绪）",
        "新闻",
        "基本面",
        "主力资金",
        "量价",
    ]
    for analyst in analysts:
        assert analyst in prompt, f"Missing analyst: {analyst}"

    # Five-step framework intact
    assert "第一步：证据链完整性审查" in prompt
    assert "第二步：传导路径验证" in prompt
    assert "第三步：焦点分歧裁决" in prompt
    assert "第四步：极端情景测试" in prompt
    assert "第五步：风险收益比量化" in prompt


def test_english_volume_price_prompt_depersonification():
    """Verify en.py volume_price_system_message has no insider control dogmatism and keeps fail-closed."""
    prompt_en = EN_PROMPTS["volume_price_system_message"]

    # Negative assertions
    assert "Follow the insiders: Market makers and large operators are the only group that can control price direction" not in prompt_en
    assert "insiders shaking out positions, don't chase" not in prompt_en

    # Positive assertions
    assert "Never infer or guess accumulation, distribution" in prompt_en
    assert "[DATA MISSING]" in prompt_en
    assert "CONFIRMED" in prompt_en and "CONFLICTING" in prompt_en and "IRRELEVANT" in prompt_en


def test_no_parallel_v2_prompt_keys_across_all_prompts():
    """Ensure no _v2, _new, or parallel prompt keys were added."""
    forbidden_suffixes = ("_v2", "_new", "_fixed", "_enhanced", "_deep")
    for key in list(ZH_PROMPTS.keys()) + list(EN_PROMPTS.keys()):
        for suffix in forbidden_suffixes:
            assert not key.endswith(suffix), f"Parallel prompt key found: {key}"
