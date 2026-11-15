"""DAV-2453: user-selectable thinking level (quick/deep), default "follow the model".

Pins:
1. NULL -> no effort parameter anywhere (kwargs and the actual HTTP request body
   are identical to the pre-DAV-2453 path).
2. quick / deep are passed independently through ``_get_provider_kwargs``;
   openai -> reasoning_effort, google -> thinking_level.
3. model_config_snapshot / served_models record what was actually passed.
4. PATCH /v1/config: whitelist only (400 otherwise), "follow" clears to NULL,
   existing columns untouched.
5. /v1/models catalog parsing + additive migration / rollback.
"""

import json
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reasoning_effort import (
    normalize_reasoning_effort,
    parse_supported_levels,
)


def _cfg(**overrides):
    cfg = {
        "project_dir": tempfile.mkdtemp(prefix="ta-effort-"),
        "llm_provider": "openai",
        "quick_think_llm": "m-quick",
        "deep_think_llm": "m-deep",
        "backend_url": "https://api.example.com/v1",
        "api_key": "k",
        "max_debate_rounds": 1,
        "max_risk_discuss_rounds": 1,
        "max_recur_limit": 10,
        "google_thinking_level": None,
        "openai_reasoning_effort": None,
    }
    cfg.update(overrides)
    return cfg


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, provider, model, base_url=None, **kwargs):
        self.calls.append({"provider": provider, "model": model, "kwargs": kwargs})
        client = MagicMock()
        client.get_llm.return_value = MagicMock()
        return client


class _FakeDbCtx:
    def __enter__(self):
        return MagicMock()

    def __exit__(self, *_exc):
        return False


def _build_graph(config, resolved_via="tier_default", role_provider=None):
    from api.services import role_routing_service

    resolved = {
        role: {
            "role_key": role,
            "resolved_via": resolved_via,
            "fallback_used": resolved_via != "role_binding",
            "provider_type": (role_provider or {}).get(role, config["llm_provider"]),
            "model_name": config[f"{role_routing_service.ROLE_DEFAULT_TIERS[role]}_think_llm"],
            "base_url": config["backend_url"],
            "api_key": "k",
            "temperature": None,
            "max_tokens": None,
        }
        for role in role_routing_service.ALL_ROLES
    }
    rec = _Recorder()
    with ExitStack() as stack:
        for target in (
            "FinancialSituationMemory", "GraphSetup", "ConditionalLogic",
            "Propagator", "Reflector", "SignalProcessor", "set_config", "ToolNode",
        ):
            stack.enter_context(patch(f"tradingagents.graph.trading_graph.{target}"))
        stack.enter_context(patch("tradingagents.graph.trading_graph.create_llm_client", rec))
        stack.enter_context(
            patch("api.services.role_routing_service.resolve_all_roles", return_value=resolved)
        )
        stack.enter_context(patch("api.database.get_db_ctx", _FakeDbCtx))
        # user_id so role resolution runs; pool bypassed so every role hits the recorder
        stack.enter_context(patch.dict("os.environ", {"TA_LLM_POOL_DISABLED": "1"}))
        graph = TradingAgentsGraph(config=dict(config, user_id="u1"), data_collector=MagicMock())
    return graph, rec


def _kwargs_for(rec, model):
    return [c["kwargs"] for c in rec.calls if c["model"] == model]


# ---------------------------------------------------------------- 1 & 2: kwargs


@pytest.mark.parametrize("provider,param", [("openai", "reasoning_effort"), ("google", "thinking_level")])
def test_null_effort_sends_nothing_and_matches_legacy_kwargs(provider, param):
    graph, rec = _build_graph(_cfg(llm_provider=provider))
    legacy = graph._get_provider_kwargs()
    assert graph._get_provider_kwargs("quick") == legacy
    assert graph._get_provider_kwargs("deep") == legacy
    assert param not in legacy
    for call in rec.calls:
        assert param not in call["kwargs"]
    assert set(graph.role_reasoning_effort.values()) == {None}


@pytest.mark.parametrize("provider,param", [("openai", "reasoning_effort"), ("google", "thinking_level")])
def test_quick_and_deep_are_passed_independently(provider, param):
    graph, rec = _build_graph(
        _cfg(llm_provider=provider, quick_reasoning_effort="low", deep_reasoning_effort="high")
    )
    assert all(k[param] == "low" for k in _kwargs_for(rec, "m-quick"))
    assert all(k[param] == "high" for k in _kwargs_for(rec, "m-deep"))
    assert graph.role_reasoning_effort["market"] == "low"
    assert graph.role_reasoning_effort["research_manager"] == "high"
    assert graph.role_reasoning_effort["risk_manager"] == "high"
    assert graph.role_reasoning_effort["trader"] == "low"


