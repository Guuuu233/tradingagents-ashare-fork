"""Social media analyst input migration adapter (Task 11 / §3.1 / §六 / D-009 / D-010).

This module is the SOLE mode branching point for social media analyst inputs.
Centralizes the migration logic across rollout modes:
- 'disabled' (Gate 0–3): returns legacy news/zt/hot via legacy_proxy; does NOT use bundle for direction.
- 'shadow': reads/holds bundle, but analyst body text input still follows legacy fields; bundle does not enter direction.
- 'active': ONLY returns bundle + market_attention; missing data results in explicit gap text; FORBIDDEN to fallback to news/get_news.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from tradingagents.dataflows.social.contracts import (
    SentimentBundleV1,
    SocialDataContext,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.prompt_formatter import format_social_sections


@dataclass
class ResolvedSocialInputs:
    """Resolved social analyst inputs and trace metadata."""

    mode: str
    source_mode: str  # 'legacy_proxy' | 'shadow' | 'active'
    human_content: str
    source_status: str
    direction_allowed: bool
    reason_codes: List[str] = field(default_factory=list)
    bundle_id: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    bundle: Optional[Dict[str, Any]] = None
    market_attention: Optional[Dict[str, Any]] = None
    legacy_data: Optional[Dict[str, Any]] = None


def resolve_social_mode(
    mode: Optional[str] = None,
    social_data_context: Optional[Union[SocialDataContext, Dict[str, Any]]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve social rollout mode from parameters, context, or config.

    Precedence:
    1. Explicit mode param (if non-empty)
    2. social_data_context['mode']
    3. config['TA_SOCIAL_MODE'] / config['social_mode']
    4. os.environ['TA_SOCIAL_MODE']
    5. Default: 'disabled'
    """
    if mode and isinstance(mode, str) and mode.strip():
        m = mode.strip().lower()
        if m in ("disabled", "shadow", "active"):
            return m

    if social_data_context and isinstance(social_data_context, dict):
        ctx_mode = social_data_context.get("mode")
        if ctx_mode and isinstance(ctx_mode, str) and ctx_mode.strip():
            m = ctx_mode.strip().lower()
            if m in ("disabled", "shadow", "active"):
                return m

    if config and isinstance(config, dict):
        cfg_mode = config.get("TA_SOCIAL_MODE") or config.get("social_mode")
        if cfg_mode and isinstance(cfg_mode, str) and cfg_mode.strip():
            m = cfg_mode.strip().lower()
            if m in ("disabled", "shadow", "active"):
                return m

    env_mode = os.environ.get("TA_SOCIAL_MODE")
    if env_mode and env_mode.strip():
        m = env_mode.strip().lower()
        if m in ("disabled", "shadow", "active"):
            return m

    return "disabled"


