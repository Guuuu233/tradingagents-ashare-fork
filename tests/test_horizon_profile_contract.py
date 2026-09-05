"""Contract tests for H-01 horizon configuration and unique resolution.

Covers:
1. 缺省 (Unprovided / Default)
2. 显式 short (Explicit short)
3. 显式 medium (Explicit medium)
4. 显式双档 (Explicit dual & deduplication preserving order)
5. 空列表 (Empty list validation error)
6. 显式 null (Explicit null validation error)
7. 非法混合 (Illegal values validation error)
8. query 冲突不扩档 (Query conflict does not rewrite resolved)
9. 二次归一化不翻转 default 也不重写 resolved
10. chat 路径不把 LLM 双档当 explicit
11. Profile 常量及元数据契约
12. API POST /v1/analyze 端点契约 (4xx on invalid, 200 on valid)
"""

import asyncio
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api import main
from api.job_store import InMemoryJobStore
from tradingagents.graph.horizon_profile import (
    HORIZON_MEDIUM,
    HORIZON_PROFILE_V1,
    HORIZON_SHORT,
    RESOLUTION_SOURCE_DEFAULT,
    RESOLUTION_SOURCE_EXPLICIT,
    SUPPORTED_HORIZONS,
    T_PLUS_10,
    T_PLUS_40,
    HorizonResolution,
    dedupe_preserve_order,
    horizon_profile_v1,
    resolve_analysis_horizons,
    _HORIZONS_UNSET,
)


# ── 1. Profile 常量契约 ─────────────────────────────────────────────────────────

def test_profile_constants_and_contract():
    assert SUPPORTED_HORIZONS == ("short", "medium")
    assert HORIZON_SHORT == "short"
    assert HORIZON_MEDIUM == "medium"
    assert T_PLUS_10 == 10
    assert T_PLUS_40 == 40
    assert horizon_profile_v1 is HORIZON_PROFILE_V1

    short_prof = HORIZON_PROFILE_V1["short"]
    assert short_prof["horizon"] == "short"
    assert short_prof["min_trading_days"] == 5
    assert short_prof["max_trading_days"] == 20
    assert short_prof["primary_eval_offset"] == 10

    medium_prof = HORIZON_PROFILE_V1["medium"]
    assert medium_prof["horizon"] == "medium"
    assert medium_prof["min_trading_days"] == 21
    assert medium_prof["max_trading_days"] == 60
    assert medium_prof["primary_eval_offset"] == 40


# ── 2. 缺省 (Unprovided / Default) ─────────────────────────────────────────────

def test_unprovided_resolves_to_default_short():
    res = resolve_analysis_horizons()
    assert res.resolved == ["short"]
    assert res.resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert res.notice is None

    res_unset = resolve_analysis_horizons(_HORIZONS_UNSET)
    assert res_unset.resolved == ["short"]
    assert res_unset.resolution_source == RESOLUTION_SOURCE_DEFAULT

    res_explicit_false = resolve_analysis_horizons(explicit=False)
    assert res_explicit_false.resolved == ["short"]
    assert res_explicit_false.resolution_source == RESOLUTION_SOURCE_DEFAULT


def test_analyze_request_unprovided_defaults_to_short():
    req = main.AnalyzeRequest(symbol="600519.SH")
    assert req.horizons == ["short"]
    assert req.horizons_explicit is False
    assert req.horizons_resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert req.horizons_notice is None

    req_json = main.AnalyzeRequest.model_validate({"symbol": "600519.SH"})
    assert req_json.horizons == ["short"]
    assert req_json.horizons_explicit is False
    assert req_json.horizons_resolution_source == RESOLUTION_SOURCE_DEFAULT


# ── 3. 显式 short (Explicit short) ─────────────────────────────────────────────