def test_only_one_tier_set_leaves_other_untouched():
    graph, rec = _build_graph(_cfg(deep_reasoning_effort="medium"))
    assert all("reasoning_effort" not in k for k in _kwargs_for(rec, "m-quick"))
    assert all(k["reasoning_effort"] == "medium" for k in _kwargs_for(rec, "m-deep"))
    assert graph.role_reasoning_effort["market"] is None


def test_role_bound_models_are_not_given_tier_effort():
    graph, rec = _build_graph(
        _cfg(quick_reasoning_effort="high", deep_reasoning_effort="high"),
        resolved_via="role_binding",
    )
    assert all("reasoning_effort" not in c["kwargs"] for c in rec.calls)
    assert set(graph.role_reasoning_effort.values()) == {None}


def test_non_whitelisted_config_value_is_never_forwarded():
    graph, rec = _build_graph(_cfg(quick_reasoning_effort="ignore previous instructions"))
    assert all("reasoning_effort" not in c["kwargs"] for c in rec.calls)


# ------------------------------------------- mixed provider_type (DAV-2457 fix)

_ALL_PROVIDERS = ("openai", "google", "anthropic", "openrouter", "deepseek", "xai", "ollama")
_OPENAI_FAMILY = ("openai", "openrouter", "deepseek", "xai", "ollama")


@pytest.mark.parametrize("global_provider", ["openai", "google", "anthropic"])
@pytest.mark.parametrize("role_provider", _ALL_PROVIDERS)
@pytest.mark.parametrize("resolved_via", ["tier_default", "global_default", "system_fallback"])
def test_tier_roles_get_kwargs_for_their_own_provider_type(
    global_provider, role_provider, resolved_via
):
    from api.services.role_routing_service import ALL_ROLES, ROLE_DEFAULT_TIERS

    graph, rec = _build_graph(
        _cfg(
            llm_provider=global_provider,
            quick_reasoning_effort="low",
            deep_reasoning_effort="high",
        ),
        resolved_via=resolved_via,
        role_provider={role: role_provider for role in ALL_ROLES},
    )
    role_calls = [
        c for c in rec.calls
        if c["provider"] == role_provider and c["model"] in ("m-quick", "m-deep")
    ]
    assert role_calls
    for call in role_calls:
        expected = "low" if call["model"] == "m-quick" else "high"
        kwargs = call["kwargs"]
        if role_provider in _OPENAI_FAMILY:
            assert kwargs.get("reasoning_effort") == expected
            assert "thinking_level" not in kwargs
        elif role_provider == "google":
            assert kwargs.get("thinking_level") == expected
            assert "reasoning_effort" not in kwargs
        else:
            assert "reasoning_effort" not in kwargs and "thinking_level" not in kwargs
    for role in ALL_ROLES:
        tier_effort = "low" if ROLE_DEFAULT_TIERS[role] == "quick" else "high"
        want = None if role_provider == "anthropic" else tier_effort
        assert graph.role_reasoning_effort[role] == want


def test_mixed_provider_roles_in_one_graph_record_per_role_consumption():
    from api.services.role_routing_service import ALL_ROLES, ROLE_DEFAULT_TIERS

    cycle = ["openai", "google", "anthropic", "deepseek"]
    role_provider = {role: cycle[idx % len(cycle)] for idx, role in enumerate(ALL_ROLES)}
    graph, rec = _build_graph(
        _cfg(llm_provider="openai", quick_reasoning_effort="medium", deep_reasoning_effort="high"),
        role_provider=role_provider,
    )
    for role in ALL_ROLES:
        want = "medium" if ROLE_DEFAULT_TIERS[role] == "quick" else "high"
        if role_provider[role] == "anthropic":
            want = None
        assert graph.role_reasoning_effort[role] == want


