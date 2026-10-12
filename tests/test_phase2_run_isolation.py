"""Phase-2 P3 experiment run isolation tests (DAV-1477).

Contracts under test:

1. ``TradingAgentsGraph`` accepts an injected checkpointer; omitting it keeps
   the class-level shared ``MemorySaver`` (production API/CLI unchanged).
2. Experiment helpers build ``{sample_id}:{system_version}:{arm}:{replicate}:{uuid}``
   thread ids and always hand out fresh checkpointer instances.
3. Experiment-mode guard: ``propagate()`` without an explicit ``thread_id``
   raises instead of falling back to the production default thread name.
4. ``result_data.experiment_run_identity`` carries ``thread_id`` +
   checkpointer identity (DAV-1486: distinct key — API-side ``run_identity``
   is unconditionally overwritten by ``_attach_traceability_fields``, so the
   experiment identity must never share it).
5. Same-process repeated runs: the legacy path (default thread name + shared
   store) reproduces cross-run checkpoint crosstalk (red); the experiment
   path (dedicated store + explicit thread ids) is clean (green).
6. Experiment runs never write ``FinancialSituationMemory`` (guard against
   silent pollution when upstream always-on decision-log merging lands).
"""

from unittest.mock import MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

from tradingagents.experiments.run_isolation import (
    ExperimentThreadIdError,
    assert_experiment_thread_id,
    checkpointer_identity,
    experiment_graph_kwargs,
    is_experiment_thread_id,
    make_experiment_thread_id,
    new_experiment_checkpointer,
)
from tradingagents.graph.data_collector import DataCollector
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.trading_graph import TradingAgentsGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bare_graph(*, checkpointer=None, experiment_mode=False):
    """Return a TradingAgentsGraph with heavy components stubbed.

    Mirrors the ``_make_mock_trading_agents_graph`` pattern already used by
    test_horizon_cache_isolation.py: real propagator/data collector, mocked
    graph/LLM surfaces.
    """
    ta = TradingAgentsGraph.__new__(TradingAgentsGraph)
    ta.debug = False
    ta.config = {}
    ta.callbacks = []
    ta.ticker = "600519"
    ta.log_states_dict = {}
    ta.quick_thinking_llm = MagicMock()
    ta.data_collector = DataCollector()
    ta.propagator = Propagator()
    ta.graph = MagicMock()
    ta.signal_processor = MagicMock()
    ta.signal_processor.process_signal.return_value = "BUY"
    ta.checkpointer = checkpointer
    ta.experiment_mode = experiment_mode
    return ta


def _final_state(horizon="short", decision="BUY"):
    return {
        "company_of_interest": "600519",
        "trade_date": "2026-08-26",
        "horizon": horizon,
        "final_trade_decision": decision,
        "trader_investment_plan": "",
        "investment_plan": "",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "",
        },
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": "", "judge_decision": "",
        },
    }


class _MiniState(TypedDict):
    """Minimal MessagesState-like channel set for real checkpointer runs."""

    messages: Annotated[list, add_messages]
    marker: str


def _mini_node(state):
    return {"messages": [("ai", "reply")], "marker": "node-ran"}


