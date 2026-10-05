"""DAV-1535: checkpointer 线程在任务结束后的清理回归（生产内存泄漏修复）。

Contracts under test:

1. ``_run_job_inner`` 三条终态路径（成功 / 失败 / 取消即硬超时）都会把本任务
   创建的全部 LangGraph 检查点线程从共享 ``MemorySaver`` 删除。
2. 回归：连续跑 N 个任务后，共享存储里的线程数不随 N 增长（终态为 0）。
3. 清理失败只记日志，不影响任务结果与报告保存。
"""

import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import Annotated, TypedDict

import api.main as main
from api.job_store import InMemoryJobStore
from api.services import report_service
from tradingagents.graph.trading_graph import TradingAgentsGraph


class _MiniState(TypedDict):
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


def _thread_count(store: MemorySaver) -> int:
    return len(store.storage)


class _FakeCompiledStream:
    """Stand-in for a compiled LangGraph: exposes checkpointer + astream.

    ``astream`` writes one real checkpoint via ``invoke`` (so the shared
    ``MemorySaver`` really accumulates the thread), then yields a healthy
    final state dict so the job pipeline completes like production.
    """

    def __init__(self, checkpointer, real_graph):
        self.checkpointer = checkpointer
        self._real_graph = real_graph

    async def astream(self, init_state, **kwargs):
        cfg = kwargs.get("config", {})
        tid = (cfg.get("configurable") or {}).get("thread_id")
        if tid:
            self._real_graph.invoke(
                {"messages": [], "marker": "x"},
                {"configurable": {"thread_id": tid}},
            )
        yield _healthy_final_state(init_state.get("horizon", "short"))


class _RealCheckpointerFakeGraph:
    """TradingAgentsGraph 替身：compiled graph 换成真实 MemorySaver 迷你图。

    ``astream`` 走真实 langgraph checkpoint 写入，便于直接断言共享存储的
    线程数；``build_*`` 接口与 _FakeTradingGraphForJob 对齐。
    """

    def __init__(self, *args, checkpointer=None, data_collector=None, **kwargs):
        self.checkpointer = checkpointer or MemorySaver()
        self.data_collector = data_collector
        self.graph = _FakeCompiledStream(
            self.checkpointer, _compile_mini_graph(self.checkpointer)
        )
        self.propagator = MagicMock()
        self.propagator.get_graph_args.return_value = {
            "stream_mode": "values",
            "config": {"recursion_limit": 10},
        }
        self.propagator.create_initial_state.side_effect = (
            lambda *a, **k: {"messages": [], "marker": "init", "horizon": k.get("horizon", "short")}
        )
        self.role_resolved_configs = {}
        self.quick_thinking_llm = MagicMock()
        self.config = kwargs.get("config", {})

    def process_signal(self, _decision):
        return "HOLD"

    def _build_horizon_result(self, horizon, state, market_source=None):
        return dict(state)

    def _ensure_game_theory_state(self, *a, **k):
        return None


def _healthy_final_state(horizon: str = "short"):
    return {
        "company_of_interest": "600519.SH",
        "trade_date": "2026-08-20",
        "horizon": horizon,
        "market_report": f"{horizon} market",
        "sentiment_report": f"{horizon} sentiment",
        "news_report": f"{horizon} news",
        "fundamentals_report": f"{horizon} fundamentals",
        "macro_report": f"{horizon} macro",
        "smart_money_report": f"{horizon} smart_money",
        "volume_price_report": f"{horizon} volume_price",
        "final_trade_decision": f"{horizon} decision 买入",
        "investment_plan": f"{horizon} 投资计划",
        "trader_investment_plan": f"{horizon} 交易计划",
        "investment_debate_state": {
            "protocol_version": "v1_legacy",
            "protocol_stage": "opening",
            "feature_flags": {"v2_debate_enabled": False},
            "count": 6,
            "bull_history": "b",
            "bear_history": "s",
            "judge_decision": "d",
            "claims": [],
        },
        "risk_debate_state": {
            "count": 9,
            "aggressive_history": "a",
            "conservative_history": "c",
            "neutral_history": "n",
            "judge_decision": "ok",
            "claims": [],
        },
        "risk_feedback_state": {"latest_risk_verdict": "pass"},
        "market_data_context": {"daily": {"as_of": "2026-08-20"}},
        "analyst_traces": [],
    }


class _JobGraph(_RealCheckpointerFakeGraph):
    """流式路径用：astream 产出一份健康 final_state。"""

    pass