def test_null_effort_mixed_providers_pass_and_record_nothing():
    from api.services.role_routing_service import ALL_ROLES

    cycle = ["openai", "google", "anthropic", "deepseek", "xai", "ollama", "openrouter"]
    role_provider = {role: cycle[idx % len(cycle)] for idx, role in enumerate(ALL_ROLES)}
    graph, rec = _build_graph(_cfg(llm_provider="openai"), role_provider=role_provider)
    for call in rec.calls:
        assert "reasoning_effort" not in call["kwargs"]
        assert "thinking_level" not in call["kwargs"]
    assert set(graph.role_reasoning_effort.values()) == {None}


def test_binding_role_with_foreign_provider_does_not_record_unconsumed_effort():
    # role_binding keeps the legacy (tier-agnostic, global-provider) kwargs; a
    # global openai_reasoning_effort must not be recorded for a google role.
    from api.services.role_routing_service import ALL_ROLES

    graph, rec = _build_graph(
        _cfg(llm_provider="openai", openai_reasoning_effort="high"),
        resolved_via="role_binding",
        role_provider={role: "google" for role in ALL_ROLES},
    )
    assert set(graph.role_reasoning_effort.values()) == {None}


def test_get_provider_kwargs_provider_override_and_legacy_default():
    graph, _ = _build_graph(_cfg(llm_provider="openai", quick_reasoning_effort="low"))
    assert graph._get_provider_kwargs("quick", provider="google") == {
        "thinking_level": "low",
        "api_key": "k",
    }
    assert graph._get_provider_kwargs("quick", provider="anthropic") == {"api_key": "k"}
    assert graph._get_provider_kwargs("quick", provider="deepseek") == {"reasoning_effort": "low"}
    assert graph._get_provider_kwargs("quick") == graph._get_provider_kwargs("quick", provider="openai")
    assert graph._get_provider_kwargs(provider="deepseek") == {}


# ---------------------------------------------------------------- 1: request body


