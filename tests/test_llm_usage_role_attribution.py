"""DAV-1330: graph-node role/horizon attribution for the LLM usage ledger.

The ledger callback cannot rely on LangChain run metadata inside graph nodes
(nodes do not forward RunnableConfig; sync nodes run in a thread pool), so
roles and horizons ride contextvars instead:

* ``api.usage_logging.current_llm_role`` — set around every LLM-calling node
  by ``GraphSetup._with_llm_role``; repair passes override it with
  ``<role>/返修``.
* ``api.usage_logging.current_llm_horizon`` — set per horizon pipeline
  (``_process_horizon`` / single-horizon paths / ``propagate``).

These tests run a real compiled LangGraph with a fake chat model and assert
every logged row carries the right role + horizon, including two parallel
dual-horizon pipelines that must not bleed into each other.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import TypedDict

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.graph import END, START, StateGraph

from api.database import LLMCallLogDB, current_report_id, get_db_ctx
from api.usage_logging import (
    LLMUsageLogger,
    current_llm_horizon,
    current_llm_role,
)


def _rows_for(report_id):
    with get_db_ctx() as db:
        return (
            db.query(LLMCallLogDB)
            .filter(LLMCallLogDB.report_id == report_id)
            .all()
        )


def _fake_llm(handler: LLMUsageLogger, n: int = 50) -> GenericFakeChatModel:
    # Constructor-level callbacks behave like the UnifiedChatOpenAI mount:
    # inherited by every invoke/ainvoke run without any run metadata.
    return GenericFakeChatModel(
        messages=iter(["ok"] * n), callbacks=[handler]
    )


class _State(TypedDict, total=False):
    x: str


def _build_two_analyst_graph(llm) -> object:
    """Minimal graph: one async node + one sync node in parallel, both wrapped
    with _with_llm_role like GraphSetup.setup_graph does."""

    from tradingagents.graph.setup import _with_llm_role

    async def _async_node(state):
        await llm.ainvoke("hi")
        return {}

    def _sync_node(state):
        llm.invoke("hi")
        return {}

    workflow = StateGraph(_State)
    workflow.add_node("Market Analyst", _with_llm_role("Market Analyst", _async_node))
    workflow.add_node("News Analyst", _with_llm_role("News Analyst", _sync_node))
    workflow.add_edge(START, "Market Analyst")
    workflow.add_edge(START, "News Analyst")
    workflow.add_edge("Market Analyst", END)
    workflow.add_edge("News Analyst", END)
    return workflow.compile()


class TestGraphNodeRoleAttribution:
    def test_wrapped_nodes_log_role_sync_and_async(self):
        """In-graph calls (async node + threadpool sync node) get their node
        role from the contextvar even though run metadata is empty."""
        handler = LLMUsageLogger()
        llm = _fake_llm(handler)
        graph = _build_two_analyst_graph(llm)
        report_id = uuid.uuid4().hex

        tok = current_report_id.set(report_id)
        htok = current_llm_horizon.set("short")
        try:
            asyncio.run(graph.ainvoke({"x": ""}))
        finally:
            current_llm_horizon.reset(htok)
            current_report_id.reset(tok)

        rows = _rows_for(report_id)
        assert len(rows) == 2
        roles = {r.agent_name for r in rows}
        assert roles == {"Market Analyst", "News Analyst"}
        assert all(r.horizon == "short" for r in rows)

    def test_parallel_dual_horizon_no_cross_bleed(self):
        """Two horizon pipelines run in parallel tasks; each context copy must
        keep its own horizon — no rows may be NULL or carry the wrong tag."""
        handler = LLMUsageLogger()
        llm = _fake_llm(handler)
        graph = _build_two_analyst_graph(llm)
        report_id = uuid.uuid4().hex

        async def _run_pipeline(horizon: str):
            rtok = current_report_id.set(report_id)
            htok = current_llm_horizon.set(horizon)
            try:
                await graph.ainvoke({"x": horizon})
            finally:
                current_llm_horizon.reset(htok)
                current_report_id.reset(rtok)

        async def _main():
            await asyncio.gather(
                _run_pipeline("short"), _run_pipeline("medium")
            )

        asyncio.run(_main())

        rows = _rows_for(report_id)
        assert len(rows) == 4
        pairs = {(r.agent_name, r.horizon) for r in rows}
        assert pairs == {
            ("Market Analyst", "short"),
            ("News Analyst", "short"),
            ("Market Analyst", "medium"),
            ("News Analyst", "medium"),
        }

    def test_role_reset_after_node(self):
        """The wrapper restores the previous value — a later unwrapped call
        must not inherit the last node's role."""
        from tradingagents.graph.setup import _with_llm_role

        sentinel = current_llm_role.set("outer")
        try:
            wrapped = _with_llm_role("Trader", lambda s: {"x": "t"})
            wrapped({})
            assert current_llm_role.get() == "outer"
        finally:
            current_llm_role.reset(sentinel)


class TestHorizonContextvarFallback:
    def test_horizon_falls_back_to_contextvar(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        tok = current_llm_horizon.set("medium")
        try:
            handler.on_chat_model_start(
                {}, [], run_id=run_id, metadata={"report_id": report_id}
            )
            handler.on_llm_end(
                _llm_result_stub(), run_id=run_id
            )
        finally:
            current_llm_horizon.reset(tok)
        r = _rows_for(report_id)[0]
        assert r.horizon == "medium"

    def test_metadata_horizon_wins_over_contextvar(self):
        handler = LLMUsageLogger()
        report_id = uuid.uuid4().hex
        run_id = uuid.uuid4()
        tok = current_llm_horizon.set("medium")
        try:
            handler.on_chat_model_start(
                {}, [], run_id=run_id,
                metadata={"report_id": report_id, "horizon": "short"},
            )
            handler.on_llm_end(_llm_result_stub(), run_id=run_id)
        finally:
            current_llm_horizon.reset(tok)
        assert _rows_for(report_id)[0].horizon == "short"


def _llm_result_stub():
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    gen = ChatGeneration(message=AIMessage(content="ok"), generation_info={})
    return LLMResult(generations=[[gen]], llm_output={})


class TestRevisionCallLabeling:
    """Repair passes (price_ref / E-04) are tagged '<role>/返修' so ledger rows
    distinguish them from the role's normal output call."""

    def test_revision_call_uses_outer_role_suffix(self):
        from tradingagents.agents.utils.price_ref_revision import (
            _invoke_revision_llm,
        )

        handler = LLMUsageLogger()
        llm = _fake_llm(handler)
        report_id = uuid.uuid4().hex

        async def _run():
            rtok = current_report_id.set(report_id)
            roletok = current_llm_role.set("Research Manager")
            try:
                return await _invoke_revision_llm(
                    llm, "orig", "revise please", role_key_hint="research_manager"
                )
            finally:
                current_llm_role.reset(roletok)
                current_report_id.reset(rtok)

        out = asyncio.run(_run())
        assert out == "ok"
        r = _rows_for(report_id)[0]
        assert r.agent_name == "Research Manager/返修"

    def test_revision_call_falls_back_to_role_key(self):
        from tradingagents.agents.utils.price_ref_revision import (
            _invoke_revision_llm,
        )

        handler = LLMUsageLogger()
        llm = _fake_llm(handler)
        report_id = uuid.uuid4().hex

        async def _run():
            rtok = current_report_id.set(report_id)
            try:
                return await _invoke_revision_llm(
                    llm, "orig", "revise please", role_key_hint="trader"
                )
            finally:
                current_report_id.reset(rtok)

        asyncio.run(_run())
        assert _rows_for(report_id)[0].agent_name == "trader/返修"
