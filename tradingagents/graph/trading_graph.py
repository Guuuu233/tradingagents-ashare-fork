# TradingAgents/graph/trading_graph.py

import copy
import os
import re
from pathlib import Path
import json
import logging
from typing import Dict, Any, List, Optional, Union, TypedDict


from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from tradingagents.llm_clients import create_llm_client, resolve_role_base_url

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.dataflows.config import set_config
from tradingagents.agents.utils.prompt_injection import DEFAULT_PLACEMENT

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news,
    get_board_fund_flow,
    get_individual_fund_flow,
    get_lhb_detail,
)

from .conditional_logic import ConditionalLogic
from .data_collector import DataCollector
from .intent_parser import parse_intent
from .horizon_profile import HorizonResolution
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .report_quality_gate import apply_report_quality_gate
from tradingagents.agents.utils.price_ref_registry import audit_price_ref_registry
from tradingagents.agents.utils.price_basis_gate import (
    GATE_BLOCKED_GAP,
    PRICE_REF_CONTRACT_VERSION,
    enforce_price_basis_gate,
)
from .signal_processing import SignalProcessor
from tradingagents.agents.utils.agent_states import get_protocol_metadata
from tradingagents.agents.utils.debate_metrics import calculate_all_debate_metrics
from tradingagents.agents.utils.shadow_credit import calculate_shadow_credit_metrics
from tradingagents.agents.utils.model_tier_warning import check_model_tier_warnings


_logger = logging.getLogger(__name__)


class GameTheoryWiringError(RuntimeError):
    """Raised when Game Theory node cannot be wired into the trading graph."""

    def __init__(self, message: str, reason_code: str = "wiring_failed"):
        super().__init__(message)
        self.reason_code = reason_code


class GameTheoryUnavailable(TypedDict, total=False):
    status: str
    reason_code: str
    message: str
    error_type: Optional[str]
    wired: bool


def _state_logging_enabled() -> bool:
    """Whether full-state eval_results logging is enabled.

    Disabled by default; opt in via TA_TRACE=1 (or true/yes/on) or TA_STATE_LOGS=1.
    When enabled, logs are written under TA_RESULTS_DIR (default ./results) instead of a
    hardcoded eval_results/ directory.
    """
    raw = os.getenv("TA_TRACE")
    if raw is not None and raw.strip().lower() in ("1", "true", "yes", "on"):
        return True
    raw_state = os.getenv("TA_STATE_LOGS")
    return raw_state is not None and raw_state.strip().lower() in ("1", "true", "yes", "on")


class _LogStatesDict(dict):
    """Dictionary supporting horizon-isolated state keys while retaining backward compatibility."""

    def __getitem__(self, key: Any) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        # Fallback for legacy trade_date lookup: e.g. "2026-08-26" -> "2026-08-26_short"
        if isinstance(key, str):
            prefix = f"{key}_"
            matches = [v for k, v in self.items() if isinstance(k, str) and k.startswith(prefix)]
            if len(matches) == 1:
                return matches[0]
        raise KeyError(key)

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: Any) -> bool:
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            prefix = f"{key}_"
            matches = [k for k in self.keys() if isinstance(k, str) and k.startswith(prefix)]
            if len(matches) == 1:
                return True
        return False