def _compile_mini_graph(checkpointer):
    builder = StateGraph(_MiniState)
    builder.add_node("n", _mini_node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 1. Checkpointer injection on TradingAgentsGraph.__init__
# ---------------------------------------------------------------------------

class TestCheckpointerInjection:
    """TradingAgentsGraph(checkpointer=...) uses the injected store; default
    construction still shares the class-level singleton."""

    def _ctor_kwargs(self):
        return dict(config={"project_dir": "/tmp/ta-dav1477"})

    def _patch_init(self):
        return [
            patch("tradingagents.graph.trading_graph.create_llm_client",
                  return_value=MagicMock(get_llm=lambda: MagicMock())),
            patch("tradingagents.graph.trading_graph.FinancialSituationMemory"),
            patch("tradingagents.graph.trading_graph.GraphSetup",
                  return_value=MagicMock(setup_graph=lambda *a, **k: MagicMock())),
            patch("tradingagents.graph.trading_graph.ConditionalLogic"),
            patch("tradingagents.graph.trading_graph.Reflector"),
            patch("tradingagents.graph.trading_graph.SignalProcessor"),
            patch("tradingagents.graph.trading_graph.set_config"),
            patch("tradingagents.graph.trading_graph.ToolNode"),
        ]

    def test_injected_checkpointer_wins(self):
        own = MemorySaver()
        with patch("tradingagents.graph.trading_graph.create_llm_client",
                   return_value=MagicMock(get_llm=lambda: MagicMock())), \
             patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
             patch("tradingagents.graph.trading_graph.GraphSetup",
                   return_value=MagicMock(setup_graph=lambda *a, **k: MagicMock())), \
             patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
             patch("tradingagents.graph.trading_graph.Reflector"), \
             patch("tradingagents.graph.trading_graph.SignalProcessor"), \
             patch("tradingagents.graph.trading_graph.set_config"), \
             patch("tradingagents.graph.trading_graph.ToolNode"):
            ta = TradingAgentsGraph(
                config={"project_dir": "/tmp/ta-dav1477-inj"},
                checkpointer=own,
            )
        assert ta.checkpointer is own
        assert ta.checkpointer is not TradingAgentsGraph._shared_checkpointer

    def test_default_still_shared_singleton(self):
        with patch("tradingagents.graph.trading_graph.create_llm_client",
                   return_value=MagicMock(get_llm=lambda: MagicMock())), \
             patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
             patch("tradingagents.graph.trading_graph.GraphSetup",
                   return_value=MagicMock(setup_graph=lambda *a, **k: MagicMock())), \
             patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
             patch("tradingagents.graph.trading_graph.Reflector"), \
             patch("tradingagents.graph.trading_graph.SignalProcessor"), \
             patch("tradingagents.graph.trading_graph.set_config"), \
             patch("tradingagents.graph.trading_graph.ToolNode"):
            ta1 = TradingAgentsGraph(config={"project_dir": "/tmp/ta-dav1477-a"})
            ta2 = TradingAgentsGraph(config={"project_dir": "/tmp/ta-dav1477-b"})
        assert ta1.checkpointer is ta2.checkpointer
        assert ta1.checkpointer is TradingAgentsGraph._shared_checkpointer
        assert ta1.experiment_mode is False

    def test_experiment_mode_without_checkpointer_fails_fast(self):
        """experiment_mode=True without a dedicated checkpointer would run on
        the shared store — forbidden per the P3 'experiment runs always inject
        a dedicated checkpointer' contract."""
        with patch("tradingagents.graph.trading_graph.create_llm_client",
                   return_value=MagicMock(get_llm=lambda: MagicMock())), \
             patch("tradingagents.graph.trading_graph.FinancialSituationMemory"), \
             patch("tradingagents.graph.trading_graph.GraphSetup",
                   return_value=MagicMock(setup_graph=lambda *a, **k: MagicMock())), \
             patch("tradingagents.graph.trading_graph.ConditionalLogic"), \
             patch("tradingagents.graph.trading_graph.Reflector"), \
             patch("tradingagents.graph.trading_graph.SignalProcessor"), \
             patch("tradingagents.graph.trading_graph.set_config"), \
             patch("tradingagents.graph.trading_graph.ToolNode"):
            with pytest.raises(ValueError, match="checkpointer"):
                TradingAgentsGraph(
                    config={"project_dir": "/tmp/ta-dav1477-guard"},
                    experiment_mode=True,
                )


# ---------------------------------------------------------------------------
# 2. Experiment helper contracts
# ---------------------------------------------------------------------------

class TestExperimentHelpers:
    def test_thread_id_shape(self):
        tid = make_experiment_thread_id("600519.SH", "v1", "A", 1)
        parts = tid.split(":")
        assert parts[:4] == ["600519.SH", "v1", "A", "1"]
        assert len(parts[4]) == 32
        assert is_experiment_thread_id(tid)

    def test_thread_id_unique_per_call(self):
        a = make_experiment_thread_id("s", "v", "A", 1)
        b = make_experiment_thread_id("s", "v", "A", 1)
        assert a != b  # uuid suffix differs → no replicate collision

    def test_is_experiment_thread_id_rejects_production_shape(self):
        assert not is_experiment_thread_id("600519_2026-08-26_short")
        assert not is_experiment_thread_id("job_42_medium")
        assert not is_experiment_thread_id("")
        assert not is_experiment_thread_id(None)
        assert not is_experiment_thread_id("a:b:c:d:not-hex!")

    def test_is_experiment_thread_id_rejects_empty_segments(self):
        """DAV-1486: ``a::b:c:{hex}`` used to be accepted — an empty segment
        is no longer a valid field value."""
        assert not is_experiment_thread_id("a::b:c:deadbeef")
        assert not is_experiment_thread_id(":a:b:c:deadbeef")
        assert not is_experiment_thread_id("a:b:c:d:")
        assert not is_experiment_thread_id("a:b:c::")  # empty + non-hex

    def test_make_thread_id_rejects_colon_and_empty_fields(self):
        """DAV-1486: fail fast at construction — the first four fields must
        not contain ':' (the layout is parsed by splitting on ':') and must
        not be empty."""
        with pytest.raises(ValueError, match="must not contain ':'"):
            make_experiment_thread_id("C:\\path\\sample", "v1", "A", 1)
        with pytest.raises(ValueError, match="must not contain ':'"):
            make_experiment_thread_id("600519", "v:1", "A", 1)
        with pytest.raises(ValueError, match="must not contain ':'"):
            make_experiment_thread_id("600519", "v1", "arm:x", 1)
        with pytest.raises(ValueError, match="non-empty"):
            make_experiment_thread_id("", "v1", "A", 1)

    def test_fresh_checkpointer_each_call(self):
        c1, c2 = new_experiment_checkpointer(), new_experiment_checkpointer()
        assert c1 is not c2

    def test_checkpointer_identity_distinguishes_instances(self):
        c1, c2 = MemorySaver(), MemorySaver()
        i1, i2 = checkpointer_identity(c1), checkpointer_identity(c2)
        assert i1 != i2
        assert "InMemorySaver" in i1
        assert checkpointer_identity(None) == "none"

    def test_experiment_graph_kwargs_bundle(self):
        kwargs = experiment_graph_kwargs()
        assert isinstance(kwargs["checkpointer"], MemorySaver)
        assert kwargs["experiment_mode"] is True

    def test_assert_thread_id_pass_through(self):
        tid = make_experiment_thread_id("s", "v", "B", 2)
        assert assert_experiment_thread_id(tid) == tid

    def test_assert_thread_id_fail_closed(self):
        with pytest.raises(ExperimentThreadIdError):
            assert_experiment_thread_id(None)
        with pytest.raises(ExperimentThreadIdError):
            assert_experiment_thread_id("")
        with pytest.raises(ExperimentThreadIdError):
            assert_experiment_thread_id("600519_2026-08-26_short")

    def test_assert_thread_id_error_reports_diagnostics(self):
        """DAV-1486: the rejection message carries the actual segment count
        and per-segment validation results so a malformed id is locatable."""
        with pytest.raises(ExperimentThreadIdError) as exc:
            assert_experiment_thread_id("a:b:c:d:not-hex")
        msg = str(exc.value)
        assert "segments=5" in msg
        assert "not-hex" in msg
        assert "uuid=" in msg
        with pytest.raises(ExperimentThreadIdError) as exc2:
            assert_experiment_thread_id("a::b:c:deadbeef")
        msg2 = str(exc2.value)
        assert "segments=5" in msg2
        assert "empty" in msg2


# ---------------------------------------------------------------------------
# 3. propagate() experiment-mode guard + experiment_run_identity
# ---------------------------------------------------------------------------

class TestPropagateExperimentGuard:
    """Experiment-mode propagate() requires explicit thread_id; the
    production default thread name must never be reached."""

    def test_experiment_mode_missing_thread_id_raises(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=True)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()) as mi:
            with pytest.raises(ExperimentThreadIdError):
                ta.propagate("600519", "2026-08-26", horizon="short")
        assert not mi.called  # fail-closed before graph runs

    def test_experiment_mode_rejects_production_shaped_id(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=True)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()):
            with pytest.raises(ExperimentThreadIdError):
                ta.propagate(
                    "600519", "2026-08-26",
                    thread_id="600519_2026-08-26_short",
                    horizon="short",
                )

    def test_experiment_mode_accepts_valid_id_and_records_run_identity(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=True)
        tid = make_experiment_thread_id("600519", "v1", "A", 1)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()) as mi:
            state, signal = ta.propagate(
                "600519", "2026-08-26", thread_id=tid, horizon="short",
            )
        call_kwargs = mi.call_args[1]
        assert call_kwargs["config"]["configurable"]["thread_id"] == tid
        assert state["experiment_run_identity"]["thread_id"] == tid
        assert "InMemorySaver" in state["experiment_run_identity"]["checkpointer"]
        # DAV-1486: experiment identity must NOT reuse the API-side
        # run_identity key — that slot belongs to _attach_traceability_fields
        # (DAV-1430) and would be silently overwritten.
        assert "run_identity" not in state

    def test_production_default_thread_name_unchanged(self):
        """Production path (experiment_mode=False) keeps the legacy default
        thread name verbatim — this is the byte-for-byte behaviour contract."""
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=False)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()) as mi:
            ta.propagate("600519", "2026-08-26", horizon="short")
        tid = mi.call_args[1]["config"]["configurable"]["thread_id"]
        assert tid == "600519_2026-08-26_short"

    def test_production_explicit_thread_id_unchanged(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=False)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()) as mi:
            ta.propagate(
                "600519", "2026-08-26", thread_id="job_99", horizon="short",
            )
        tid = mi.call_args[1]["config"]["configurable"]["thread_id"]
        assert tid == "job_99"

    def test_production_state_has_no_run_identity(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=False)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()):
            state, _ = ta.propagate("600519", "2026-08-26", horizon="short")
        assert "run_identity" not in state
        assert "experiment_run_identity" not in state