def resolve_social_analyst_inputs(
    mode: Optional[str] = None,
    social_data_context: Optional[Union[SocialDataContext, Dict[str, Any]]] = None,
    market_data_context: Optional[Dict[str, Any]] = None,
    pool: Optional[Dict[str, Any]] = None,
    legacy_data: Optional[Dict[str, Any]] = None,
    market_attention: Optional[Dict[str, Any]] = None,
    ticker: Optional[str] = None,
    current_date: Optional[str] = None,
    ticker_display: Optional[str] = None,
    horizon_ctx: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> ResolvedSocialInputs:
    """Resolve inputs for social media analyst node based on mode.

    The SOLE branching point:
    - 'disabled': legacy news/zt/hot, trace source_mode='legacy_proxy'.
    - 'shadow': legacy news/zt/hot text, but captures bundle; direction_allowed=False.
    - 'active': bundle + market_attention ONLY; no news/get_news.
    """
    # 1. State extraction fallbacks
    if state and isinstance(state, dict):
        ticker = ticker or state.get("company_of_interest")
        current_date = current_date or state.get("trade_date")
        if not social_data_context:
            social_data_context = state.get("social_data_context")
        if not market_data_context:
            market_data_context = state.get("market_data_context")

    # 2. Pool extraction fallbacks
    if pool and isinstance(pool, dict):
        if not social_data_context:
            social_data_context = pool.get("social_data_context")
        if not market_data_context:
            market_data_context = pool.get("market_data_context")

    # 3. Market attention resolution
    if market_attention is None:
        if market_data_context and isinstance(market_data_context, dict):
            market_attention = market_data_context.get("market_attention")
        elif pool and isinstance(pool, dict):
            if "market_attention" in pool:
                market_attention = pool.get("market_attention")
            elif "market_data_context" in pool and isinstance(pool["market_data_context"], dict):
                market_attention = pool["market_data_context"].get("market_attention")

    # 4. Resolve rollout mode
    active_mode = resolve_social_mode(
        mode=mode,
        social_data_context=social_data_context,
        config=config,
    )

    cur_date_str = str(current_date or "")
    display_str = str(ticker_display or ticker or "")
    prefix = (horizon_ctx + "\n") if horizon_ctx else ""

    # ── Branch A: disabled mode (Gate 0–3 legacy proxy) ──────────────
    if active_mode == "disabled":
        news_text = (legacy_data or {}).get("news") or (pool.get("news") if pool else "无数据")
        zt_data = (legacy_data or {}).get("zt_data") or (pool.get("zt_pool") if pool else "无数据")
        hot_stocks = (legacy_data or {}).get("hot_stocks") or (pool.get("hot_stocks") if pool else "无数据")

        human_content = (
            prefix
            + f"以下是 {display_str} 在 {cur_date_str} 的舆情近似资料。\n\n"
            f"【get_news】\n{news_text}\n\n"
            f"【涨停池数据】\n{zt_data}\n\n"
            f"【雪球热门股票】\n{hot_stocks}\n"
        )

        return ResolvedSocialInputs(
            mode="disabled",
            source_mode="legacy_proxy",
            human_content=human_content,
            source_status="legacy_proxy",
            direction_allowed=False,
            reason_codes=[],
            bundle_id=None,
            evidence_refs=[],
            bundle=None,
            market_attention=market_attention,
            legacy_data={
                "news": news_text,
                "zt_data": zt_data,
                "hot_stocks": hot_stocks,
            },
        )

    # ── Branch B: shadow mode ─────────────────────────────────────────
    if active_mode == "shadow":
        # Extract bundle if present in context
        bundle_dict: Optional[Dict[str, Any]] = None
        if social_data_context and isinstance(social_data_context, dict):
            b = social_data_context.get("bundle")
            if isinstance(b, SentimentBundleV1):
                bundle_dict = b.to_dict()
            elif isinstance(b, dict):
                bundle_dict = b

        news_text = (legacy_data or {}).get("news") or (pool.get("news") if pool else "无数据")
        zt_data = (legacy_data or {}).get("zt_data") or (pool.get("zt_pool") if pool else "无数据")
        hot_stocks = (legacy_data or {}).get("hot_stocks") or (pool.get("hot_stocks") if pool else "无数据")

        human_content = (
            prefix
            + f"以下是 {display_str} 在 {cur_date_str} 的舆情近似资料。\n\n"
            f"【get_news】\n{news_text}\n\n"
            f"【涨停池数据】\n{zt_data}\n\n"
            f"【雪球热门股票】\n{hot_stocks}\n"
        )

        b_status = bundle_dict.get("status", "shadow") if bundle_dict else "shadow"
        b_reasons = list(bundle_dict.get("reason_codes", [])) if bundle_dict else []
        b_id = bundle_dict.get("bundle_id") if bundle_dict else None

        return ResolvedSocialInputs(
            mode="shadow",
            source_mode="shadow",
            human_content=human_content,
            source_status=b_status,
            direction_allowed=False,  # Bundle must not enter directional evidence in shadow mode
            reason_codes=b_reasons,
            bundle_id=b_id,
            evidence_refs=[],
            bundle=bundle_dict,
            market_attention=market_attention,
            legacy_data={
                "news": news_text,
                "zt_data": zt_data,
                "hot_stocks": hot_stocks,
            },
        )

    # ── Branch C: active mode ─────────────────────────────────────────
    # Active mode: ONLY bundle + market_attention. FORBIDDEN to fallback to news/get_news.
    bundle_dict = None
    if social_data_context and isinstance(social_data_context, dict):
        b = social_data_context.get("bundle")
        if isinstance(b, SentimentBundleV1):
            bundle_dict = b.to_dict()
        elif isinstance(b, dict):
            bundle_dict = b

    if bundle_dict is None:
        # Construct empty fallback bundle
        b_obj = create_empty_sentiment_bundle(
            status="empty",
            requested_as_of=cur_date_str,
            cutoff_at=f"{cur_date_str}T15:59:59Z",
            reason_codes=["social_empty_context"],
            symbol=ticker,
        )
        bundle_dict = b_obj.to_dict()

    b_status = bundle_dict.get("status", "empty")
    direction_allowed = bool(bundle_dict.get("direction_allowed", False))
    reasons = list(bundle_dict.get("reason_codes", []))
    b_id = bundle_dict.get("bundle_id")

    evidence_refs = []
    samples = bundle_dict.get("evidence_samples", [])
    for sample in samples:
        if isinstance(sample, dict):
            ref = sample.get("record_id") or sample.get("native_id") or sample.get("sample_id")
            if ref:
                evidence_refs.append(str(ref))

    human_content = format_social_sections(
        bundle=bundle_dict,
        market_attention=market_attention,
        ticker_display=display_str,
        current_date=cur_date_str,
        horizon_ctx=horizon_ctx or "",
        status=b_status,
        direction_allowed=direction_allowed,
        reason_codes=reasons,
    )

    return ResolvedSocialInputs(
        mode="active",
        source_mode="active",
        human_content=human_content,
        source_status=b_status,
        direction_allowed=direction_allowed,
        reason_codes=reasons,
        bundle_id=b_id,
        evidence_refs=evidence_refs,
        bundle=bundle_dict,
        market_attention=market_attention,
        legacy_data=None,
    )
