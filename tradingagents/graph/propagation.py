# TradingAgents/graph/propagation.py

import copy
from typing import Dict, Any, List, Optional, Mapping
from tradingagents.agents.utils.agent_states import (
    DEFAULT_PROTOCOL_METADATA,
    InvestDebateState,
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
    RiskDebateState,
    is_v2_debate_enabled,
)
from tradingagents.agents.utils.context_utils import (
    build_market_context,
    infer_instrument_context,
    normalize_user_context,
    summarize_instrument_context,
    summarize_market_context,
    summarize_user_context,
)
from tradingagents.agents.utils.debate_utils import (
    build_empty_risk_debate_state,
    default_round_goal,
)
from .data_collector import default_market_data_context


def default_fund_flow_consensus_guard() -> Dict[str, Any]:
    """Return the serialized fail-closed fund-flow source-selection contract."""
    return {
        "blocked": True,
        "direction_allowed": False,
        "status": "not_checked",
        "validation": {"status": "not_checked", "hard_guard": {"blocked": True}},
        "reason": "fund-flow source selection not checked",
    }


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        user_context: Optional[Mapping[str, Any]] = None,
        selected_analysts: Optional[List[str]] = None,
        request_source: str = "api",
        user_intent: Optional[Dict[str, Any]] = None,
        horizon: str = "short",
        market_data_context: Optional[Dict[str, Any]] = None,
        runtime_config: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create the initial state for the agent graph."""
        instrument_context = infer_instrument_context(company_name)
        market_context = build_market_context(company_name, str(trade_date))
        normalized_user_context = normalize_user_context(user_context)
        user_context_summary = summarize_user_context(normalized_user_context)
        user_prompt_context = (
            f"{summarize_instrument_context(instrument_context)}\n"
            f"{summarize_market_context(market_context)}\n"
            f"{user_context_summary}"
        )
        protocol_meta = copy.deepcopy(DEFAULT_PROTOCOL_METADATA)
        if runtime_config and is_v2_debate_enabled(runtime_config):
            protocol_meta["protocol_version"] = PROTOCOL_VERSION_V2_STRUCTURED
            protocol_meta["protocol_stage"] = "opening"
            protocol_meta["feature_flags"]["v2_debate_enabled"] = True

        investment_debate_state_dict: Dict[str, Any] = {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_speaker": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
            "claims": [],
            "challenges": [],
            "round_messages": [],
            "focus_claim_ids": [],
            "open_claim_ids": [],
            "resolved_claim_ids": [],
            "unresolved_claim_ids": [],
            "round_summary": "",
            "round_goal": default_round_goal("investment", 1),
            "claim_counter": 0,
            "challenge_counter": 0,
            "attempts": [],
        }
        investment_debate_state_dict.update(protocol_meta)
        state: Dict[str, Any] = {
            "messages": [("human", user_prompt_context)],
            "company_of_interest": company_name,
            "trade_date": str(trade_date),
            "instrument_context": instrument_context,
            "market_context": market_context,
            "market_data_context": market_data_context or default_market_data_context(),
            "fund_flow_consensus_guard": default_fund_flow_consensus_guard(),
            "user_context": normalized_user_context,
            "workflow_context": {
                "context_version": "v1",
                "request_source": request_source,
                "selected_analysts": selected_analysts or [],
                "analysis_baseline_date": str(trade_date),
                "data_as_of": market_context.get("data_as_of"),
            },
            "investment_debate_state": InvestDebateState(investment_debate_state_dict),
            "risk_debate_state": RiskDebateState(build_empty_risk_debate_state()),
            "risk_feedback_state": {
                "retry_count": 0,
                "max_retries": 1,
                "revision_required": False,
                "latest_risk_verdict": "",
                "hard_constraints": [],
                "soft_constraints": [],
                "execution_preconditions": [],
                "de_risk_triggers": [],
                "revision_reason": "",
            },
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
            "macro_report": "",
            "smart_money_report": "",
            "investment_plan": "",
            "trader_investment_plan": "",
            "final_trade_decision": "",
            "sender": "",
            "metadata": {},
            "analyst_traces": [],
            "horizon": horizon,
            "short_term_result": None,
            "medium_term_result": None,
        }
        if user_intent is not None:
            state["user_intent"] = user_intent
        return state

    def get_graph_args(self, callbacks: Optional[List] = None) -> Dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