def _capture_body(effort_kwargs):
    """Send one real ChatOpenAI request through a MockTransport; return body bytes."""
    from tradingagents.llm_clients import create_llm_client

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(
            200,
            json={
                "id": "x", "object": "chat.completion", "created": 0, "model": "m-quick",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    llm = create_llm_client(
        provider="openai", model="m-quick", base_url="https://api.example.com/v1",
        api_key="k", max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **effort_kwargs,
    ).get_llm()
    llm.invoke("hello")
    assert len(seen) == 1
    return seen[0]


def test_null_effort_request_body_is_byte_identical_to_legacy():
    graph, _ = _build_graph(_cfg())
    legacy_kwargs = {k: v for k, v in graph._get_provider_kwargs().items() if k != "api_key"}
    tier_kwargs = {k: v for k, v in graph._get_provider_kwargs("quick").items() if k != "api_key"}
    assert _capture_body(tier_kwargs) == _capture_body(legacy_kwargs)
    assert b"reasoning_effort" not in _capture_body(tier_kwargs)


def test_selected_effort_appears_in_request_body():
    graph, _ = _build_graph(_cfg(quick_reasoning_effort="low"))
    kwargs = {k: v for k, v in graph._get_provider_kwargs("quick").items() if k != "api_key"}
    assert json.loads(_capture_body(kwargs))["reasoning_effort"] == "low"


# ---------------------------------------------------------------- helpers


def test_normalize_reasoning_effort_whitelist():
    assert normalize_reasoning_effort(" High ") == "high"
    for bad in ("none", "xhigh", "", None, 3, "low; drop table"):
        with pytest.raises(ValueError):
            normalize_reasoning_effort(bad)


def test_parse_supported_levels_shapes():
    assert parse_supported_levels({"supported_reasoning_levels": [{"effort": "high"}]}) == ["high"]
    assert parse_supported_levels(
        {"supported_reasoning_levels": [{"effort": "high"}, "low", {"effort": "none"}, "low"]}
    ) == ["low", "high"]
    assert parse_supported_levels({"id": "x"}) is None
    assert parse_supported_levels({"supported_reasoning_levels": "high"}) is None
    assert parse_supported_levels({"supported_reasoning_levels": []}) == []


# ---------------------------------------------------------------- 3: snapshot


def test_traceability_records_effort_per_role():
    from api.main import _attach_traceability_fields

    graph = MagicMock()
    graph.role_reasoning_effort = {"market": "low", "research_manager": None}
    with patch("api.main._compute_input_snapshot", return_value={}), \
         patch("api.main._build_run_identity", return_value={}), \
         patch("api.usage_logging.build_served_models_summary",
               return_value={"by_role": {}, "call_count": 1}):
        out = _attach_traceability_fields({}, pool=None, graph=graph, prompt_snapshot={}, report_id="r")
    assert out["served_models"]["reasoning_effort_by_role"] == {"market": "low", "research_manager": None}


# ---------------------------------------------------------------- 4: API


@pytest.fixture()
def api_client():
    from api.main import app
    from tests.test_api_smoke import _auth_unique

    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {_auth_unique(client)}"}
    with patch("api.main._run_config_warmup"):
        yield client, headers


def test_patch_saves_and_reads_back_and_follow_clears(api_client):
    client, headers = api_client
    r = client.patch("/v1/config", headers=headers, json={
        "quick_reasoning_effort": "low", "deep_reasoning_effort": "high", "warmup": False,
    })
    assert r.status_code == 200, r.text
    cfg = client.get("/v1/config", headers=headers).json()
    assert cfg["quick_reasoning_effort"] == "low"
    assert cfg["deep_reasoning_effort"] == "high"

    # omitted fields are untouched
    r = client.patch("/v1/config", headers=headers, json={"max_debate_rounds": 2, "warmup": False})
    assert r.status_code == 200, r.text
    cfg = client.get("/v1/config", headers=headers).json()
    assert (cfg["quick_reasoning_effort"], cfg["deep_reasoning_effort"]) == ("low", "high")

    # "follow" -> NULL
    r = client.patch("/v1/config", headers=headers, json={"quick_reasoning_effort": "follow", "warmup": False})
    assert r.status_code == 200, r.text
    cfg = client.get("/v1/config", headers=headers).json()
    assert cfg["quick_reasoning_effort"] is None
    assert cfg["deep_reasoning_effort"] == "high"


@pytest.mark.parametrize("bad", ["none", "xhigh", "", "HIGH; DROP", 5])
def test_patch_rejects_non_whitelisted_values_with_400_and_writes_nothing(api_client, bad):
    client, headers = api_client
    r = client.patch("/v1/config", headers=headers, json={
        "quick_reasoning_effort": "low", "deep_reasoning_effort": bad, "warmup": False,
    })
    assert r.status_code == 400, r.text
    cfg = client.get("/v1/config", headers=headers).json()
    assert cfg["quick_reasoning_effort"] is None
    assert cfg["deep_reasoning_effort"] is None


def test_new_users_default_to_follow_model(api_client):
    client, headers = api_client
    cfg = client.get("/v1/config", headers=headers).json()
    assert cfg["quick_reasoning_effort"] is None
    assert cfg["deep_reasoning_effort"] is None


# ---------------------------------------------------------------- 5: catalog + migration


def test_models_payload_reasoning_levels():
    from api.main import _extract_reasoning_levels_from_payload

    body = json.dumps({"data": [
        {"id": "a", "supported_reasoning_levels": [{"effort": "high"}]},
        {"id": "b", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}, {"effort": "high"}]},
        {"id": "c"},
    ]})
    assert _extract_reasoning_levels_from_payload(body) == {
        "a": ["high"], "b": ["low", "medium", "high"], "c": None,
    }


def test_migration_is_additive_nullable_and_rollbackable(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    import api.database as database

    eng = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE user_llm_configs (user_id VARCHAR(36) PRIMARY KEY, quick_think_llm VARCHAR(255))"
        ))
        conn.execute(text(
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)"
        ))
        conn.execute(text("INSERT INTO user_llm_configs VALUES ('u', 'm')"))
    monkeypatch.setattr(database, "engine", eng)
    monkeypatch.setattr(database, "_migrate_tokens_to_hashed", lambda: None)
    monkeypatch.setattr(database, "_migrate_api_keys_reencrypt", lambda: None)

    database._ensure_user_schema()
    with eng.begin() as conn:
        cols = {r[1]: r for r in conn.execute(text("PRAGMA table_info(user_llm_configs)"))}
        row = conn.execute(text(
            "SELECT quick_think_llm, quick_reasoning_effort, deep_reasoning_effort FROM user_llm_configs"
        )).one()
    assert row == ("m", None, None)  # old row untouched, not backfilled
    assert cols["quick_reasoning_effort"][3] == 0 and cols["quick_reasoning_effort"][4] is None

    database.rollback_reasoning_effort_columns()
    with eng.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(user_llm_configs)"))}
    assert "quick_reasoning_effort" not in cols and "deep_reasoning_effort" not in cols
    assert "quick_think_llm" in cols


# ------------------------------------- 6: member-registered ∩ group levels (DAV-2457)


