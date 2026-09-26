"""DAV-1305: saving 常规/推理模型 must sync tier default ModelProfiles so unbound
roles resolve to the settings-page models instead of stale tier profiles."""
from uuid import uuid4

from api.database import Base, ModelProfileDB, ProviderDB, RoleBindingDB, UserDB, UserLLMConfigDB
from api.services import auth_service
from api.services.role_routing_service import (
    ALL_ROLES,
    ROLE_DEFAULT_TIERS,
    apply_role_preset,
    resolve_all_roles,
    resolve_role_model_config,
)


def _make_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def _seed_stale_tier_account(db):
    """Account with an existing provider whose tier profiles point at old models."""
    user_id = uuid4().hex
    db.add(UserDB(id=user_id, email=f"{user_id}@test.local"))
    provider_id = uuid4().hex
    db.add(
        ProviderDB(
            id=provider_id,
            user_id=user_id,
            provider_type="openai",
            base_url="http://100.65.130.33:8317/v1",
            display_name="默认厂商 (openai)",
            enabled=True,
        )
    )
    db.add(
        ModelProfileDB(
            id=uuid4().hex,
            user_id=user_id,
            provider_id=provider_id,
            model_name="gemini-3.5-flash-low",
            display_name="常规模型 (gemini-3.5-flash-low)",
            tier="quick",
            is_default=True,
        )
    )
    db.add(
        ModelProfileDB(
            id=uuid4().hex,
            user_id=user_id,
            provider_id=provider_id,
            model_name="gemini-3.6-flash-high",
            display_name="推理模型 (gemini-3.6-flash-high)",
            tier="deep",
            is_default=False,
        )
    )
    db.commit()
    return user_id, provider_id


def test_saving_models_replaces_stale_tier_profiles():
    """Account already has a provider; tier profile holds an old model.
    After saving new 常规/推理模型, unbound roles resolve to the new models."""
    db = _make_session()
    try:
        user_id, provider_id = _seed_stale_tier_account(db)

        auth_service.upsert_user_llm_config(
            db,
            user_id,
            llm_provider="openai",
            backend_url="http://100.65.130.33:8317/v1",
            quick_think_llm="gpt-6-luna",
            deep_think_llm="gpt-6-luna",
        )

        resolved = resolve_role_model_config(db, user_id, "market")
        assert resolved["model_name"] == "gpt-6-luna"
        assert resolved["resolved_via"] == "tier_default"
        assert resolved["provider_id"] == provider_id

        # Stale tier profiles must lose their tier flags.
        all_profiles = db.query(ModelProfileDB).filter(ModelProfileDB.user_id == user_id).all()
        tiers = {p.model_name: p.tier for p in all_profiles if p.model_name.startswith("gemini")}
        assert tiers == {"gemini-3.5-flash-low": None, "gemini-3.6-flash-high": None}
        luna_tiers = {p.tier for p in all_profiles if p.model_name == "gpt-6-luna"}
        assert luna_tiers == {"quick", "deep"}
        deep_roles = resolve_all_roles(db, user_id)
        assert deep_roles["research_manager"]["model_name"] == "gpt-6-luna"
        assert deep_roles["research_manager"]["resolved_via"] == "tier_default"
    finally:
        db.close()


def test_single_preset_resolves_all_roles_to_current_page_models():
    """After 单模型 preset deletes all bindings, every role must resolve to the
    current 常规/推理模型 shown on the settings page."""
    db = _make_session()
    try:
        user_id, _ = _seed_stale_tier_account(db)
        auth_service.upsert_user_llm_config(
            db,
            user_id,
            llm_provider="openai",
            backend_url="http://100.65.130.33:8317/v1",
            quick_think_llm="gpt-6-luna",
            deep_think_llm="gpt-6-luna",
        )

        apply_role_preset(db, user_id, "single")
        assert db.query(RoleBindingDB).filter(RoleBindingDB.user_id == user_id).count() == 0

        resolved = resolve_all_roles(db, user_id)
        assert set(resolved.keys()) == set(ALL_ROLES)
        for role, cfg in resolved.items():
            assert cfg["model_name"] == "gpt-6-luna", role
            assert cfg["resolved_via"] == "tier_default", role
    finally:
        db.close()


def test_distinct_quick_and_deep_tiers_sync_independently():
    db = _make_session()
    try:
        user_id, _ = _seed_stale_tier_account(db)
        auth_service.upsert_user_llm_config(
            db,
            user_id,
            llm_provider="openai",
            backend_url="http://100.65.130.33:8317/v1",
            quick_think_llm="qwen-turbo",
            deep_think_llm="qwen-max",
        )
        resolved = resolve_all_roles(db, user_id)
        for role, cfg in resolved.items():
            expected = "qwen-max" if ROLE_DEFAULT_TIERS[role] == "deep" else "qwen-turbo"
            assert cfg["model_name"] == expected, role
    finally:
        db.close()


def test_explicit_role_and_group_bindings_keep_priority_over_tier_sync():
    """Regression: role/group bindings still win; sync only rewrites tier flags."""
    db = _make_session()
    try:
        user_id, provider_id = _seed_stale_tier_account(db)
        bound_profile_id = uuid4().hex
        db.add(
            ModelProfileDB(
                id=bound_profile_id,
                user_id=user_id,
                provider_id=provider_id,
                model_name="bound-model-x",
                display_name="bound-model-x",
            )
        )
        db.add(
            RoleBindingDB(
                id=uuid4().hex,
                user_id=user_id,
                target_type="role",
                target_key="market",
                model_profile_id=bound_profile_id,
            )
        )
        db.add(
            RoleBindingDB(
                id=uuid4().hex,
                user_id=user_id,
                target_type="group",
                target_key="researchers",
                model_profile_id=bound_profile_id,
            )
        )
        db.commit()

        auth_service.upsert_user_llm_config(
            db,
            user_id,
            llm_provider="openai",
            backend_url="http://100.65.130.33:8317/v1",
            quick_think_llm="gpt-6-luna",
            deep_think_llm="gpt-6-luna",
        )

        assert resolve_role_model_config(db, user_id, "market")["model_name"] == "bound-model-x"
        bull = resolve_role_model_config(db, user_id, "bull_researcher")
        assert bull["model_name"] == "bound-model-x"
        assert bull["resolved_via"] == "group_binding"
        # Unbound roles still follow the synced tier profiles.
        assert resolve_role_model_config(db, user_id, "news")["model_name"] == "gpt-6-luna"
    finally:
        db.close()