def test_explicit_short_resolution():
    res = resolve_analysis_horizons(["short"])
    assert res.resolved == ["short"]
    assert res.resolution_source == RESOLUTION_SOURCE_EXPLICIT
    assert res.notice is None

    req = main.AnalyzeRequest(symbol="600519.SH", horizons=["short"])
    assert req.horizons == ["short"]
    assert req.horizons_explicit is True
    assert req.horizons_resolution_source == RESOLUTION_SOURCE_EXPLICIT

    req_json = main.AnalyzeRequest.model_validate({"symbol": "600519.SH", "horizons": ["short"]})
    assert req_json.horizons == ["short"]
    assert req_json.horizons_explicit is True
    assert req_json.horizons_resolution_source == RESOLUTION_SOURCE_EXPLICIT


# ── 4. 显式 medium (Explicit medium) ───────────────────────────────────────────

def test_explicit_medium_resolution():
    res = resolve_analysis_horizons(["medium"])
    assert res.resolved == ["medium"]
    assert res.resolution_source == RESOLUTION_SOURCE_EXPLICIT
    assert res.notice is None

    req = main.AnalyzeRequest(symbol="600519.SH", horizons=["medium"])
    assert req.horizons == ["medium"]
    assert req.horizons_explicit is True
    assert req.horizons_resolution_source == RESOLUTION_SOURCE_EXPLICIT

    req_json = main.AnalyzeRequest.model_validate({"symbol": "600519.SH", "horizons": ["medium"]})
    assert req_json.horizons == ["medium"]
    assert req_json.horizons_explicit is True
    assert req_json.horizons_resolution_source == RESOLUTION_SOURCE_EXPLICIT


# ── 5. 显式双档与去重保序 ────────────────────────────────────────────────────────

def test_explicit_dual_and_dedupe_preserve_order():
    res = resolve_analysis_horizons(["short", "medium"])
    assert res.resolved == ["short", "medium"]
    assert res.resolution_source == RESOLUTION_SOURCE_EXPLICIT

    # Order preserved when medium is first
    res_rev = resolve_analysis_horizons(["medium", "short"])
    assert res_rev.resolved == ["medium", "short"]
    assert res_rev.resolution_source == RESOLUTION_SOURCE_EXPLICIT

    # Deduplicate while preserving order
    res_dedupe = resolve_analysis_horizons(["medium", "short", "medium"])
    assert res_dedupe.resolved == ["medium", "short"]
    assert res_dedupe.resolution_source == RESOLUTION_SOURCE_EXPLICIT

    res_short_dup = resolve_analysis_horizons(["short", "short", "short"])
    assert res_short_dup.resolved == ["short"]
    assert res_short_dup.resolution_source == RESOLUTION_SOURCE_EXPLICIT

    req = main.AnalyzeRequest(symbol="600519.SH", horizons=["medium", "short", "medium"])
    assert req.horizons == ["medium", "short"]
    assert req.horizons_explicit is True
    assert req.horizons_resolution_source == RESOLUTION_SOURCE_EXPLICIT


# ── 6. 空列表验证错误 ───────────────────────────────────────────────────────────

def test_empty_horizons_raises_error():
    with pytest.raises(ValueError, match="不能为空"):
        resolve_analysis_horizons([])

    with pytest.raises(ValidationError):
        main.AnalyzeRequest(symbol="600519.SH", horizons=[])

    with pytest.raises(ValidationError):
        main.AnalyzeRequest.model_validate({"symbol": "600519.SH", "horizons": []})


# ── 7. 显式 null 验证错误 ───────────────────────────────────────────────────────

def test_explicit_null_raises_error():
    with pytest.raises(ValueError, match="显式 null"):
        resolve_analysis_horizons(None)

    with pytest.raises(ValidationError):
        main.AnalyzeRequest(symbol="600519.SH", horizons=None)

    with pytest.raises(ValidationError):
        main.AnalyzeRequest.model_validate({"symbol": "600519.SH", "horizons": None})


# ── 8. 非法值与混合非法值验证错误 ────────────────────────────────────────────────

