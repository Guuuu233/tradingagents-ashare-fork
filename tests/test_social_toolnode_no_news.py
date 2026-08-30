"""Tests for social ToolNode removal of get_news (Task 10 / D-009 / D-010).

Specification:
- docs/social_data/implementation_plan.md Task 10
- D-009, D-010
- Behaviour contracts:
  1. social ToolNode tools list does not contain get_news.
  2. news ToolNode still contains get_news, get_global_news, get_insider_transactions.
  3. Other analyst tool nodes (market, fundamentals, macro, smart_money, volume_price) remain unchanged.
  4. No new social tools (e.g., get_social_sentiment_bundle) are registered.
  5. tradingagents/agents/utils/social_data_tools.py does not exist.
  6. Graph compilation succeeds with selected_analysts containing social.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.data_collector import DataCollector
from tradingagents.graph.propagation import Propagator


def _make_lightweight_trading_graph():
    """Create a lightweight TradingAgentsGraph instance without real LLMs."""
    with patch("tradingagents.graph.trading_graph.create_llm_client"), \
         patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
         patch("tradingagents.graph.trading_graph.GraphSetup"), \
         patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
         patch("tradingagents.graph.trading_graph.Propagator"), \
         patch("tradingagents.graph.trading_graph.Reflector"), \
         patch("tradingagents.graph.trading_graph.SignalProcessor"), \
         patch("tradingagents.graph.trading_graph.set_config"):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
        ta.debug = False
        ta.config = DEFAULT_CONFIG.copy()
        ta.callbacks = []
        ta.ticker = None
        ta.log_states_dict = {}
        ta.quick_thinking_llm = MagicMock()
        ta.data_collector = DataCollector()
        ta.propagator = Propagator()
        ta.graph = MagicMock()
        ta.signal_processor = MagicMock()
        return ta


def test_social_toolnode_does_not_contain_get_news():
    """Verify that _create_tool_nodes()['social'] does NOT contain get_news."""
    ta = _make_lightweight_trading_graph()
    tool_nodes = ta._create_tool_nodes()

    assert "social" in tool_nodes, "Tool nodes dictionary must contain 'social' key"
    social_node = tool_nodes["social"]

    social_tool_names = set(social_node.tools_by_name.keys())
    assert "get_news" not in social_tool_names, (
        f"social ToolNode must not contain 'get_news', but found: {social_tool_names}"
    )
    # Ensure no data tools are registered for social ToolNode
    assert len(social_tool_names) == 0, (
        f"social ToolNode must have empty tool set (no fallback tools), found: {social_tool_names}"
    )


def test_news_toolnode_preserves_get_news_and_other_tools():
    """Verify that news ToolNode retains get_news, get_global_news, and get_insider_transactions."""
    ta = _make_lightweight_trading_graph()
    tool_nodes = ta._create_tool_nodes()

    assert "news" in tool_nodes
    news_tool_names = set(tool_nodes["news"].tools_by_name.keys())
    expected_news_tools = {"get_news", "get_global_news", "get_insider_transactions"}
    assert news_tool_names == expected_news_tools, (
        f"news ToolNode tools mismatch: expected {expected_news_tools}, got {news_tool_names}"
    )


def test_other_analyst_toolnodes_remain_unchanged():
    """Verify all other analyst tool nodes maintain their expected toolsets."""
    ta = _make_lightweight_trading_graph()
    tool_nodes = ta._create_tool_nodes()

    expected_sets = {
        "market": {"get_stock_data", "get_indicators"},
        "fundamentals": {
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement",
        },
        "macro": {"get_board_fund_flow", "get_news"},
        "smart_money": {
            "get_individual_fund_flow",
            "get_lhb_detail",
            "get_indicators",
        },
        "volume_price": {"get_stock_data"},
    }

    for node_name, expected_tools in expected_sets.items():
        assert node_name in tool_nodes, f"Missing tool node: {node_name}"
        actual_tools = set(tool_nodes[node_name].tools_by_name.keys())
        assert actual_tools == expected_tools, (
            f"ToolNode '{node_name}' tools mismatch: expected {expected_tools}, got {actual_tools}"
        )


def test_no_forbidden_social_data_tools_registered():
    """Verify no new social tools like get_social_sentiment_bundle are registered in any tool node."""
    ta = _make_lightweight_trading_graph()
    tool_nodes = ta._create_tool_nodes()

    all_registered_tools = set()
    for tn in tool_nodes.values():
        all_registered_tools.update(tn.tools_by_name.keys())

    forbidden_tools = {
        "get_social_sentiment_bundle",
        "get_social_data",
        "get_social_sentiment",
        "get_social_archive",
    }
    present_forbidden = all_registered_tools & forbidden_tools
    assert not present_forbidden, f"Forbidden social tools found in ToolNodes: {present_forbidden}"


def test_social_data_tools_module_does_not_exist():
    """Verify that tradingagents/agents/utils/social_data_tools.py is NOT created."""
    tools_file = Path("tradingagents/agents/utils/social_data_tools.py")
    assert not tools_file.exists(), (
        "tradingagents/agents/utils/social_data_tools.py must NOT exist per Task 10 specification"
    )


def test_trading_graph_compilation_with_social_analyst():
    """Verify graph compiles successfully with social analyst included."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    cfg = DEFAULT_CONFIG.copy()
    cfg["api_key"] = "test-api-key"
    tg = TradingAgentsGraph(
        selected_analysts=["social", "news", "market"],
        config=cfg,
        debug=False,
    )
    assert tg.graph is not None
    assert "tools_social" in tg.graph.nodes
    assert "tools_news" in tg.graph.nodes
    assert "tools_market" in tg.graph.nodes