# ---------------------------------------------------------------------------
# 4. result_data.experiment_run_identity via _build_horizon_result
# ---------------------------------------------------------------------------

class TestRunIdentityInResult:
    def test_result_carries_run_identity(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=True)
        tid = make_experiment_thread_id("600519", "v2", "B", 3)
        state = _final_state()
        state["experiment_run_identity"] = {
            "thread_id": tid,
            "checkpointer": checkpointer_identity(ta.checkpointer),
        }
        with patch("tradingagents.graph.trading_graph.apply_report_quality_gate"), \
             patch("tradingagents.graph.trading_graph.finalize_price_ref_state",
                   return_value={"status": "ok"}):
            result = ta._build_horizon_result("short", state)
        assert result["experiment_run_identity"]["thread_id"] == tid
        assert result["experiment_run_identity"]["checkpointer"].startswith("InMemorySaver@")
        assert "run_identity" not in result

    def test_production_result_has_no_run_identity(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=False)
        state = _final_state()
        with patch("tradingagents.graph.trading_graph.apply_report_quality_gate"), \
             patch("tradingagents.graph.trading_graph.finalize_price_ref_state",
                   return_value={"status": "ok"}):
            result = ta._build_horizon_result("short", state)
        assert "run_identity" not in result
        assert "experiment_run_identity" not in result