def test_illegal_horizons_raise_error():
    for illegal in [
        ["unknown"],
        ["short", "bogus"],
        ["bogus", "medium"],
        ["long"],
        ["daily"],
        [123],
    ]:
        with pytest.raises(ValueError):
            resolve_analysis_horizons(illegal)

        with pytest.raises(ValidationError):
            main.AnalyzeRequest(symbol="600519.SH", horizons=illegal)

        with pytest.raises(ValidationError):
            main.AnalyzeRequest.model_validate({"symbol": "600519.SH", "horizons": illegal})


# ── 9. query 冲突不扩档 ─────────────────────────────────────────────────────────

def test_query_conflict_does_not_expand_or_rewrite_resolved():
    # 显式单档 + query 明确双档 -> 仍只显式单档，不得改 resolved
    res_short = resolve_analysis_horizons(
        ["short"],
        query="分析 600519.SH 短线和中线机会，短中都看看",
    )
    assert res_short.resolved == ["short"]
    assert res_short.resolution_source == RESOLUTION_SOURCE_EXPLICIT
    assert res_short.notice is not None

    req_short = main.AnalyzeRequest(
        symbol="600519.SH",
        horizons=["short"],
        query="分析 600519.SH 短线和中线机会",
    )
    assert req_short.horizons == ["short"]
    assert req_short.horizons_explicit is True
    assert req_short.horizons_notice is not None

    # 显式 medium + query 明确短中 -> 仍只 medium
    res_med = resolve_analysis_horizons(
        ["medium"],
        query="短线和中线都分析一下",
    )
    assert res_med.resolved == ["medium"]
    assert res_med.resolution_source == RESOLUTION_SOURCE_EXPLICIT
    assert res_med.notice is not None

    req_med = main.AnalyzeRequest(
        symbol="600519.SH",
        horizons=["medium"],
        query="短线和中线都分析一下",
    )
    assert req_med.horizons == ["medium"]
    assert req_med.horizons_explicit is True

    # 未提供 + query 明确双档 -> 仍 short/default，不可自动扩档
    res_unprovided = resolve_analysis_horizons(
        query="分析 600519.SH 短线和中线机会",
    )
    assert res_unprovided.resolved == ["short"]
    assert res_unprovided.resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert res_unprovided.notice is not None

    req_unprovided = main.AnalyzeRequest(
        symbol="600519.SH",
        query="分析 600519.SH 短线和中线机会",
    )
    assert req_unprovided.horizons == ["short"]
    assert req_unprovided.horizons_explicit is False
    assert req_unprovided.horizons_resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert req_unprovided.horizons_notice is not None


# ── 10. 二次归一化不得翻转 default 也不得再用 query 重写 ─────────────────────────

def test_secondary_normalization_idempotent():
    res_default = resolve_analysis_horizons(explicit=False)
    assert res_default.resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert res_default.resolved == ["short"]

    # Re-normalize with query
    res_second = resolve_analysis_horizons(
        res_default,
        query="短线和中线都分析一下",
    )
    assert res_second.resolution_source == RESOLUTION_SOURCE_DEFAULT
    assert res_second.resolved == ["short"]


# ── 11. chat 路径不把 LLM 抽取的双档当显式勾选 ───────────────────────────────────

