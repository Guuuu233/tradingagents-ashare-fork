"""Tests for P1-B/B1 Opening: Request-level enablement, 5-battlefield gate, and double-blind input isolation (DAV-391)."""

import asyncio
import copy
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    DEFAULT_PROTOCOL_METADATA,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    get_protocol_metadata,
    is_v2_debate_enabled,
)
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


class TestO1RequestLevelConfigAndState:
    """O1: Request-level enablement, protocol versioning, and initial state isolation."""

    def test_default_config_initial_state_is_v1_legacy(self):
        """Default config without override produces v1_legacy initial state with v2_debate_enabled=False."""
        propagator = Propagator()
        state = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
        )
        inv_state = state["investment_debate_state"]
        assert inv_state["protocol_version"] == PROTOCOL_VERSION_V1_LEGACY
        assert inv_state["protocol_stage"] == "opening"
        assert inv_state["feature_flags"]["v2_debate_enabled"] is False
        assert is_v2_debate_enabled(state) is False
        assert is_v2_debate_enabled(inv_state) is False

    def test_request_config_override_v2_debate_enabled_true(self):
        """Single request config with v2_debate_enabled=True produces v2_structured_disagreement and opening stage."""
        propagator = Propagator()

        # Test direct flag
        state_direct = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"v2_debate_enabled": True},
        )
        inv_direct = state_direct["investment_debate_state"]
        assert inv_direct["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
        assert inv_direct["protocol_stage"] == "opening"
        assert inv_direct["feature_flags"]["v2_debate_enabled"] is True
        assert is_v2_debate_enabled(state_direct) is True
        assert is_v2_debate_enabled(inv_direct) is True

        # Test nested feature_flags dict
        state_nested = propagator.create_initial_state(
            company_name="600519.SH",
            trade_date="2026-08-20",
            runtime_config={"feature_flags": {"v2_debate_enabled": True}},
        )
        inv_nested = state_nested["investment_debate_state"]
        assert inv_nested["protocol_version"] == PROTOCOL_VERSION_V2_STRUCTURED
        assert inv_nested["protocol_stage"] == "opening"
        assert inv_nested["feature_flags"]["v2_debate_enabled"] is True
        assert is_v2_debate_enabled(state_nested) is True

    def test_state_deepcopy_isolation(self):
        """States with different configs are completely isolated without mutating global defaults or each other."""
        meta_before = copy.deepcopy(DEFAULT_PROTOCOL_METADATA)
        flags_before = copy.deepcopy(DEFAULT_FEATURE_FLAGS)

        propagator = Propagator()
        state_v1 = propagator.create_initial_state("600519.SH", "2026-08-20")
        state_v2 = propagator.create_initial_state(
            "600519.SH", "2026-08-20", runtime_config={"v2_debate_enabled": True}
        )

        # Mutate state_v2
        state_v2["investment_debate_state"]["claims"].append({"claim_id": "INV-1"})
        state_v2["investment_debate_state"]["feature_flags"]["shadow_credit_enabled"] = False

        # Assert state_v1 and defaults remain untouched
        assert len(state_v1["investment_debate_state"]["claims"]) == 0
        assert state_v1["investment_debate_state"]["feature_flags"]["v2_debate_enabled"] is False
        assert state_v1["investment_debate_state"]["feature_flags"]["shadow_credit_enabled"] is True
        assert DEFAULT_PROTOCOL_METADATA == meta_before
        assert DEFAULT_FEATURE_FLAGS == flags_before


class TestO2OpeningStageAndBattlefieldProtocol:
    """O2: Stage recording in round_messages, claims, 5-battlefield gate, and opening protocol rules."""

    def test_v2_opening_valid_payload_bull_message_1(self):
        """Bull opening (message_index=1) with 3 claims in 3 distinct battlefields is valid and recorded with stage and debate_round."""
        from tradingagents.agents.utils.debate_utils import (
            VALID_BATTLEFIELDS,
            validate_debate_response,
            update_debate_state_with_payload,
        )

        state = {
            "count": 0,
            "claims": [],
            "round_messages": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        raw_response = (
            "多头立论正文：从资金筹码、情绪题材和量价三个维度全面看好。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "主力超大单持续净流入", "evidence": ["主力资金流入1.2亿"], "confidence": 0.85, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "行业题材政策高景气", "evidence": ["宏观扶持政策落地"], "confidence": 0.80, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
            '    {"claim": "日线突破放量均线多头", "evidence": ["突破60日线放量20%"], "confidence": 0.78, "battlefield": "price_volume", "target_claim_ids": []}\n'
            "  ],\n"
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "多头开国立论三维度",\n'
            '  "round_goal": "建立多头核心立论"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )

        assert is_valid is True
        assert parse_status == "valid"
        assert error_detail == ""
        assert payload is not None

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 1
        assert len(new_state["round_messages"]) == 1
        msg = new_state["round_messages"][0]
        assert msg["message_index"] == 1
        assert msg["debate_round"] == 1
        assert msg["stage"] == "opening"
        assert len(msg["new_claim_ids"]) == 3

        claims = new_state["claims"]
        assert len(claims) == 3
        for idx, c in enumerate(claims, 1):
            assert c["claim_id"] == f"INV-{idx}"
            assert c["debate_round"] == 1
            assert c["message_index"] == 1
            assert c["stage"] == "opening"
            assert c["battlefield"] in VALID_BATTLEFIELDS

    def test_v2_opening_bear_message_index_2_does_not_trigger_legacy_check_b_c(self):
        """Bear opening (message_index=2) in v2 with responded_claim_ids=[] and target_claim_ids=[] is valid and does NOT trigger Check B/C."""
        from tradingagents.agents.utils.debate_utils import (
            validate_debate_response,
            update_debate_state_with_payload,
        )

        # State after Bull opening
        bull_claims = [
            {"claim_id": "INV-1", "speaker_key": "Bull", "stance": "bullish", "claim": "多头看涨", "battlefield": "capital_flow", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
            {"claim_id": "INV-2", "speaker_key": "Bull", "stance": "bullish", "claim": "情绪向好", "battlefield": "sentiment_theme", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
            {"claim_id": "INV-3", "speaker_key": "Bull", "stance": "bullish", "claim": "量价突破", "battlefield": "price_volume", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
        ]
        state = {
            "count": 1,
            "claims": bull_claims,
            "claim_counter": 3,
            "round_messages": [{"message_index": 1, "debate_round": 1, "stage": "opening", "speaker_key": "Bull", "new_claim_ids": ["INV-1", "INV-2", "INV-3"]}],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        raw_response = (
            "空头独立立论正文：从基本面、宏观政策与筹码三个维度分析下行风险。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "应收账款恶化现金流承压", "evidence": ["财报显示现金流下滑30%"], "confidence": 0.82, "battlefield": "fundamentals", "target_claim_ids": []},\n'
            '    {"claim": "宏观外需降温面临逆风", "evidence": ["出口增速下行"], "confidence": 0.75, "battlefield": "macro_policy", "target_claim_ids": []},\n'
            '    {"claim": "北向资金持续净流出", "evidence": ["北向单周净卖出15亿"], "confidence": 0.79, "battlefield": "capital_flow", "target_claim_ids": []}\n'
            "  ],\n"
            '  "resolved_claim_ids": [],\n'
            '  "unresolved_claim_ids": [],\n'
            '  "next_focus_claim_ids": [],\n'
            '  "round_summary": "空头开国立论三维度",\n'
            '  "round_goal": "建立空头核心立论"\n'
            "} -->"
        )

        is_valid, parse_status, error_detail, payload = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bear",
            stance="bearish",
            marker="DEBATE_STATE",
            domain="investment",
        )

        assert is_valid is True
        assert parse_status == "valid"

        new_state = update_debate_state_with_payload(
            state=state,
            raw_response=raw_response,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )

        assert new_state["count"] == 2
        assert len(new_state["round_messages"]) == 2
        bear_msg = new_state["round_messages"][1]
        assert bear_msg["message_index"] == 2
        assert bear_msg["debate_round"] == 1
        assert bear_msg["stage"] == "opening"
        assert bear_msg["protocol_stage"] == "opening"
        assert new_state["protocol_stage"] == "challenge"
        assert len(new_state["claims"]) == 6  # 3 Bull + 3 Bear
        bear_claims = new_state["claims"][3:]
        for idx, c in enumerate(bear_claims, 4):
            assert c["claim_id"] == f"INV-{idx}"
            assert c["debate_round"] == 1
            assert c["message_index"] == 2
            assert c["stage"] == "opening"

    def test_v2_opening_invalid_if_responded_claim_ids_non_empty(self):
        """In v2 opening stage, responded_claim_ids must be empty; non-empty results in invalid_protocol."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        state = {
            "count": 0,
            "claims": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        raw_response = (
            "多头立论正文。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": ["INV-1"],\n'
            '  "new_claims": [\n'
            '    {"claim": "主力流入", "evidence": ["证据1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "情绪向上", "evidence": ["证据2"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
            '    {"claim": "量价突破", "evidence": ["证据3"], "confidence": 0.8, "battlefield": "price_volume", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "responded_claim_ids" in error_detail

    def test_v2_opening_invalid_if_target_claim_ids_non_empty(self):
        """In v2 opening stage, target_claim_ids must be empty; non-empty results in invalid_protocol."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        state = {
            "count": 0,
            "claims": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        raw_response = (
            "多头立论正文。\n\n"
            "<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "主力流入", "evidence": ["证据1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": ["INV-2"]},\n'
            '    {"claim": "情绪向上", "evidence": ["证据2"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
            '    {"claim": "量价突破", "evidence": ["证据3"], "confidence": 0.8, "battlefield": "price_volume", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )

        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state,
            raw_response=raw_response,
            speaker_key="Bull",
            stance="bullish",
            marker="DEBATE_STATE",
            domain="investment",
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "target_claim_ids" in error_detail

    def test_v2_opening_invalid_if_not_exactly_3_claims(self):
        """In v2 opening stage, new_claims count must be exactly 3. 1, 2, or 4 claims are all rejected as invalid_protocol."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        state = {
            "count": 0,
            "claims": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        # 1 claim: invalid
        raw_1 = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "主力流入", "evidence": ["证据1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state, raw_response=raw_1, speaker_key="Bull", stance="bullish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"

        # 2 claims covering 2 distinct valid battlefields: MUST BE REJECTED (exactly 3 required)
        raw_2 = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "资金1", "evidence": ["e1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "情绪1", "evidence": ["e2"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state, raw_response=raw_2, speaker_key="Bull", stance="bullish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"

        # 4 claims: invalid
        raw_4 = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "1", "evidence": ["e1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "2", "evidence": ["e2"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
            '    {"claim": "3", "evidence": ["e3"], "confidence": 0.8, "battlefield": "price_volume", "target_claim_ids": []},\n'
            '    {"claim": "4", "evidence": ["e4"], "confidence": 0.8, "battlefield": "macro_policy", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state, raw_response=raw_4, speaker_key="Bull", stance="bullish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"

    def test_v2_opening_invalid_if_less_than_3_distinct_battlefields(self):
        """In v2 opening stage, new_claims must cover at least 3 distinct battlefields."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        state = {
            "count": 0,
            "claims": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        # 3 claims covering only 2 distinct battlefields
        raw_2_distinct = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "资金1", "evidence": ["e1"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "资金2", "evidence": ["e2"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "情绪1", "evidence": ["e3"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state, raw_response=raw_2_distinct, speaker_key="Bull", stance="bullish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "战场" in error_detail or "battlefield" in error_detail

    def test_v2_opening_invalid_if_invalid_battlefield(self):
        """In v2 opening stage, invalid or missing battlefield yields invalid_protocol."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        state = {
            "count": 0,
            "claims": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        raw_bad_field = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "未知1", "evidence": ["e1"], "confidence": 0.8, "battlefield": "unknown_bf", "target_claim_ids": []},\n'
            '    {"claim": "资金2", "evidence": ["e2"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []},\n'
            '    {"claim": "情绪1", "evidence": ["e3"], "confidence": 0.8, "battlefield": "sentiment_theme", "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=state, raw_response=raw_bad_field, speaker_key="Bull", stance="bullish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "battlefield" in error_detail or "战场" in error_detail

    def test_legacy_mode_preserves_check_b_c_and_dav346(self):
        """When v2_debate_enabled is False, Bear message_index=2 still requires responded_claim_ids and target_claim_ids (legacy Check B/C)."""
        from tradingagents.agents.utils.debate_utils import validate_debate_response

        legacy_state = {
            "count": 1,
            "claims": [
                {"claim_id": "INV-1", "speaker_key": "Bull", "stance": "bullish", "claim": "多头看涨", "status": "open"}
            ],
            "protocol_version": PROTOCOL_VERSION_V1_LEGACY,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": False},
        }

        # Bear message_index=2 with responded_claim_ids=[] -> legacy Check B violation
        raw_bear_empty = (
            "正文\n\n<!-- DEBATE_STATE: {\n"
            '  "responded_claim_ids": [],\n'
            '  "new_claims": [\n'
            '    {"claim": "空头观点", "evidence": ["证据"], "confidence": 0.8, "target_claim_ids": []}\n'
            "  ]\n"
            "} -->"
        )
        is_valid, parse_status, error_detail, _ = validate_debate_response(
            state=legacy_state, raw_response=raw_bear_empty, speaker_key="Bear", stance="bearish"
        )
        assert is_valid is False
        assert parse_status == "invalid_protocol"
        assert "responded_claim_ids" in error_detail


class FakeStreamingLLM:
    """Fake streaming LLM that captures all prompts passed to astream and yields configured response chunks."""

    def __init__(self, responses: list[str] | None = None):
        self.captured_prompts: list[str] = []
        self.responses = responses or []
        self.call_count = 0

    async def astream(self, prompt: str):
        self.captured_prompts.append(prompt)
        resp = self.responses[self.call_count] if self.call_count < len(self.responses) else "默认立论回复\n<!-- DEBATE_STATE: {\"responded_claim_ids\": [], \"new_claims\": [{\"claim\": \"c1\", \"evidence\": [\"e1\"], \"confidence\": 0.8, \"battlefield\": \"capital_flow\", \"target_claim_ids\": []}, {\"claim\": \"c2\", \"evidence\": [\"e2\"], \"confidence\": 0.8, \"battlefield\": \"sentiment_theme\", \"target_claim_ids\": []}, {\"claim\": \"c3\", \"evidence\": [\"e3\"], \"confidence\": 0.8, \"battlefield\": \"price_volume\", \"target_claim_ids\": []}], \"resolved_claim_ids\": [], \"unresolved_claim_ids\": [], \"next_focus_claim_ids\": [], \"round_summary\": \"s\", \"round_goal\": \"g\"} -->"
        self.call_count += 1
        for chunk in [resp[:len(resp)//2], resp[len(resp)//2:]]:
            mock_chunk = MagicMock()
            mock_chunk.content = chunk
            yield mock_chunk


class TestO3DoubleBlindPromptCaptureAndIsolation:
    """O3: Double-blind input isolation, prompt capture, and zero leak guarantees."""

    def test_bull_and_bear_opening_double_blind_zero_leak(self):
        """In v2 opening:

        1. Bull opening receives empty history, empty current_response, empty claims, n_matches=0 memory.
        2. Bear opening (executed after Bull) prompt contains ZERO Bull claims, ZERO Bull unique sentences, 0 current_response, n_matches=0 memory.
        3. 7 reports are byte-for-byte symmetric between Bull and Bear prompts.
        4. Authoritative state retains both Bull (INV-1..3) and Bear (INV-4..6) claims.
        """
        async def _run():
            from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
            from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
            from tradingagents.agents.utils.debate_utils import build_debate_report_manifest

            seven_reports = {
                "macro_report": "宏观报告内容：全球流动性宽松，国内财政发力，基准利率调降25bp。",
                "market_report": "市场技术报告：突破年线中轨，量能放大15%，MACD零轴上方金叉。",
                "sentiment_report": "情绪舆情报告：雪球热度前5，市场情绪温和偏多，游资题材轮动。",
                "news_report": "新闻事件报告：公司中标重大特许经营项目，订单规模达30亿元。",
                "fundamentals_report": "基本面财报：Q1营收增18%，毛利率提升2.1pct，现金流充沛。",
                "smart_money_report": "主力资金报告：超大单单日净买入2.5亿元，机构席位增持。",
                "volume_price_report": "量价分析报告：量价齐升，VPA量价配合良好无异常放量滞涨。",
            }

            propagator = Propagator()
            initial_state = propagator.create_initial_state(
                company_name="600519.SH",
                trade_date="2026-08-20",
                runtime_config={"v2_debate_enabled": True},
            )
            initial_state.update(seven_reports)

            bull_unique_secret = "BULL_SECRET_ALPHA_TOKEN_99999"
            bull_response = (
                f"多头立论正文包含独特论据：{bull_unique_secret}。\n\n"
                "<!-- DEBATE_STATE: {\n"
                '  "responded_claim_ids": [],\n'
                '  "new_claims": [\n'
                f'    {{"claim": "主力大单强劲建仓_{bull_unique_secret}", "evidence": ["主力流入2.5亿"], "confidence": 0.88, "battlefield": "capital_flow", "target_claim_ids": []}},\n'
                '    {"claim": "情绪题材高景气", "evidence": ["雪球热度前5"], "confidence": 0.82, "battlefield": "sentiment_theme", "target_claim_ids": []},\n'
                '    {"claim": "量价技术突破年线", "evidence": ["MACD金叉突破"], "confidence": 0.85, "battlefield": "price_volume", "target_claim_ids": []}\n'
                "  ],\n"
                '  "resolved_claim_ids": [],\n'
                '  "unresolved_claim_ids": [],\n'
                '  "next_focus_claim_ids": [],\n'
                f'  "round_summary": "多头开国立论摘要_{bull_unique_secret}",\n'
                '  "round_goal": "建立多头核心立论"\n'
                "} -->"
            )

            bear_unique_secret = "BEAR_SECRET_RISK_TOKEN_88888"
            bear_response = (
                f"空头独立立论正文：{bear_unique_secret}。\n\n"
                "<!-- DEBATE_STATE: {\n"
                '  "responded_claim_ids": [],\n'
                '  "new_claims": [\n'
                '    {"claim": "应收账款恶化存在计提风险", "evidence": ["财报应收增加"], "confidence": 0.80, "battlefield": "fundamentals", "target_claim_ids": []},\n'
                '    {"claim": "外需放缓出口增速回落", "evidence": ["宏观数据走弱"], "confidence": 0.76, "battlefield": "macro_policy", "target_claim_ids": []},\n'
                '    {"claim": "北向资金近期持续离场", "evidence": ["北向单周净流出"], "confidence": 0.79, "battlefield": "capital_flow", "target_claim_ids": []}\n'
                "  ],\n"
                '  "resolved_claim_ids": [],\n'
                '  "unresolved_claim_ids": [],\n'
                '  "next_focus_claim_ids": [],\n'
                '  "round_summary": "空头开国立论摘要",\n'
                '  "round_goal": "建立空头核心立论"\n'
                "} -->"
            )

            bull_llm = FakeStreamingLLM(responses=[bull_response])
            bear_llm = FakeStreamingLLM(responses=[bear_response])

            bull_memory = MagicMock()
            bull_memory.get_memories = MagicMock(return_value=[])

            bear_memory = MagicMock()
            bear_memory.get_memories = MagicMock(return_value=[])

            bull_node = create_bull_researcher(bull_llm, bull_memory)
            bear_node = create_bear_researcher(bear_llm, bear_memory)

            # 1. Execute Bull opening
            bull_result = await bull_node(initial_state)
            assert "investment_debate_state" in bull_result
            state_after_bull = dict(initial_state)
            state_after_bull["investment_debate_state"] = bull_result["investment_debate_state"]

            assert len(bull_llm.captured_prompts) == 1
            bull_prompt = bull_llm.captured_prompts[0]

            if bull_memory.get_memories.called:
                assert bull_memory.get_memories.call_args[1].get("n_matches") == 0

            inv_after_bull = state_after_bull["investment_debate_state"]
            assert inv_after_bull["count"] == 1
            assert len(inv_after_bull["claims"]) == 3
            assert inv_after_bull["claims"][0]["claim_id"] == "INV-1"

            # 2. Execute Bear opening
            bear_result = await bear_node(state_after_bull)
            assert "investment_debate_state" in bear_result
            final_state = dict(state_after_bull)
            final_state["investment_debate_state"] = bear_result["investment_debate_state"]

            assert len(bear_llm.captured_prompts) == 1
            bear_prompt = bear_llm.captured_prompts[0]

            # 3. Double-blind zero leak checks
            assert bull_unique_secret not in bear_prompt, "Leak detected: Bull unique token found in Bear opening prompt!"
            assert "多头开国立论摘要" not in bear_prompt, "Leak detected: Bull round_summary found in Bear opening prompt!"
            assert "主力大单强劲建仓" not in bear_prompt, "Leak detected: Bull claim text found in Bear opening prompt!"
            assert "情绪题材高景气" not in bear_prompt, "Leak detected: Bull claim text found in Bear opening prompt!"
            assert "量价技术突破年线" not in bear_prompt, "Leak detected: Bull claim text found in Bear opening prompt!"

            # Static INV IDs must be 0 in opening prompts
            assert "INV-1" not in bear_prompt, "Leak detected: Static INV-1 found in Bear opening prompt!"
            assert "INV-2" not in bear_prompt, "Leak detected: Static INV-2 found in Bear opening prompt!"
            assert "INV-3" not in bear_prompt, "Leak detected: Static INV-3 found in Bear opening prompt!"
            assert "INV-1" not in bull_prompt, "Leak detected: Static INV-1 found in Bull opening prompt!"
            assert "INV-2" not in bull_prompt, "Leak detected: Static INV-2 found in Bull opening prompt!"
            assert "INV-3" not in bull_prompt, "Leak detected: Static INV-3 found in Bull opening prompt!"

            # Focus and unresolved subsets must be empty placeholder
            assert "当前没有未解决 claim。" in bear_prompt
            assert "当前没有已登记 claim" in bear_prompt

            if bear_memory.get_memories.called:
                assert bear_memory.get_memories.call_args[1].get("n_matches") == 0

            # 4. 7-report symmetry & manifest
            for report_name, report_text in seven_reports.items():
                assert report_text in bull_prompt, f"Report {report_name} missing from Bull prompt"
                assert report_text in bear_prompt, f"Report {report_name} missing from Bear prompt"

            manifest_bull = build_debate_report_manifest(initial_state)
            manifest_bear = build_debate_report_manifest(state_after_bull)
            assert manifest_bull == manifest_bear

            # 5. Authoritative state retention
            inv_final = final_state["investment_debate_state"]
            assert inv_final["count"] == 2
            assert len(inv_final["claims"]) == 6
            assert [c["claim_id"] for c in inv_final["claims"]] == [
                "INV-1", "INV-2", "INV-3", "INV-4", "INV-5", "INV-6"
            ]
            assert len(inv_final["round_messages"]) == 2
            assert inv_final["round_messages"][0]["stage"] == "opening"
            assert inv_final["round_messages"][1]["stage"] == "opening"

        asyncio.run(_run())

    def test_opening_retry_prompt_does_not_leak_or_demand_rebuttal(self):
        """When opening attempt 1 fails validation, attempt 2 retry prompt enforces opening rules and does not leak or demand rebuttal."""
        async def _run():
            from tradingagents.agents.researchers.bear_researcher import create_bear_researcher

            bull_unique_secret = "BULL_SPECIAL_CLAIM_PHRASE_77777"
            bull_claims = [
                {"claim_id": "INV-1", "speaker_key": "Bull", "stance": "bullish", "claim": f"多头看涨_{bull_unique_secret}", "battlefield": "capital_flow", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
                {"claim_id": "INV-2", "speaker_key": "Bull", "stance": "bullish", "claim": "情绪向好", "battlefield": "sentiment_theme", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
                {"claim_id": "INV-3", "speaker_key": "Bull", "stance": "bullish", "claim": "量价突破", "battlefield": "price_volume", "debate_round": 1, "message_index": 1, "stage": "opening", "status": "open"},
            ]
            state = {
                "macro_report": "宏观",
                "market_report": "市场",
                "sentiment_report": "情绪",
                "news_report": "新闻",
                "fundamentals_report": "基本面",
                "smart_money_report": "资金",
                "volume_price_report": "量价",
                "investment_debate_state": {
                    "count": 1,
                    "claims": bull_claims,
                    "claim_counter": 3,
                    "round_messages": [{"message_index": 1, "debate_round": 1, "stage": "opening", "speaker_key": "Bull", "new_claim_ids": ["INV-1", "INV-2", "INV-3"]}],
                    "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                    "protocol_stage": "opening",
                    "feature_flags": {"v2_debate_enabled": True},
                },
            }

            invalid_resp_1 = (
                "空头立论尝试1\n\n<!-- DEBATE_STATE: {\n"
                '  "responded_claim_ids": [],\n'
                '  "new_claims": [\n'
                '    {"claim": "单条观点", "evidence": ["e1"], "confidence": 0.8, "battlefield": "fundamentals", "target_claim_ids": []}\n'
                "  ]\n"
                "} -->"
            )
            valid_resp_2 = (
                "空头立论尝试2\n\n<!-- DEBATE_STATE: {\n"
                '  "responded_claim_ids": [],\n'
                '  "new_claims": [\n'
                '    {"claim": "空头观点1", "evidence": ["e1"], "confidence": 0.8, "battlefield": "fundamentals", "target_claim_ids": []},\n'
                '    {"claim": "空头观点2", "evidence": ["e2"], "confidence": 0.8, "battlefield": "macro_policy", "target_claim_ids": []},\n'
                '    {"claim": "空头观点3", "evidence": ["e3"], "confidence": 0.8, "battlefield": "capital_flow", "target_claim_ids": []}\n'
                "  ],\n"
                '  "resolved_claim_ids": [],\n'
                '  "unresolved_claim_ids": [],\n'
                '  "next_focus_claim_ids": [],\n'
                '  "round_summary": "空头立论",\n'
                '  "round_goal": "立论"\n'
                "} -->"
            )

            bear_llm = FakeStreamingLLM(responses=[invalid_resp_1, valid_resp_2])
            bear_memory = MagicMock()
            bear_memory.get_memories = MagicMock(return_value=[])

            bear_node = create_bear_researcher(bear_llm, bear_memory)
            res = await bear_node(state)

            assert len(bear_llm.captured_prompts) == 2
            retry_prompt = bear_llm.captured_prompts[1]

            # Zero leak assertions on retry prompt
            assert bull_unique_secret not in retry_prompt
            assert "多头看涨" not in retry_prompt
            assert "responded_claim_ids 必须包含至少一条对手未解决 Claim ID" not in retry_prompt
            assert "target_claim_ids 必须指定至少一条对手 Claim ID" not in retry_prompt

            # Must contain opening retry requirements
            assert "responded_claim_ids 必须为空数组 []" in retry_prompt
            assert "target_claim_ids 必须为空数组 []" in retry_prompt
            assert "battlefield" in retry_prompt or "战场" in retry_prompt

        asyncio.run(_run())


class TestRenderDebatePromptAndStageContracts:
    """Independent unit tests for render_debate_prompt unified stage helper and protocol stage transition."""

    def test_render_debate_prompt_legacy_preserves_three_round_framework_and_inv_examples(self):
        from tradingagents.agents.utils.debate_utils import render_debate_prompt
        from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS
        from tradingagents.prompts.en import PROMPTS as EN_PROMPTS

        for key in ("bull_prompt", "bear_prompt"):
            zh_rendered = render_debate_prompt(ZH_PROMPTS[key], is_opening_stage=False, language="zh")
            assert "【辩论三轮递进推进框架】" in zh_rendered
            assert "STAGE_FRAMEWORK_START" not in zh_rendered
            assert "STAGE_OUTPUT_CONTRACT_START" not in zh_rendered
            if key == "bull_prompt":
                assert '"target_claim_ids": ["INV-2"]' in zh_rendered
            else:
                assert '"target_claim_ids": ["INV-1"]' in zh_rendered

            en_rendered = render_debate_prompt(EN_PROMPTS[key], is_opening_stage=False, language="en")
            assert "【Three-Round Progressive Debate Framework】" in en_rendered
            assert "STAGE_FRAMEWORK_START" not in en_rendered
            assert "STAGE_OUTPUT_CONTRACT_START" not in en_rendered
            if key == "bull_prompt":
                assert '"target_claim_ids": ["INV-2"]' in en_rendered
            else:
                assert '"target_claim_ids": ["INV-1"]' in en_rendered

    def test_render_debate_prompt_opening_removes_legacy_and_injects_opening_contract_with_zero_leak(self):
        from tradingagents.agents.utils.debate_utils import render_debate_prompt
        from tradingagents.prompts.zh import PROMPTS as ZH_PROMPTS
        from tradingagents.prompts.en import PROMPTS as EN_PROMPTS

        for key in ("bull_prompt", "bear_prompt"):
            zh_rendered = render_debate_prompt(ZH_PROMPTS[key], is_opening_stage=True, language="zh")
            assert "【Opening 阶段独立双盲立论契约】" in zh_rendered
            assert "【辩论三轮递进推进框架】" not in zh_rendered
            assert "INV-1" not in zh_rendered
            assert "INV-2" not in zh_rendered
            assert "INV-3" not in zh_rendered
            assert "INV-4" not in zh_rendered
            assert "INV-5" not in zh_rendered
            assert "恰好3条核心立论" in zh_rendered
            assert "capital_flow / sentiment_theme / price_volume / macro_policy / fundamentals" in zh_rendered
            assert '"responded_claim_ids": []' in zh_rendered
            assert "STAGE_FRAMEWORK_START" not in zh_rendered
            assert "STAGE_OUTPUT_CONTRACT_START" not in zh_rendered

            en_rendered = render_debate_prompt(EN_PROMPTS[key], is_opening_stage=True, language="en")
            assert "【Opening Stage Independent Double-Blind Opening Contract】" in en_rendered
            assert "【Three-Round Progressive Debate Framework】" not in en_rendered
            assert "INV-1" not in en_rendered
            assert "INV-2" not in en_rendered
            assert "INV-3" not in en_rendered
            assert "INV-4" not in en_rendered
            assert "INV-5" not in en_rendered
            assert "State exactly 3 core claims" in en_rendered
            assert "capital_flow / sentiment_theme / price_volume / macro_policy / fundamentals" in en_rendered
            assert '"responded_claim_ids": []' in en_rendered
            assert "STAGE_FRAMEWORK_START" not in en_rendered
            assert "STAGE_OUTPUT_CONTRACT_START" not in en_rendered

    def test_v2_opening_stage_transition_and_unaccepted_attempt_isolation(self):
        """Verify:

        - Bull message 1 -> msg.stage=opening, state.protocol_stage=opening.
        - Bear message 2 -> msg.stage=opening, authoritative state.protocol_stage=challenge.
        - Invalid attempt -> count and protocol_stage do not advance, claims unpolluted.
        """
        from tradingagents.agents.utils.debate_utils import update_debate_state_with_payload

        initial_state = {
            "count": 0,
            "claims": [],
            "round_messages": [],
            "attempts": [],
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": True},
        }

        # 1. Bull message 1 valid
        bull_raw = (
            "多头正文\n<!-- DEBATE_STATE: {\"responded_claim_ids\": [], \"new_claims\": ["
            "{\"claim\": \"c1\", \"evidence\": [\"e1\"], \"confidence\": 0.8, \"battlefield\": \"capital_flow\", \"target_claim_ids\": []},"
            "{\"claim\": \"c2\", \"evidence\": [\"e2\"], \"confidence\": 0.8, \"battlefield\": \"sentiment_theme\", \"target_claim_ids\": []},"
            "{\"claim\": \"c3\", \"evidence\": [\"e3\"], \"confidence\": 0.8, \"battlefield\": \"price_volume\", \"target_claim_ids\": []}"
            "], \"resolved_claim_ids\": [], \"unresolved_claim_ids\": [], \"next_focus_claim_ids\": [], \"round_summary\": \"s1\", \"round_goal\": \"g1\"} -->"
        )
        state_after_bull = update_debate_state_with_payload(
            state=initial_state,
            raw_response=bull_raw,
            speaker_label="Bull Analyst",
            speaker_key="Bull",
            stance="bullish",
            history_key="bull_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert state_after_bull["count"] == 1
        assert state_after_bull["protocol_stage"] == "opening"
        assert state_after_bull["round_messages"][0]["stage"] == "opening"
        assert state_after_bull["round_messages"][0]["protocol_stage"] == "opening"
        assert len(state_after_bull["claims"]) == 3

        # 2. Bear invalid attempt (only 2 claims) -> does not advance count or stage, claims unpolluted
        bear_invalid = (
            "空头无效尝试\n<!-- DEBATE_STATE: {\"responded_claim_ids\": [], \"new_claims\": ["
            "{\"claim\": \"c4\", \"evidence\": [\"e4\"], \"confidence\": 0.8, \"battlefield\": \"fundamentals\", \"target_claim_ids\": []},"
            "{\"claim\": \"c5\", \"evidence\": [\"e5\"], \"confidence\": 0.8, \"battlefield\": \"macro_policy\", \"target_claim_ids\": []}"
            "], \"resolved_claim_ids\": [], \"unresolved_claim_ids\": [], \"next_focus_claim_ids\": [], \"round_summary\": \"s2\", \"round_goal\": \"g2\"} -->"
        )
        state_after_invalid = update_debate_state_with_payload(
            state=state_after_bull,
            raw_response=bear_invalid,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert state_after_invalid["count"] == 1
        assert state_after_invalid["protocol_stage"] == "opening"
        assert len(state_after_invalid["claims"]) == 3  # Not polluted by invalid attempt
        assert state_after_invalid["blocked"] is True

        # 3. Bear valid message 2 -> advances count to 2, msg.stage=opening, authoritative state.protocol_stage=challenge
        bear_valid = (
            "空头有效发言\n<!-- DEBATE_STATE: {\"responded_claim_ids\": [], \"new_claims\": ["
            "{\"claim\": \"c4\", \"evidence\": [\"e4\"], \"confidence\": 0.8, \"battlefield\": \"fundamentals\", \"target_claim_ids\": []},"
            "{\"claim\": \"c5\", \"evidence\": [\"e5\"], \"confidence\": 0.8, \"battlefield\": \"macro_policy\", \"target_claim_ids\": []},"
            "{\"claim\": \"c6\", \"evidence\": [\"e6\"], \"confidence\": 0.8, \"battlefield\": \"capital_flow\", \"target_claim_ids\": []}"
            "], \"resolved_claim_ids\": [], \"unresolved_claim_ids\": [], \"next_focus_claim_ids\": [], \"round_summary\": \"s2\", \"round_goal\": \"g2\"} -->"
        )
        state_after_bear = update_debate_state_with_payload(
            state=state_after_bull,
            raw_response=bear_valid,
            speaker_label="Bear Analyst",
            speaker_key="Bear",
            stance="bearish",
            history_key="bear_history",
            marker="DEBATE_STATE",
            claim_prefix="INV",
            domain="investment",
            speaker_field="current_speaker",
        )
        assert state_after_bear["count"] == 2
        assert state_after_bear["protocol_stage"] == "challenge"
        assert state_after_bear["round_messages"][1]["stage"] == "opening"
        assert state_after_bear["round_messages"][1]["protocol_stage"] == "opening"
        assert len(state_after_bear["claims"]) == 6