# ---------------------------------------------------------------------------
# 5. Same-process repeat runs: red on legacy path, green on experiment path
# ---------------------------------------------------------------------------

class TestCrossRunCheckpointIsolation:
    """Two consecutive runs of the same ticker/date/horizon in one process.

    Legacy path (shared store + default thread name) reproduces the
    checkpoint crosstalk documented in the issue; experiment path (dedicated
    store + explicit experiment thread id) shows no residue from run 1.
    """

    def _run_mini(self, checkpointer, thread_id, human_msg):
        g = _compile_mini_graph(checkpointer)
        return g.invoke(
            {"messages": [("human", human_msg)], "marker": "init"},
            config={"configurable": {"thread_id": thread_id}},
        )

    def test_legacy_shared_store_default_thread_crosstalk(self):
        """Red: same thread_id on the shared store → run 2 inherits run 1's
        messages and channel values (the bug this card fixes)."""
        shared = MemorySaver()
        thread = "600519_2026-08-26_short"  # production default thread name
        out1 = self._run_mini(shared, thread, "first")
        out2 = self._run_mini(shared, thread, "second")
        # messages accumulate: run 2 sees run 1's human+ai messages
        assert len(out2["messages"]) > len(out1["messages"])
        # marker persists from checkpoint even though input supplied "init"
        assert out2["marker"] == "node-ran"

    def test_experiment_dedicated_store_no_crosstalk(self):
        """Green: two experiment runs with dedicated stores + distinct
        experiment thread ids are fully isolated."""
        kw1 = experiment_graph_kwargs()
        kw2 = experiment_graph_kwargs()
        t1 = make_experiment_thread_id("600519", "v1", "A", 1)
        t2 = make_experiment_thread_id("600519", "v1", "A", 2)
        out1 = self._run_mini(kw1["checkpointer"], t1, "first")
        out2 = self._run_mini(kw2["checkpointer"], t2, "second")
        assert len(out1["messages"]) == 2  # human + ai
        assert len(out2["messages"]) == 2
        contents2 = [m.content for m in out2["messages"]]
        assert "first" not in contents2

    def test_experiment_same_store_different_threads_isolated(self):
        """Even on one injected store, distinct experiment thread ids cannot
        collide (uuid suffix makes every replicate unique)."""
        store = new_experiment_checkpointer()
        t1 = make_experiment_thread_id("600519", "v1", "A", 1)
        t2 = make_experiment_thread_id("600519", "v1", "A", 2)
        assert t1 != t2
        out1 = self._run_mini(store, t1, "first")
        out2 = self._run_mini(store, t2, "second")
        assert len(out2["messages"]) == 2
        contents2 = [m.content for m in out2["messages"]]
        assert "first" not in contents2


