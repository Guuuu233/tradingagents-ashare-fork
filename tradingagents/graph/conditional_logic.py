# TradingAgents/graph/conditional_logic.py

from typing import Any, Mapping
from tradingagents.agents.utils.agent_states import AgentState, is_v2_debate_enabled
from tradingagents.agents.utils.debate_utils import DebateProtocolError, safe_int


def should_enter_tiebreak(inv_state: Mapping[str, Any]) -> bool:
    """Determine whether v2 debate requires entering the tiebreak stage.

    Tiebreak is triggered when there is opposite interpretation on the same data
    with unresolved evidence, or when tiebreak is explicitly requested in state.
    """
    if not isinstance(inv_state, Mapping):
        return False
    if inv_state.get("requires_tiebreak") is not None:
        return bool(inv_state.get("requires_tiebreak"))
    if inv_state.get("tiebreak_required") is not None:
        return bool(inv_state.get("tiebreak_required"))
    if inv_state.get("tiebreak_skipped") is True:
        return False

    # Explicit disputed data points or tiebreak trigger
    disputed_points = inv_state.get("disputed_data_points") or []
    if disputed_points:
        return True

    # Check for deadlocked opposite interpretations on shared battlefield with unresolved claims
    claims = [c for c in (inv_state.get("claims") or []) if isinstance(c, Mapping)]
    unresolved_ids = set(inv_state.get("unresolved_claim_ids") or [])
    unresolved_claims = [
        c for c in claims
        if c.get("claim_id") in unresolved_ids or c.get("status") in ("unresolved", "open")
    ]

    bull_unresolved_bf = {
        str(c.get("battlefield")).strip()
        for c in unresolved_claims
        if (c.get("speaker_key") == "Bull" or c.get("stance") == "bull") and c.get("battlefield")
    }
    bear_unresolved_bf = {
        str(c.get("battlefield")).strip()
        for c in unresolved_claims
        if (c.get("speaker_key") == "Bear" or c.get("stance") == "bear") and c.get("battlefield")
    }
    shared_unresolved_bf = bull_unresolved_bf.intersection(bear_unresolved_bf)

    challenges = [ch for ch in (inv_state.get("challenges") or []) if isinstance(ch, Mapping)]
    open_fatal_major = [
        ch for ch in challenges
        if ch.get("severity") in ("fatal", "major") and ch.get("status") in ("open", "unresolved")
    ]

    if shared_unresolved_bf and open_fatal_major and bool(inv_state.get("force_tiebreak")):
        return True

    return False


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(self, max_debate_rounds=3, max_risk_discuss_rounds=3):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    def should_continue_analyst(self, state: AgentState):
        """Determine if an analyst node should continue (shared by all analyst types)."""
        messages = state["messages"]
        last_message = messages[-1]
        if getattr(last_message, "tool_calls", None):
            return "continue"
        return "done"

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""
        inv_state = state.get("investment_debate_state") or {}

        # Fail-closed check: if state is blocked by protocol failure
        if inv_state.get("blocked"):
            raise DebateProtocolError(
                f"Debate state is blocked (parse_status={inv_state.get('parse_status')}, "
                f"reason={inv_state.get('block_reason')}). Cannot route to next debate node."
            )

        # Count accepted valid messages
        count = safe_int(inv_state.get("count", 0), 0)
        round_messages = inv_state.get("round_messages", [])
        accepted_valid = [
            m for m in round_messages
            if m.get("accepted", True) and m.get("parse_status") == "valid"
        ]
        effective_count = len(accepted_valid) if round_messages else count

        v2_enabled = is_v2_debate_enabled(state) or is_v2_debate_enabled(inv_state)

        # ── Legacy v1 Routing ──────────────────────────────────────────────
        if not v2_enabled:
            if effective_count >= 2 * self.max_debate_rounds:
                return "Research Manager"
            if inv_state.get("current_speaker", "").startswith("Bull"):
                return "Bear Researcher"
            return "Bull Researcher"

        # ── V2 Three-Stage Protocol Routing ────────────────────────────────
        if effective_count <= 0:
            return "Bull Researcher"
        elif effective_count == 1:
            return "Bear Researcher"
        elif effective_count == 2:
            # Opening complete -> enter challenge stage with Bull (msg 3)
            return "Bull Researcher"
        elif effective_count == 3:
            # Bull challenge complete -> Bear challenge next (msg 4)
            return "Bear Researcher"
        elif effective_count == 4:
            # Challenge stage complete (effective_count == 4)
            # Decide whether to enter tiebreak or route directly to manager
            if should_enter_tiebreak(inv_state):
                inv_state["tiebreak_skipped"] = False
                if "tiebreak_skipped" in state:
                    state["tiebreak_skipped"] = False
                inv_state["protocol_stage"] = "tiebreak"
                if "protocol_stage" in state:
                    state["protocol_stage"] = "tiebreak"
                return "Bull Researcher"
            else:
                inv_state["tiebreak_skipped"] = True
                if "tiebreak_skipped" in state:
                    state["tiebreak_skipped"] = True
                inv_state["protocol_stage"] = "manager"
                if "protocol_stage" in state:
                    state["protocol_stage"] = "manager"
                return "Research Manager"
        elif effective_count == 5:
            # Bull tiebreak complete -> Bear tiebreak next (msg 6)
            return "Bear Researcher"
        else:
            # Tiebreak round complete (effective_count >= 6, max 1 round)
            inv_state["protocol_stage"] = "manager"
            if "protocol_stage" in state:
                state["protocol_stage"] = "manager"
            return "Research Manager"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Risk Judge"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"

    def should_revise_after_risk_judge(self, state: AgentState) -> str:
        """Determine whether the trader must revise the plan after the risk judge."""
        feedback = state.get("risk_feedback_state", {})
        if (
            feedback.get("revision_required")
            and safe_int(feedback.get("retry_count", 0), 0) <= safe_int(feedback.get("max_retries", 1), 1)
        ):
            return "Trader"
        return "END"