def _patched_job_env(store, checkpointer):
    """Return the patch context shared by all cleanup tests."""
    collector = MagicMock()
    collector.collect.return_value = {
        "market_data_context": {"daily": {"as_of": "2026-08-20"}},
    }
    db = MagicMock()
    fake_structured = report_service.StructuredReport(
        decision="HOLD",
        confidence=50,
        probability=0.5,
        risks=[],
        key_metrics=[],
        data_gaps=[],
        falsification_conditions=[],
        not_applicable=False,
    )
    return (
        patch.object(main, "_job_store_instance", store),
        patch.object(main, "_shared_data_collector", collector),
        patch.object(
            main,
            "TradingAgentsGraph",
            side_effect=lambda **kw: _JobGraph(checkpointer=checkpointer, **kw),
        ),
        patch.object(main, "_build_runtime_config", return_value={}),
        patch.object(main, "_resolve_and_freeze_custom_prompts", return_value=({}, False)),
        patch.object(main, "get_db_ctx", return_value=nullcontext(db)),
        patch.object(report_service, "init_report"),
        patch.object(report_service, "update_report_partial"),
        patch.object(report_service, "extract_structured_data", return_value=fake_structured),
        patch.object(report_service, "create_report"),
        patch.object(report_service, "resolve_report_fields", return_value={}),
    )


def _make_request(horizons):
    return main.AnalyzeRequest(
        symbol="600519.SH",
        trade_date="2026-08-20",
        horizons=horizons,
        selected_analysts=[],
    )


class TestCheckpointThreadCleanup:
    """连续 N 个任务后共享 checkpointer 的线程数不增长。"""

    def test_threads_do_not_grow_over_n_streaming_jobs(self):
        store = InMemoryJobStore()
        shared_cp = MemorySaver()
        patches = _patched_job_env(store, shared_cp)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            for _ in range(3):
                job_id = f"clean-{uuid4().hex}"
                req = _make_request(["short"])
                asyncio.run(
                    main._run_job_inner(
                        job_id, req, stream_events=True, save_report=True
                    )
                )
        assert _thread_count(shared_cp) == 0

    def test_threads_do_not_grow_over_n_dual_jobs(self):
        store = InMemoryJobStore()
        shared_cp = MemorySaver()
        patches = _patched_job_env(store, shared_cp)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            for _ in range(3):
                job_id = f"clean-{uuid4().hex}"
                req = _make_request(["short", "medium"])
                asyncio.run(
                    main._run_job_inner(
                        job_id, req, stream_events=False, save_report=True
                    )
                )
        assert _thread_count(shared_cp) == 0

    def test_failed_job_still_cleans_threads(self):
        store = InMemoryJobStore()
        shared_cp = MemorySaver()

        async def _failing_astream(self, init_state, **kwargs):
            cfg = kwargs.get("config", {})
            tid = (cfg.get("configurable") or {}).get("thread_id")
            if tid:
                self._real_graph.invoke(
                    {"messages": [], "marker": "x"},
                    {"configurable": {"thread_id": tid}},
                )
            raise RuntimeError("boom")
            yield  # pragma: no cover - keep this an async generator

        patches = _patched_job_env(store, shared_cp)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10], \
             patch.object(_FakeCompiledStream, "astream", _failing_astream):
            job_id = f"clean-{uuid4().hex}"
            req = _make_request(["short"])
            asyncio.run(
                main._run_job_inner(
                    job_id, req, stream_events=True, save_report=True
                )
            )
        assert _thread_count(shared_cp) == 0
        job = store.get_job(job_id)
        assert job["status"] == "failed"

    def test_cleanup_failure_does_not_break_job(self):
        store = InMemoryJobStore()

        class _FailingDeleteSaver(MemorySaver):
            def __init__(self):
                super().__init__()
                self.deleted: list = []

            def delete_thread(self, thread_id):
                self.deleted.append(thread_id)
                raise RuntimeError("delete blew up")

        bad_cp = _FailingDeleteSaver()
        patches = _patched_job_env(store, bad_cp)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9], \
             patches[10]:
            job_id = f"clean-{uuid4().hex}"
            req = _make_request(["short"])
            asyncio.run(
                main._run_job_inner(
                    job_id, req, stream_events=True, save_report=True
                )
            )
        job = store.get_job(job_id)
        assert job["status"] == "completed"
        assert bad_cp.deleted == [job_id]