def _summarize_social_context(ctx: Optional[Union[Dict[str, Any], Any]]) -> Dict[str, Any]:
    """Summarize social_data_context into a privacy-safe structure for state logging (M4).

    Includes safe metadata: mode, status, requested_as_of, direction_allowed, reason counts and codes.
    Strictly forbidden: raw text/content, user cookies, access tokens, or personal identifiers.
    """
    if not isinstance(ctx, dict):
        return {}
    reasons = ctx.get("reason_codes") or []
    ledger = ctx.get("data_failure_ledger") or []
    bundle = ctx.get("bundle") if isinstance(ctx.get("bundle"), dict) else {}
    evidence_count = len(bundle.get("evidence_summary", [])) if isinstance(bundle, dict) else 0
    return {
        "mode": ctx.get("mode", "disabled"),
        "status": ctx.get("status", "not_applicable"),
        "requested_as_of": ctx.get("requested_as_of", ""),
        "direction_allowed": bool(ctx.get("direction_allowed", False)),
        "reason_count": len(reasons),
        "reason_codes": list(reasons),
        "ledger_count": len(ledger),
        "evidence_count": evidence_count,
        "bundle_id": (bundle.get("bundle_id") if isinstance(bundle, dict) else None) or "none",
    }


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    # Class-level cache for persistence to handle concurrency
    _shared_checkpointer = None

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals", "macro", "smart_money", "volume_price"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
        data_collector: Optional["DataCollector"] = None,
        custom_prompts: Optional[Dict[str, str]] = None,
        custom_prompt_placement: str = DEFAULT_PLACEMENT,
        strict_game_theory_wiring: Optional[bool] = None,
    ):
        """Initialize the trading agents graph and components."""
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []
        self.custom_prompts: Dict[str, str] = custom_prompts if custom_prompts is not None else {}
        self.custom_prompt_placement: str = custom_prompt_placement

        # Determine strict wiring mode: explicit arg takes precedence, then config flag (DAV-881 / P0-B)
        if strict_game_theory_wiring is not None:
            self.strict_game_theory_wiring = bool(strict_game_theory_wiring)
        else:
            self.strict_game_theory_wiring = bool(self.config.get("strict_game_theory_wiring", False))

        self.game_theory_wired: bool = False
        self.game_theory_unavailable: Optional[Dict[str, Any]] = None

        # Update the interface's config
        set_config(self.config)

        # Initialize persistence (Singleton Pattern for concurrency)
        if TradingAgentsGraph._shared_checkpointer is None:
            TradingAgentsGraph._shared_checkpointer = MemorySaver()
        
        self.checkpointer = TradingAgentsGraph._shared_checkpointer

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        # Resolve per-role model configurations
        db = self.config.get("db")
        user_id = self.config.get("user_id")
        resolved_roles = {}
        try:
            from api.services.role_routing_service import resolve_all_roles
            if db:
                resolved_roles = resolve_all_roles(db, user_id, runtime_config=self.config)
            elif user_id:
                from api.database import get_db_ctx
                with get_db_ctx() as session:
                    resolved_roles = resolve_all_roles(session, user_id, runtime_config=self.config)
        except Exception as err:
            _logger.warning("[TradingAgentsGraph] Failed to resolve role configs: %s", err)

        self.role_llms = {}
        self.role_resolved_configs = resolved_roles

        from api.services.role_routing_service import ALL_ROLES, ROLE_DEFAULT_TIERS
        for role_key in ALL_ROLES:
            r_cfg = resolved_roles.get(role_key, {})
            p_type = r_cfg.get("provider_type") or self.config.get("llm_provider") or "openai"
            default_tier = ROLE_DEFAULT_TIERS.get(role_key, "quick")
            m_name = r_cfg.get("model_name") or (self.config.get("deep_think_llm") if default_tier == "deep" else self.config.get("quick_think_llm")) or "gpt-4o-mini"
            b_url = resolve_role_base_url(
                role_provider=p_type,
                role_base_url=r_cfg.get("base_url"),
                global_provider=self.config.get("llm_provider", "openai"),
                global_base_url=self.config.get("backend_url"),
            )
            a_key = r_cfg.get("api_key") or self.config.get("api_key")

            r_kwargs = dict(llm_kwargs)
            if r_cfg.get("temperature") is not None:
                r_kwargs["temperature"] = r_cfg["temperature"]
            if r_cfg.get("max_tokens") is not None:
                r_kwargs["max_tokens"] = r_cfg["max_tokens"]
            if a_key:
                r_kwargs["api_key"] = a_key

            role_client = create_llm_client(
                provider=p_type,
                model=m_name,
                base_url=b_url,
                **r_kwargs,
            )
            self.role_llms[role_key] = role_client.get_llm()

        deep_client = create_llm_client(
            provider=self.config.get("llm_provider", "openai"),
            model=self.config.get("deep_think_llm", "gpt-4o"),
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config.get("llm_provider", "openai"),
            model=self.config.get("quick_think_llm", "gpt-4o-mini"),
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = self.role_llms.get("research_manager", deep_client.get_llm())
        self.quick_thinking_llm = self.role_llms.get("market", quick_client.get_llm())
        
        # Initialize memories
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.risk_manager_memory = FinancialSituationMemory("risk_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Data collector — fetches once, shared across dual-horizon runs
        self.data_collector = data_collector if data_collector is not None else DataCollector()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config.get("max_debate_rounds", 3),
            max_risk_discuss_rounds=self.config.get("max_risk_discuss_rounds", 3),
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.risk_manager_memory,
            self.conditional_logic,
            data_collector=self.data_collector,
            role_llms=self.role_llms,
            custom_prompts=self.custom_prompts,
            custom_prompt_placement=self.custom_prompt_placement,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100)
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = _LogStatesDict()  # date/horizon to full state dict

        # Set up the graph with checkpointer
        raw_graph = self.graph_setup.setup_graph(selected_analysts, checkpointer=self.checkpointer)
        self.graph = self._wire_game_theory_into_graph(raw_graph)

    def _wire_game_theory_into_graph(self, raw_graph: Any) -> Any:
        """Wire Game Theory node into the compiled graph with hardened contract (P0-B / DAV-881)."""
        if raw_graph is None:
            self.game_theory_wired = False
            self.game_theory_unavailable = {
                "status": "unavailable",
                "reason_code": "raw_graph_none",
                "message": "Raw graph is None; Game Theory node cannot be wired",
                "error_type": "ValueError",
                "wired": False,
            }
            if getattr(self, "strict_game_theory_wiring", False):
                raise GameTheoryWiringError(
                    self.game_theory_unavailable["message"],
                    reason_code="raw_graph_none",
                )
            _logger.warning("[TradingAgentsGraph] %s", self.game_theory_unavailable["message"])
            return raw_graph

        try:
            from unittest.mock import Mock
            if isinstance(raw_graph, Mock):
                self.game_theory_wired = False
                self.game_theory_unavailable = {
                    "status": "unavailable",
                    "reason_code": "mock_graph_unwired",
                    "message": "Raw graph is a Mock instance; Game Theory node not wired",
                    "error_type": "MockType",
                    "wired": False,
                }
                if getattr(self, "strict_game_theory_wiring", False):
                    raise GameTheoryWiringError(
                        self.game_theory_unavailable["message"],
                        reason_code="mock_graph_unwired",
                    )
                return raw_graph
        except ImportError:
            pass

        builder = getattr(raw_graph, "builder", None)
        if builder is None or not hasattr(builder, "nodes") or not hasattr(builder, "edges"):
            self.game_theory_wired = False
            self.game_theory_unavailable = {
                "status": "unavailable",
                "reason_code": "builder_missing",
                "message": "Graph builder is missing or lacks required nodes/edges attributes",
                "error_type": "AttributeError",
                "wired": False,
            }
            if getattr(self, "strict_game_theory_wiring", False):
                raise GameTheoryWiringError(
                    self.game_theory_unavailable["message"],
                    reason_code="builder_missing",
                )
            _logger.warning("[TradingAgentsGraph] %s", self.game_theory_unavailable["message"])
            return raw_graph

        try:
            from .game_theory_node import wire_game_theory_node, NODE_NAME
            builder.compiled = False
            wire_game_theory_node(
                builder,
                llm=getattr(self, "quick_thinking_llm", None),
                data_collector=getattr(self, "data_collector", None),
            )
            compiled_graph = builder.compile(checkpointer=getattr(self, "checkpointer", None))

            if hasattr(compiled_graph, "nodes") and NODE_NAME not in compiled_graph.nodes:
                raise GameTheoryWiringError(
                    f"Node '{NODE_NAME}' missing from compiled graph nodes after wiring",
                    reason_code="node_missing_after_compile",
                )

            self.game_theory_wired = True
            self.game_theory_unavailable = None
            _logger.info("[TradingAgentsGraph] Successfully wired Game Theory node into graph")
            return compiled_graph
        except Exception as exc:
            reason_code = getattr(exc, "reason_code", None)
            if not reason_code:
                msg = str(exc)
                if "anchor" in msg or "Research Manager" in msg:
                    reason_code = "missing_anchor_edge"
                elif "compile" in msg.lower():
                    reason_code = "compile_failed"
                else:
                    reason_code = "wiring_exception"

            self.game_theory_wired = False
            self.game_theory_unavailable = {
                "status": "unavailable",
                "reason_code": reason_code,
                "message": str(exc),
                "error_type": type(exc).__name__,
                "wired": False,
            }
            _logger.warning(
                "[TradingAgentsGraph] Failed to wire game theory node (%s): %s",
                reason_code,
                exc,
            )
            if getattr(self, "strict_game_theory_wiring", False):
                raise GameTheoryWiringError(
                    f"Failed to wire Game Theory node into graph: {exc}",
                    reason_code=reason_code,
                ) from exc
            return raw_graph

    def _ensure_game_theory_state(self, state: Dict[str, Any], horizon: str = "short") -> None:
        """Enforce the Game Theory contract on final graph state (DAV-881 / P0-B).

        Guarantees:
        1. If the node ran and completed (normal or degraded):
           - Preserves existing game_theory_report and game_theory_signals verbatim.
           - Strictly retains valid fail-closed degradations (Requirement 2).
        2. If the node did not run or was not wired:
           - Injects typed game_theory_unavailable, reason_code, degraded report, and fail trace.
           - Forbids disguising unwired/unexecuted state as normal completion.
           - Ensures game_theory_signals is strictly None (atomic consistency with degraded report).
        """
        if not isinstance(state, dict):
            return

        report = state.get("game_theory_report")

        # Case 1: Node already executed and produced report (normal report or explicit fail-closed)
        if report is not None and str(report).strip() != "":
            if state.get("game_theory_signals") is None and not str(report).startswith("【博弈论分析不可用】"):
                state["game_theory_report"] = "【博弈论分析不可用】原因：信号计算缺失，保持原子性降级。"
            return

        # Case 2: Node was not wired or did not execute
        from .game_theory_node import AGENT_NAME

        if not getattr(self, "game_theory_wired", False):
            unavail = getattr(self, "game_theory_unavailable", None) or {
                "status": "unavailable",
                "reason_code": "not_wired",
                "message": "博弈论节点未挂载到分析图中",
                "error_type": "WiringNotApplied",
                "wired": False,
            }
        else:
            route_info = state.get("integrity_route") or "unreached"
            unavail = {
                "status": "unavailable",
                "reason_code": "route_unreached",
                "message": f"图执行未到达博弈论节点（流程早停或未被路由，route={route_info}）",
                "error_type": "RouteUnreached",
                "wired": True,
            }

        reason_code = unavail.get("reason_code", "unknown")
        message = unavail.get("message", "")

        state["game_theory_report"] = f"【博弈论分析不可用】原因：博弈论接线未生效或未被路由（{reason_code}: {message}），该项未在图路由中执行。"
        state["game_theory_signals"] = None
        state["game_theory_unavailable"] = unavail

        fail_trace = {
            "agent": AGENT_NAME,
            "horizon": horizon,
            "data_window": "短期博弈",
            "key_finding": f"博弈论分析未执行: {reason_code}",
            "verdict": "中性",
            "confidence": "低",
            "source_status": "failed",
            "source_mode": "deterministic_game_theory",
            "bundle_id": "game_theory_v1",
            "direction_allowed": False,
            "reason_codes": [f"wiring_or_routing_{reason_code}"],
            "evidence_refs": [],
            "financial_period_compliance": {},
        }

        traces = state.get("analyst_traces")
        if traces is None or not isinstance(traces, list):
            state["analyst_traces"] = [fail_trace]
        else:
            if not any(isinstance(t, dict) and t.get("agent") == AGENT_NAME for t in traces):
                traces.append(fail_trace)

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level
            api_key = self.config.get("api_key")
            if api_key:
                kwargs["api_key"] = api_key

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort
            api_key = self.config.get("api_key")
            if api_key:
                kwargs["api_key"] = api_key

        elif provider == "anthropic":
            api_key = self.config.get("api_key")
            if api_key:
                kwargs["api_key"] = api_key

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # Social analyst does not use news fallback tools (Task 10 / D-009 / D-010)
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
            "macro": ToolNode(
                [
                    # Macro analyst tools
                    get_board_fund_flow,
                    get_news,
                ]
            ),
            "smart_money": ToolNode(
                [
                    # Smart money analyst tools
                    get_individual_fund_flow,
                    get_lhb_detail,
                    get_indicators,
                ]
            ),
            "volume_price": ToolNode(
                [
                    # Volume price analyst tools (fallback, normally uses data_collector)
                    get_stock_data,
                ]
            ),
        }

    def propagate(
        self,
        company_name,
        trade_date,
        user_context: Optional[Dict[str, Any]] = None,
        selected_analysts: Optional[List[str]] = None,
        request_source: str = "api",
        thread_id: Optional[str] = None,
        horizon_resolution: Optional[Any] = None,
        horizon: Optional[str] = None,
    ):
        """Run the trading agents graph for a company on a specific date."""

        self.ticker = company_name

        effective_horizon = horizon
        if effective_horizon is None:
            if isinstance(horizon_resolution, HorizonResolution) and len(horizon_resolution.resolved) == 1:
                effective_horizon = horizon_resolution.resolved[0]
            elif isinstance(horizon_resolution, dict) and len(horizon_resolution.get("resolved", [])) == 1:
                effective_horizon = horizon_resolution["resolved"][0]
            else:
                effective_horizon = "short"

        collected = self.data_collector.collect(company_name, trade_date)
        market_data_context = (
            collected.get("market_data_context")
            if isinstance(collected, dict)
            else None
        )
        social_data_context = (
            collected.get("social_data_context")
            if isinstance(collected, dict)
            else None
        )

        # Initialize state
        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            user_context=user_context,
            selected_analysts=selected_analysts,
            request_source=request_source,
            market_data_context=market_data_context,
            social_data_context=social_data_context,
            runtime_config=self.config,
            horizon_resolution=horizon_resolution,
            horizon=effective_horizon,
        )
        args = self.propagator.get_graph_args()

        state_horizon = init_agent_state.get("horizon") or effective_horizon

        # Use thread_id for checkpointer
        if thread_id:
            args["config"]["configurable"] = {"thread_id": thread_id}
        elif not args["config"].get("configurable"):
            # Default fallback for standalone runs: isolate by horizon to prevent thread collision
            args["config"]["configurable"] = {"thread_id": f"{company_name}_{trade_date}_{state_horizon}"}

        if self.debug:
            # Debug mode with tracing
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)

            final_state = trace[-1]
        else:
            # Standard mode without tracing
            final_state = self.graph.invoke(init_agent_state, **args)

        self._ensure_game_theory_state(final_state, horizon=effective_horizon)

        # Store current state for reflection
        self.curr_state = final_state
        apply_report_quality_gate(final_state)
        # DAV-1198: bypass-only price_ref registry audit
        audit_price_ref_registry(final_state)
        # DAV-1199: price-basis hard gate — violations fail-close (non-executable)
        gate = enforce_price_basis_gate(final_state)

        # Log state
        self._log_state(trade_date, final_state)

        # Return decision and processed signal
        signal = self.process_signal(final_state["final_trade_decision"])
        if gate.get("status") == "blocked" and signal in ("BUY", "SELL"):
            # fail-close: a gate-blocked run must not emit a directional signal
            signal = "NO_TRADE"
        return final_state, signal

    async def propagate_async(
        self,
        company_name: str,
        trade_date: str,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a single integrated analysis.

        Each analyst uses its own natural time window (technical/funds → short,
        fundamentals/macro → medium). The graph runs once; Research Manager
        synthesizes both short and long term perspectives.

        Returns a dict with short_term result and user_intent.
        """
        self.ticker = company_name

        # Parse intent from query, or build a minimal intent from ticker alone
        if query:
            user_intent = parse_intent(query, self.quick_thinking_llm, fallback_ticker=company_name)
            ticker = user_intent.get("ticker") or company_name
        else:
            ticker = company_name
            user_intent = {
                "raw_query": "",
                "ticker": ticker,
                "horizons": ["short"],
                "focus_areas": [],
                "specific_questions": [],
                "user_context": {},
            }

        # Pre-collect data once (always full data); analysts will read from cache
        _logger.info("[TradingAgentsGraph] Collecting data for %s %s…", ticker, trade_date)
        collected = self.data_collector.collect(ticker, trade_date)
        market_data_context = (
            collected.get("market_data_context")
            if isinstance(collected, dict)
            else None
        )
        social_data_context = (
            collected.get("social_data_context")
            if isinstance(collected, dict)
            else None
        )

        graph_args = self.propagator.get_graph_args()

        state = self.propagator.create_initial_state(
            ticker,
            trade_date,
            user_intent=user_intent,
            horizon="short",
            market_data_context=market_data_context,
            social_data_context=social_data_context,
            runtime_config=self.config,
        )
        final_state = await self.graph.ainvoke(state, **graph_args)

        self._ensure_game_theory_state(final_state, horizon="short")

        # Evict cached data to free memory
        self.data_collector.evict(ticker, trade_date)

        result = self._build_horizon_result("short", final_state)

        self._log_state_dual(trade_date, result, {}, user_intent)

        return {
            "short_term": result,
            "medium_term": None,
            "user_intent": user_intent,
            "analysis_baseline_date": trade_date,
            "data_as_of": result.get("data_as_of"),
            "data_gaps": list(result.get("data_gaps") or []),
            "market_data_context": market_data_context,
            "social_data_context": social_data_context,
        }

    def _build_horizon_result(self, horizon: str, final_state: Dict[str, Any]) -> Dict[str, Any]:
        """Extract a compact result dict from a completed graph state."""
        apply_report_quality_gate(final_state)
        # DAV-1198: bypass-only price_ref registry audit
        audit_price_ref_registry(final_state)
        # DAV-1199: price-basis hard gate — violations fail-close (non-executable)
        enforce_price_basis_gate(final_state)
        market_context = final_state.get("market_context", {})
        trade_date = final_state.get("trade_date", "")
        market_data_context = final_state.get("market_data_context", {})
        social_data_context = final_state.get("social_data_context", {})
        daily_context = market_data_context.get("daily", {}) if isinstance(market_data_context, dict) else {}
        failure_ledger = market_data_context.get("data_failure_ledger", []) if isinstance(market_data_context, dict) else []
        social_failure_ledger = social_data_context.get("data_failure_ledger", []) if isinstance(social_data_context, dict) else []

        valid_failure_statuses = {"failed", "timeout", "unavailable", "refused", "error"}
        data_gaps: List[str] = []
        for entry in failure_ledger:
            if isinstance(entry, dict) and entry.get("gap"):
                status = entry.get("status")
                if status is None or str(status).lower() in valid_failure_statuses:
                    gap_str = str(entry["gap"])
                    if gap_str not in data_gaps:
                        data_gaps.append(gap_str)

        for entry in social_failure_ledger:
            if isinstance(entry, dict) and entry.get("gap"):
                status = entry.get("status")
                if status is not None and str(status).lower() in valid_failure_statuses:
                    gap_str = str(entry["gap"])
                    if gap_str not in data_gaps:
                        data_gaps.append(gap_str)

        gt_unavail = final_state.get("game_theory_unavailable") or (
            getattr(self, "game_theory_unavailable", None)
            if not getattr(self, "game_theory_wired", True)
            else None
        )
        if gt_unavail and "game_theory_unavailable" not in data_gaps:
            data_gaps.append("game_theory_unavailable")

        # DAV-1199: gate-blocked runs are fail-closed into data_gaps.
        gate_payload = final_state.get("price_basis_gate")
        if (
            isinstance(gate_payload, dict)
            and gate_payload.get("status") == "blocked"
            and GATE_BLOCKED_GAP not in data_gaps
        ):
            data_gaps.append(GATE_BLOCKED_GAP)

        raw_inv_state = final_state.get("investment_debate_state")
        inv_state = dict(raw_inv_state) if isinstance(raw_inv_state, dict) else None

        result = {
            "horizon": horizon,
            "company_of_interest": final_state.get("company_of_interest", ""),
            "trade_date": trade_date,
            "analysis_baseline_date": trade_date,
            "data_as_of": daily_context.get("as_of"),
            "data_gaps": data_gaps,
            "market_context": market_context,
            "market_data_context": market_data_context,
            "social_data_context": social_data_context,
            "final_trade_decision": final_state.get("final_trade_decision", ""),
            "investment_plan": final_state.get("investment_plan", ""),
            "trader_investment_plan": final_state.get("trader_investment_plan", ""),
            "investment_debate_state": inv_state,
            "manager_verdict": final_state.get("manager_verdict") or (raw_inv_state.get("manager_verdict") if isinstance(raw_inv_state, dict) else None),
            "evidence_verification": final_state.get("evidence_verification") or (raw_inv_state.get("evidence_verification") if isinstance(raw_inv_state, dict) else []),
            "report_manifest": final_state.get("report_manifest") or (raw_inv_state.get("report_manifest") if isinstance(raw_inv_state, dict) else None),
            "risk_debate_state": final_state.get("risk_debate_state"),
            "risk_feedback_state": final_state.get("risk_feedback_state"),
            "fund_flow_consensus_guard": (
                final_state.get("fund_flow_consensus_guard")
                if final_state.get("fund_flow_consensus_guard") and final_state.get("fund_flow_consensus_guard", {}).get("status") != "not_checked"
                else (
                    (market_data_context.get("fund_flow_consensus_guard") if isinstance(market_data_context, dict) else None)
                    or final_state.get("fund_flow_consensus_guard")
                    or {"blocked": True, "direction_allowed": False, "status": "not_checked"}
                )
            ),
            "analyst_traces": final_state.get("analyst_traces", []),
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "macro_report": final_state.get("macro_report", ""),
            "smart_money_report": final_state.get("smart_money_report", ""),
            "volume_price_report": final_state.get("volume_price_report", ""),
            "game_theory_report": final_state.get("game_theory_report"),
            "game_theory_signals": final_state.get("game_theory_signals"),
            "game_theory_unavailable": gt_unavail,
            # D-009 P0-1 status fields (must survive dual-horizon packaging)
            "run_integrity": final_state.get("run_integrity"),
            "decision_status": final_state.get("decision_status"),
            "analysis_status": final_state.get("analysis_status"),
            "trade_action": final_state.get("trade_action"),
            "risk_status": final_state.get("risk_status"),
            "horizon_run_metadata": copy.deepcopy(final_state.get("horizon_run_metadata")) if isinstance(final_state.get("horizon_run_metadata"), dict) else final_state.get("horizon_run_metadata"),
            # DAV-1198 bypass-only price_ref audit fields (preview semantics; no decision effect)
            "price_refs": final_state.get("price_refs"),
            "price_basis_gaps": final_state.get("price_basis_gaps"),
            "price_basis_validation": final_state.get("price_basis_validation"),
            # DAV-1199 hard gate + contract versioning (no DB schema migration)
            "price_basis_gate": final_state.get("price_basis_gate"),
            "price_ref_contract_version": PRICE_REF_CONTRACT_VERSION,
            "price_basis_version": final_state.get("price_basis_version"),
        }

        # Normalize protocol metadata and compute debate metrics without mutating final_state
        meta = get_protocol_metadata(final_state)
        data_utilization_metrics = calculate_all_debate_metrics(result)
        shadow_credit_metrics = meta.get("shadow_credit_metrics")
        if not shadow_credit_metrics or shadow_credit_metrics == {}:
            shadow_credit_metrics = calculate_shadow_credit_metrics(result)
        model_tier_warning = check_model_tier_warnings(
            result,
            role_resolved_configs=getattr(self, "role_resolved_configs", {}),
        )

        if inv_state is not None:
            if (
                "protocol_version" in raw_inv_state
                or "feature_flags" in raw_inv_state
                or "data_utilization_metrics" in raw_inv_state
            ):
                inv_state["protocol_version"] = meta["protocol_version"]
                inv_state["protocol_stage"] = meta["protocol_stage"]
                inv_state["tiebreak_skipped"] = meta["tiebreak_skipped"]
                inv_state["debate_degenerate"] = meta["debate_degenerate"]
                inv_state["data_utilization_metrics"] = data_utilization_metrics
                inv_state["challenge_verification"] = meta["challenge_verification"]
                inv_state["shadow_credit_metrics"] = shadow_credit_metrics
                inv_state["feature_flags"] = meta["feature_flags"]
                inv_state["model_tier_warning"] = model_tier_warning
                inv_state["model_tier_warnings"] = model_tier_warning["warnings"]
                inv_state["model_tier_check"] = model_tier_warning

        result["protocol_version"] = meta["protocol_version"]
        result["protocol_stage"] = meta["protocol_stage"]
        result["tiebreak_skipped"] = meta["tiebreak_skipped"]
        result["debate_degenerate"] = meta["debate_degenerate"]
        result["data_utilization_metrics"] = data_utilization_metrics
        result["challenge_verification"] = meta["challenge_verification"]
        result["shadow_credit_metrics"] = shadow_credit_metrics
        result["feature_flags"] = meta["feature_flags"]
        result["model_tier_warning"] = model_tier_warning
        result["model_tier_warnings"] = model_tier_warning["warnings"]
        result["model_tier_check"] = model_tier_warning

        return result

    @staticmethod
    def _safe_ticker(ticker: str) -> str:
        """Sanitize ticker for use in filesystem paths."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", ticker) or "unknown"

    def _log_state_dual(
        self,
        trade_date: str,
        short_result: Dict[str, Any],
        medium_result: Dict[str, Any],
        user_intent: Dict[str, Any],
    ) -> None:
        """Log dual-horizon results to a JSON file."""
        ticker = self._safe_ticker(
            short_result.get("company_of_interest") or self.ticker or "unknown"
        )
        entry = {
            "user_intent": user_intent,
            "short_term": short_result,
            "medium_term": medium_result,
        }
        self.log_states_dict[str(trade_date)] = entry

        if not _state_logging_enabled():
            return
        directory = (
            Path(os.getenv("TA_RESULTS_DIR", "./results"))
            / ticker
            / "TradingAgentsStrategy_logs"
        )
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / f"dual_horizon_{trade_date}.json", "w") as f:
            json.dump(entry, f, indent=4, ensure_ascii=False)

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        if not isinstance(self.log_states_dict, _LogStatesDict):
            self.log_states_dict = _LogStatesDict(self.log_states_dict)

        horizon = final_state.get("horizon")
        if not horizon and isinstance(final_state.get("horizon_run_metadata"), dict):
            resolved = final_state["horizon_run_metadata"].get("resolved")
            if resolved and len(resolved) == 1:
                horizon = resolved[0]

        log_key = f"{trade_date}_{horizon}" if horizon else str(trade_date)

        inv_state = final_state.get("investment_debate_state")
        if isinstance(inv_state, dict):
            logged_inv_state = {
                "bull_history": inv_state.get("bull_history", ""),
                "bear_history": inv_state.get("bear_history", ""),
                "history": inv_state.get("history", ""),
                "current_speaker": inv_state.get("current_speaker", ""),
                "current_response": inv_state.get("current_response", ""),
                "judge_decision": inv_state.get("judge_decision", ""),
                "claims": inv_state.get("claims", []),
                "round_messages": inv_state.get("round_messages", []),
                "focus_claim_ids": inv_state.get("focus_claim_ids", []),
                "open_claim_ids": inv_state.get("open_claim_ids", []),
                "resolved_claim_ids": inv_state.get("resolved_claim_ids", []),
                "unresolved_claim_ids": inv_state.get("unresolved_claim_ids", []),
                "round_summary": inv_state.get("round_summary", ""),
                "round_goal": inv_state.get("round_goal", ""),
                "manager_verdict": inv_state.get("manager_verdict"),
                "evidence_verification": inv_state.get("evidence_verification", []),
                "report_manifest": inv_state.get("report_manifest"),
            }
        else:
            logged_inv_state = {}

        risk_state = final_state.get("risk_debate_state")
        if isinstance(risk_state, dict):
            logged_risk_state = {
                "aggressive_history": risk_state.get("aggressive_history", ""),
                "conservative_history": risk_state.get("conservative_history", ""),
                "neutral_history": risk_state.get("neutral_history", ""),
                "history": risk_state.get("history", ""),
                "judge_decision": risk_state.get("judge_decision", ""),
                "claims": risk_state.get("claims", []),
                "focus_claim_ids": risk_state.get("focus_claim_ids", []),
                "open_claim_ids": risk_state.get("open_claim_ids", []),
                "resolved_claim_ids": risk_state.get("resolved_claim_ids", []),
                "unresolved_claim_ids": risk_state.get("unresolved_claim_ids", []),
                "round_summary": risk_state.get("round_summary", ""),
                "round_goal": risk_state.get("round_goal", ""),
            }
        else:
            logged_risk_state = {}

        entry = {
            "company_of_interest": final_state.get("company_of_interest") or self.ticker or "unknown",
            "trade_date": final_state.get("trade_date") or str(trade_date),
            "horizon": horizon,
            "horizon_run_metadata": copy.deepcopy(final_state.get("horizon_run_metadata")) if isinstance(final_state.get("horizon_run_metadata"), dict) else final_state.get("horizon_run_metadata"),
            "instrument_context": final_state.get("instrument_context", {}),
            "market_context": final_state.get("market_context", {}),
            "market_data_context": final_state.get("market_data_context", {}),
            "social_data_context": _summarize_social_context(final_state.get("social_data_context")),
            "fund_flow_consensus_guard": (
                final_state.get("fund_flow_consensus_guard")
                if final_state.get("fund_flow_consensus_guard") and final_state.get("fund_flow_consensus_guard", {}).get("status") != "not_checked"
                else (
                    (final_state.get("market_data_context", {}).get("fund_flow_consensus_guard") if isinstance(final_state.get("market_data_context"), dict) else None)
                    or final_state.get("fund_flow_consensus_guard", {})
                )
            ),
            "user_context": final_state.get("user_context", {}),
            "workflow_context": final_state.get("workflow_context", {}),
            "market_report": final_state.get("market_report", ""),
            "sentiment_report": final_state.get("sentiment_report", ""),
            "news_report": final_state.get("news_report", ""),
            "fundamentals_report": final_state.get("fundamentals_report", ""),
            "macro_report": final_state.get("macro_report", ""),
            "smart_money_report": final_state.get("smart_money_report", ""),
            "volume_price_report": final_state.get("volume_price_report", ""),
            "investment_debate_state": logged_inv_state,
            "manager_verdict": final_state.get("manager_verdict") or (inv_state.get("manager_verdict") if isinstance(inv_state, dict) else None),
            "evidence_verification": final_state.get("evidence_verification") or (inv_state.get("evidence_verification") if isinstance(inv_state, dict) else []),
            "report_manifest": final_state.get("report_manifest") or (inv_state.get("report_manifest") if isinstance(inv_state, dict) else None),
            "trader_investment_decision": final_state.get("trader_investment_plan", ""),
            "risk_debate_state": logged_risk_state,
            "risk_feedback_state": final_state.get("risk_feedback_state", {}),
            "investment_plan": final_state.get("investment_plan", ""),
            "final_trade_decision": final_state.get("final_trade_decision", ""),
        }
        self.log_states_dict[log_key] = entry

        # Save to file — only when state logging is explicitly enabled; writes
        # land under TA_RESULTS_DIR instead of a hardcoded eval_results/ dir.
        if not _state_logging_enabled():
            return
        safe_ticker = self._safe_ticker(self.ticker or final_state.get("company_of_interest") or "unknown")
        directory = (
            Path(os.getenv("TA_RESULTS_DIR", "./results"))
            / safe_ticker
            / "TradingAgentsStrategy_logs"
        )
        directory.mkdir(parents=True, exist_ok=True)

        filename = f"full_states_log_{trade_date}_{horizon}.json" if horizon else f"full_states_log_{trade_date}.json"
        with open(directory / filename, "w", encoding="utf-8") as f:
            json.dump({log_key: entry}, f, indent=4, ensure_ascii=False)

    @classmethod
    def read_state_log(
        cls,
        ticker: str,
        trade_date: str,
        horizon: Optional[str] = None,
        results_dir: Optional[Union[str, Path]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Read state log from disk for a ticker, trade_date, and horizon.

        Handles:
        - Horizon-isolated files: full_states_log_{trade_date}_{horizon}.json
        - Legacy files: full_states_log_{trade_date}.json (without horizon suffix).
          Legacy files are marked as horizon='legacy' and NEVER interpreted as a T+40 / medium run.
        """
        base_dir = Path(results_dir or os.getenv("TA_RESULTS_DIR", "./results"))
        safe_ticker = cls._safe_ticker(ticker)
        directory = base_dir / safe_ticker / "TradingAgentsStrategy_logs"
        if not directory.exists():
            return None

        # 1. If horizon is specified, check for horizon-specific log first
        if horizon:
            horizon_str = str(horizon).lower()
            file_path = directory / f"full_states_log_{trade_date}_{horizon_str}.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls._extract_entry_from_log_data(data, trade_date, horizon=horizon_str)
            # If specifically looking for medium, legacy files (without suffix) MUST NOT match
            if horizon_str == "medium":
                return None

        # 2. Check for legacy log file without horizon suffix
        legacy_file = directory / f"full_states_log_{trade_date}.json"
        if legacy_file.exists():
            with open(legacy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = cls._extract_entry_from_log_data(data, trade_date, horizon=None)
            if entry is not None:
                # Mark as legacy/unknown; never interpret as T+40 run
                return cls._mark_legacy_state(entry)

        return None

    @classmethod
    def _extract_entry_from_log_data(
        cls,
        data: Any,
        trade_date: str,
        horizon: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(data, dict):
            return None
        candidates = []
        if horizon:
            candidates.append(f"{trade_date}_{horizon}")
        candidates.append(str(trade_date))
        for k in candidates:
            if k in data and isinstance(data[k], dict):
                return dict(data[k])
        if "company_of_interest" in data or "final_trade_decision" in data:
            return dict(data)
        for v in data.values():
            if isinstance(v, dict):
                return dict(v)
        return None

    @classmethod
    def _mark_legacy_state(cls, entry: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(entry)
        curr_h = result.get("horizon")
        if not curr_h or str(curr_h).lower() in ("unknown", "legacy", "none"):
            result["horizon"] = "legacy"
        meta = result.get("horizon_run_metadata")
        if not isinstance(meta, dict):
            result["horizon_run_metadata"] = {
                "requested": None,
                "resolved": ["short"],
                "resolution_source": "legacy",
                "profile_id": "unknown",
                "primary_eval_offsets": {"short": 10},
                "cutoff": result.get("data_as_of"),
                "investment_horizon": None,
            }
        else:
            meta = dict(meta)
            meta["resolution_source"] = meta.get("resolution_source") or "legacy"
            meta["profile_id"] = meta.get("profile_id") or "unknown"
            offsets = meta.get("primary_eval_offsets") or {}
            meta["primary_eval_offsets"] = {
                k: v for k, v in offsets.items() if k != "medium" and v != 40
            }
            if not meta["primary_eval_offsets"]:
                meta["primary_eval_offsets"] = {"short": 10}
            result["horizon_run_metadata"] = meta
        return result

    def load_state_log(
        self,
        trade_date: str,
        horizon: Optional[str] = None,
        ticker: Optional[str] = None,
        results_dir: Optional[Union[str, Path]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Instance helper to load state log from disk."""
        return self.read_state_log(
            ticker=ticker or self.ticker or "unknown",
            trade_date=trade_date,
            horizon=horizon,
            results_dir=results_dir,
        )

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)