def test_intersect_and_effective_effort_pure_rules():
    from tradingagents.reasoning_effort import effective_effort, intersect_levels

    assert intersect_levels(["low", "medium", "high"], ["high", "low"]) == ["low", "high"]
    assert intersect_levels(["low"], ["high"]) == []
    assert intersect_levels(["low"], None) == []
    assert intersect_levels() == []

    assert effective_effort("low", ["low", "medium", "high"]) == "low"
    assert effective_effort(" HIGH ", ["high"]) == "high"
    # empty intersection / unreadable group / invalid or missing member -> not adjustable
    assert effective_effort("low", ["high"]) is None
    assert effective_effort("low", []) is None
    assert effective_effort("low", None) is None
    assert effective_effort(None, ["low", "medium", "high"]) is None
    assert effective_effort("none", ["low", "medium", "high"]) is None
    assert effective_effort(5, ["low", "medium", "high"]) is None


def _overrides_with(user_cfg, catalog=None, fetch_error=None):
    from types import SimpleNamespace

    from api import main as api_main

    cfg = SimpleNamespace(
        llm_provider="openai",
        backend_url="http://127.0.0.1:3425/v1",
        quick_think_llm="g",
        deep_think_llm="g",
        max_debate_rounds=None,
        max_risk_discuss_rounds=None,
        quick_reasoning_effort=None,
        deep_reasoning_effort=None,
        api_key_encrypted=None,
    )
    for key, value in user_cfg.items():
        setattr(cfg, key, value)

    fetch = MagicMock(side_effect=fetch_error) if fetch_error else MagicMock(
        return_value=(sorted(catalog or {}), "u", dict(catalog or {}))
    )
    with patch.object(api_main.auth_service, "get_user_llm_config", return_value=cfg), \
         patch.object(api_main.auth_service, "decrypt_secret", return_value=""), \
         patch.object(api_main, "_fetch_available_models", fetch):
        return api_main._user_config_overrides("user-1", db=MagicMock()), fetch


def test_runtime_overrides_keep_effort_inside_group_levels():
    out, _ = _overrides_with(
        {"quick_reasoning_effort": "low", "deep_reasoning_effort": "high"},
        catalog={"g": ["low", "medium", "high"]},
    )
    assert (out["quick_reasoning_effort"], out["deep_reasoning_effort"]) == ("low", "high")


def test_runtime_overrides_drop_effort_outside_group_levels():
    out, _ = _overrides_with(
        {"quick_reasoning_effort": "low", "deep_reasoning_effort": "high"},
        catalog={"g": ["high"]},
    )
    assert "quick_reasoning_effort" not in out  # empty intersection -> not passed
    assert out["deep_reasoning_effort"] == "high"


@pytest.mark.parametrize("catalog", [{"g": None}, {"g": []}, {}, {"other": ["low", "medium", "high"]}])
def test_runtime_overrides_drop_effort_when_group_levels_unreadable(catalog):
    out, _ = _overrides_with({"quick_reasoning_effort": "medium"}, catalog=catalog)
    assert "quick_reasoning_effort" not in out


def test_runtime_overrides_drop_effort_when_catalog_fetch_fails():
    out, _ = _overrides_with({"deep_reasoning_effort": "high"}, fetch_error=RuntimeError("down"))
    assert "deep_reasoning_effort" not in out


def test_runtime_overrides_null_effort_does_no_catalog_io():
    out, fetch = _overrides_with({}, catalog={"g": ["high"]})
    fetch.assert_not_called()
    assert "quick_reasoning_effort" not in out and "deep_reasoning_effort" not in out


def test_runtime_overrides_use_each_tiers_own_model():
    out, _ = _overrides_with(
        {"quick_think_llm": "q", "deep_think_llm": "d",
         "quick_reasoning_effort": "low", "deep_reasoning_effort": "low"},
        catalog={"q": ["low", "high"], "d": ["high"]},
    )
    assert out["quick_reasoning_effort"] == "low"
    assert "deep_reasoning_effort" not in out


def test_dropped_effort_reaches_client_and_snapshot_as_nothing():
    out, _ = _overrides_with({"quick_reasoning_effort": "low"}, catalog={"g": ["high"]})
    graph, recorder = _build_graph(_cfg(**out))
    assert all("reasoning_effort" not in call["kwargs"] for call in recorder.calls)
    assert set(graph.role_reasoning_effort.values()) == {None}
