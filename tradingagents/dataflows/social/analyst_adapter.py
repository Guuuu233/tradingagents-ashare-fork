"""Social media analyst input migration adapter (Task 11 & Task 15 / §3.1 / §六 / D-009 / D-010 / Gate 4).

This module is the SOLE mode branching point for social media analyst inputs.
Centralizes the input resolution across rollout modes:
- 'disabled': returns 4-section structured text with status='not_applicable', direction_allowed=False; does NOT call provider or read news.
- 'shadow': reads/holds bundle, formats 4-section structured text with direction_allowed=False; bundle does not enter direction; source_mode='shadow'.
- 'active': ONLY returns bundle + market_attention; missing data results in explicit gap text; FORBIDDEN to fallback to news/get_news.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from tradingagents.dataflows.social.contracts import (
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_NOT_APPLICABLE,
    SentimentBundleV1,
    SocialDataContext,
    SocialStatus,
    create_empty_sentiment_bundle,
)
from tradingagents.dataflows.social.prompt_formatter import format_social_sections


@dataclass
class ResolvedSocialInputs:
    """Resolved social analyst inputs and trace metadata."""

    mode: str
    source_mode: str
    human_content: str
    source_status: str
    direction_allowed: bool
    reason_codes: List[str] = field(default_factory=list)
    bundle_id: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    bundle: Optional[Dict[str, Any]] = None
    market_attention: Optional[Dict[str, Any]] = None


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
    market_attention: Optional[Dict[str, Any]] = None,
    ticker: Optional[str] = None,
    current_date: Optional[str] = None,
    ticker_display: Optional[str] = None,
    horizon_ctx: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> ResolvedSocialInputs:
    """Resolve inputs for social media analyst node based on mode.

    The SOLE branching point:
    - 'disabled': 4-section not_applicable text, trace source_mode='disabled', source_status='not_applicable'.
    - 'shadow': 4-section text from bundle with direction_allowed=False, trace source_mode='shadow'.
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

    # ── Branch A: disabled mode (Gate 4: not_applicable, no news fallback) ──
    if active_mode == "disabled":
        bundle_dict = None
        if social_data_context and isinstance(social_data_context, dict):
            b = social_data_context.get("bundle")
            if isinstance(b, SentimentBundleV1):
                bundle_dict = b.to_dict()
            elif isinstance(b, dict):
                bundle_dict = b

        if bundle_dict is None:
            b_obj = create_empty_sentiment_bundle(
                status=SocialStatus.NOT_APPLICABLE.value,
                requested_as_of=cur_date_str or "unknown",
                cutoff_at="",
                reason_codes=[REASON_SOCIAL_NOT_APPLICABLE],
                symbol=ticker or "",
            )
            bundle_dict = b_obj.to_dict()

        reasons = list(bundle_dict.get("reason_codes", [])) or [REASON_SOCIAL_NOT_APPLICABLE]

        human_content = format_social_sections(
            bundle=bundle_dict,
            market_attention=market_attention,
            ticker_display=display_str,
            current_date=cur_date_str,
            horizon_ctx=horizon_ctx or "",
            status=SocialStatus.NOT_APPLICABLE.value,
            direction_allowed=False,
            reason_codes=reasons,
        )

        return ResolvedSocialInputs(
            mode="disabled",
            source_mode="disabled",
            human_content=human_content,
            source_status=SocialStatus.NOT_APPLICABLE.value,
            direction_allowed=False,
            reason_codes=reasons,
            bundle_id=None,
            evidence_refs=[],
            bundle=bundle_dict,
            market_attention=market_attention,
        )

    # ── Branch B: shadow mode (Gate 4: structured bundle text, direction_allowed=False) ──
    if active_mode == "shadow":
        bundle_dict = None
        if social_data_context and isinstance(social_data_context, dict):
            b = social_data_context.get("bundle")
            if isinstance(b, SentimentBundleV1):
                bundle_dict = b.to_dict()
            elif isinstance(b, dict):
                bundle_dict = b

        if bundle_dict is None:
            fallback_cutoff = "unknown"
            if cur_date_str:
                try:
                    from tradingagents.dataflows.social.provider import compute_as_of_cutoff

                    _, cutoff_utc, _ = compute_as_of_cutoff(cur_date_str)
                    fallback_cutoff = (
                        cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if cutoff_utc.microsecond == 0
                        else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    )
                except Exception:
                    fallback_cutoff = "unknown"

            b_obj = create_empty_sentiment_bundle(
                status="empty",
                requested_as_of=cur_date_str or "unknown",
                cutoff_at=fallback_cutoff,
                reason_codes=[REASON_SOCIAL_EMPTY],
                symbol=ticker or "",
            )
            bundle_dict = b_obj.to_dict()

        b_status = bundle_dict.get("status", "shadow")
        b_reasons = list(bundle_dict.get("reason_codes", []))
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
            direction_allowed=False,  # strictly False in shadow mode
            reason_codes=b_reasons,
        )

        return ResolvedSocialInputs(
            mode="shadow",
            source_mode="shadow",
            human_content=human_content,
            source_status=b_status,
            direction_allowed=False,
            reason_codes=b_reasons,
            bundle_id=b_id,
            evidence_refs=evidence_refs,
            bundle=bundle_dict,
            market_attention=market_attention,
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
        # Construct empty fallback bundle without hardcoded cutoff
        fallback_cutoff = "unknown"
        if cur_date_str:
            try:
                from tradingagents.dataflows.social.provider import compute_as_of_cutoff

                _, cutoff_utc, _ = compute_as_of_cutoff(cur_date_str)
                fallback_cutoff = (
                    cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if cutoff_utc.microsecond == 0
                    else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                )
            except Exception:
                fallback_cutoff = "unknown"

        b_obj = create_empty_sentiment_bundle(
            status="empty",
            requested_as_of=cur_date_str or "unknown",
            cutoff_at=fallback_cutoff,
            reason_codes=[REASON_SOCIAL_EMPTY],
            symbol=ticker or "",
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
    )