@pytest.mark.parametrize("stream", [False, True])
def test_chat_completions_does_not_treat_llm_dual_horizon_as_explicit(stream):
    captured = []
    user = MagicMock(id="user-1")
    request = main.ChatCompletionRequest(
        messages=[{"role": "user", "content": "分析 600519.SH 短线和中线机会"}],
        stream=stream,
        dry_run=True,
    )

    async def fake_run_job(job_id, analyze_request, *_args, **_kwargs):
        captured.append(analyze_request)
        main._set_job(job_id, status="completed", decision="DRY_RUN", result={})
        main._emit_job_event(job_id, "job.completed", {"job_id": job_id, "result": {}})

    # LLM extraction extracted dual horizons
    extraction = ("600519.SH", "2026-07-31", ["short", "medium"], [], [], {})

    async def run():
        with (
            patch.object(main, "_build_runtime_config", return_value={}),
            patch.object(main, "_compose_analysis_user_context", return_value={}),
            patch.object(main, "_job_store_instance", InMemoryJobStore()),
            patch.object(main, "_ai_extract_symbol_and_date", return_value=extraction),
            patch.object(main, "_ai_extract_symbol_and_date_streaming", return_value=extraction),
            patch.object(main, "_run_job", side_effect=fake_run_job),
        ):
            response = await main.chat_completions(request, current_user=user)
            if stream:
                body = "".join([chunk async for chunk in response.body_iterator])
                assert "job.completed" in body
            else:
                assert response["choices"][0]["finish_reason"] == "stop"

    asyncio.run(run())
    assert captured
    # Must NOT have dual horizons from chat natural language
    assert captured[0].horizons == ["short"]
    assert captured[0].horizons_explicit is False
    assert captured[0].horizons_resolution_source == RESOLUTION_SOURCE_DEFAULT


# ── 12. HTTP POST /v1/analyze 接口契约测试 ───────────────────────────────────────

def test_api_analyze_endpoint_horizon_contract():
    client = TestClient(main.app, raise_server_exceptions=False)
    captured = []

    async def fake_run_job(job_id, analyze_request, *_args, **_kwargs):
        captured.append(analyze_request)
        main._set_job(job_id, status="completed", decision="DRY_RUN", result={})
        main._emit_job_event(job_id, "job.completed", {"job_id": job_id, "result": {}})

    with (
        patch.object(main, "_job_store_instance", InMemoryJobStore()),
        patch.object(main, "_compose_analysis_user_context", return_value={}),
        patch.object(main, "_run_job", side_effect=fake_run_job),
        patch.object(main, "_get_reverse_stock_map", return_value={}),
    ):
        # 1. 字段未提供 -> 200 OK，默认 short
        captured.clear()
        resp = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "dry_run": True},
        )
        assert resp.status_code == 200
        assert captured[0].horizons == ["short"]
        assert captured[0].horizons_explicit is False
        assert captured[0].horizons_resolution_source == RESOLUTION_SOURCE_DEFAULT

        # 2. 显式 short -> 200 OK
        captured.clear()
        resp = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": ["short"], "dry_run": True},
        )
        assert resp.status_code == 200
        assert captured[0].horizons == ["short"]
        assert captured[0].horizons_explicit is True

        # 3. 显式 medium -> 200 OK
        captured.clear()
        resp = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": ["medium"], "dry_run": True},
        )
        assert resp.status_code == 200
        assert captured[0].horizons == ["medium"]
        assert captured[0].horizons_explicit is True

        # 4. 显式双档 -> 200 OK
        captured.clear()
        resp = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": ["short", "medium"], "dry_run": True},
        )
        assert resp.status_code == 200
        assert captured[0].horizons == ["short", "medium"]
        assert captured[0].horizons_explicit is True

        # 5. 显式 null -> 422 验证错误 (API 4xx)
        resp_null = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": None, "dry_run": True},
        )
        assert resp_null.status_code == 422

        # 6. 显式 [] -> 422 验证错误 (API 4xx)
        resp_empty = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": [], "dry_run": True},
        )
        assert resp_empty.status_code == 422

        # 7. 包含非法值 -> 422 验证错误 (API 4xx)
        resp_illegal = client.post(
            "/v1/analyze",
            json={"symbol": "600519.SH", "horizons": ["short", "bogus"], "dry_run": True},
        )
        assert resp_illegal.status_code == 422

        # 8. 显式 short + query 双档 -> 仍只 short
        captured.clear()
        resp = client.post(
            "/v1/analyze",
            json={
                "symbol": "600519.SH",
                "horizons": ["short"],
                "query": "短线和中线机会",
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        assert captured[0].horizons == ["short"]
        assert captured[0].horizons_explicit is True