# ---------------------------------------------------------------------------
# 6. Experiment path must not write FinancialSituationMemory
# ---------------------------------------------------------------------------

class TestNoMemoryWritesOnExperimentPath:
    """Guard against silent FinancialSituationMemory pollution when upstream
    always-on decision logging lands: experiment runs only read memories
    inside graph nodes; add_situations must never be invoked."""

    def test_experiment_propagate_never_calls_add_situations(self):
        ta = _make_bare_graph(checkpointer=MemorySaver(), experiment_mode=True)
        # Attach mock memories like __init__ does
        ta.bull_memory = MagicMock()
        ta.bear_memory = MagicMock()
        ta.trader_memory = MagicMock()
        ta.invest_judge_memory = MagicMock()
        ta.risk_manager_memory = MagicMock()
        tid = make_experiment_thread_id("600519", "v1", "A", 1)
        with patch.object(ta.data_collector, "collect", return_value={}), \
             patch.object(ta.graph, "invoke", return_value=_final_state()):
            ta.propagate("600519", "2026-08-26", thread_id=tid, horizon="short")
        for mem in (
            ta.bull_memory, ta.bear_memory, ta.trader_memory,
            ta.invest_judge_memory, ta.risk_manager_memory,
        ):
            assert not mem.add_situations.called, (
                "experiment propagate() must not write FinancialSituationMemory"
            )
