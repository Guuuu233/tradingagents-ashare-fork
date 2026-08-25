"""H1a Shadow Credit Metrics Module (P1-S).

Pure function implementation for computing observational shadow credit metrics
without modifying debate topology or influencing investment decisions (zero-weighting).
"""

from typing import Any, Mapping, Optional

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    get_protocol_metadata,
)
from tradingagents.agents.utils.debate_metrics import (
    SEVEN_REPORT_KEYS,
    _extract_cited_debate_numbers,
    extract_numerical_tokens,
)

SCHEMA_VERSION: str = "h1a_json_v1"

# Mapping from report key to canonical role slug
REPORT_KEY_TO_ROLE: dict[str, str] = {
    "macro_report": "macro",
    "market_report": "market",
    "sentiment_report": "sentiment",
    "news_report": "news",
    "fundamentals_report": "fundamentals",
    "smart_money_report": "smart_money",
    "volume_price_report": "volume_price",
}


def _is_bull(speaker_key: str, stance: str) -> bool:
    """Return True if speaker or stance indicates bullish side."""
    sp = speaker_key.lower().strip()
    st = stance.lower().strip()
    return sp in ("bull", "看多", "多方", "多头") or st in ("bullish", "bull", "看多", "多头")


def _is_bear(speaker_key: str, stance: str) -> bool:
    """Return True if speaker or stance indicates bearish side."""
    sp = speaker_key.lower().strip()
    st = stance.lower().strip()
    return sp in ("bear", "看空", "空方", "空头") or st in ("bearish", "bear", "看空", "空头")


def calculate_shadow_credit_metrics(
    result_data_or_state: Mapping[str, Any],
    *,
    version: Optional[str] = None,
    t_plus_5_price: Optional[float] = None,
) -> dict[str, Any]:
    """Calculate shadow credit metrics from result_data or investment_debate_state.

    This function is strictly pure, read-only, deterministic, and replayable.
    Credit weighting is permanently disabled (credit_weighting_enabled=False).
    """
    if not isinstance(result_data_or_state, Mapping):
        return {
            "schema_version": SCHEMA_VERSION,
            "credit_weighting_enabled": False,
            "bull_verified_rate": None,
            "bear_verified_rate": None,
            "bull_challenge_adoption_rate": None,
            "bear_challenge_adoption_rate": None,
            "analyst_utilization_by_role": {role: None for role in REPORT_KEY_TO_ROLE.values()},
            "manager_evidence_coverage": None,
            "manager_consistency_gate_triggered": False,
            "t_plus_5_direction_hit": None,
            "sample_count": 1,
            "protocol_version": version or PROTOCOL_VERSION_V1_LEGACY,
            "model_id_by_stance": {
                "bull": None,
                "bear": None,
                "manager": None,
            },
        }

    meta = get_protocol_metadata(result_data_or_state)
    protocol_version = version or meta["protocol_version"]

    inv_state = result_data_or_state.get("investment_debate_state")
    if not isinstance(inv_state, Mapping):
        inv_state = result_data_or_state

    # ── 1. Bull / Bear Verified Rates ──────────────────────────────────────────
    manager_verdict = (
        result_data_or_state.get("manager_verdict")
        or inv_state.get("manager_verdict")
        or {}
    )
    if not isinstance(manager_verdict, Mapping):
        manager_verdict = {}

    claim_evidence_summary = (
        manager_verdict.get("claim_evidence_summary")
        or inv_state.get("claim_evidence_summary")
        or {}
    )
    if not isinstance(claim_evidence_summary, Mapping):
        claim_evidence_summary = {}

    claims = inv_state.get("claims") or result_data_or_state.get("claims") or []
    if not isinstance(claims, list):
        claims = []

    bull_v, bull_t = 0, 0
    bear_v, bear_t = 0, 0

    if claim_evidence_summary:
        for _cid, info in claim_evidence_summary.items():
            if not isinstance(info, Mapping):
                continue
            sp_key = str(info.get("speaker_key") or info.get("speaker") or "")
            st_val = str(info.get("stance") or "")
            counts = info.get("counts") or {}
            v_cnt = int(counts.get("verified", 0))
            t_cnt = int(counts.get("total", 0))
            if _is_bull(sp_key, st_val):
                bull_v += v_cnt
                bull_t += t_cnt
            elif _is_bear(sp_key, st_val):
                bear_v += v_cnt
                bear_t += t_cnt
    elif claims:
        for c in claims:
            if not isinstance(c, Mapping):
                continue
            sp_key = str(c.get("speaker_key") or c.get("speaker") or "")
            st_val = str(c.get("stance") or "")
            is_v = bool(c.get("status") == "verified" or c.get("is_verified") is True)
            if _is_bull(sp_key, st_val):
                bull_t += 1
                if is_v:
                    bull_v += 1
            elif _is_bear(sp_key, st_val):
                bear_t += 1
                if is_v:
                    bear_v += 1

    bull_verified_rate = round(bull_v / bull_t, 4) if bull_t > 0 else None
    bear_verified_rate = round(bear_v / bear_t, 4) if bear_t > 0 else None

    # ── 2. Challenge Adoption Rates ───────────────────────────────────────────
    challenges = inv_state.get("challenges") or result_data_or_state.get("challenges") or []
    if not isinstance(challenges, list):
        challenges = []

    adopted_ids: set[str] = set()
    raw_adopted = manager_verdict.get("adopted_challenge_ids") or []
    if isinstance(raw_adopted, list):
        adopted_ids = {str(x).strip() for x in raw_adopted if str(x).strip()}

    bull_ch_tot, bull_ch_adopt = 0, 0
    bear_ch_tot, bear_ch_adopt = 0, 0

    for ch in challenges:
        if not isinstance(ch, Mapping):
            continue
        cid = str(ch.get("challenge_id") or "").strip()
        is_adopted = (cid in adopted_ids) or bool(ch.get("adopted") is True)
        sp_key = str(ch.get("speaker_key") or ch.get("speaker") or "")
        st_val = str(ch.get("stance") or "")
        if _is_bull(sp_key, st_val):
            bull_ch_tot += 1
            if is_adopted:
                bull_ch_adopt += 1
        elif _is_bear(sp_key, st_val):
            bear_ch_tot += 1
            if is_adopted:
                bear_ch_adopt += 1

    bull_challenge_adoption_rate = (
        round(bull_ch_adopt / bull_ch_tot, 4) if bull_ch_tot > 0 else None
    )
    bear_challenge_adoption_rate = (
        round(bear_ch_adopt / bear_ch_tot, 4) if bear_ch_tot > 0 else None
    )

    # ── 3. Analyst Utilization by Role ────────────────────────────────────────
    cited_tokens = _extract_cited_debate_numbers(inv_state)
    utilization_by_role: dict[str, Optional[float]] = {}

    for report_key, role_slug in REPORT_KEY_TO_ROLE.items():
        report_text = str(
            result_data_or_state.get(report_key, "")
            or inv_state.get(report_key, "")
            or ""
        )
        tokens = extract_numerical_tokens(report_text)
        denom = len(tokens)
        if denom == 0:
            utilization_by_role[role_slug] = None
        else:
            num = sum(1 for t in tokens if t in cited_tokens)
            utilization_by_role[role_slug] = round(num / denom, 4)

    # ── 4. Manager Evidence Coverage ──────────────────────────────────────────
    manager_evidence_coverage: Optional[float] = None
    if claim_evidence_summary:
        tot_v = sum(
            int(info.get("counts", {}).get("verified", 0))
            for info in claim_evidence_summary.values()
            if isinstance(info, Mapping)
        )
        tot_t = sum(
            int(info.get("counts", {}).get("total", 0))
            for info in claim_evidence_summary.values()
            if isinstance(info, Mapping)
        )
        if tot_t > 0:
            manager_evidence_coverage = round(tot_v / tot_t, 4)
    elif claims:
        tot_v = sum(
            1
            for c in claims
            if isinstance(c, Mapping) and (c.get("status") == "verified" or c.get("is_verified") is True)
        )
        tot_t = len(claims)
        if tot_t > 0:
            manager_evidence_coverage = round(tot_v / tot_t, 4)

    # ── 5. Manager Consistency Gate Triggered ─────────────────────────────────
    manager_consistency_gate_triggered: bool = False
    if manager_verdict:
        if (
            manager_verdict.get("consistency_check_passed") is False
            or bool(manager_verdict.get("failed_checks"))
        ):
            manager_consistency_gate_triggered = True
    elif inv_state.get("blocked") is True:
        block_reason = str(inv_state.get("block_reason", "")).lower()
        if "gate" in block_reason or "自洽" in block_reason or "硬闸" in block_reason:
            manager_consistency_gate_triggered = True

    # ── 6. T+5 Direction Hit ──────────────────────────────────────────────────
    # Unreached window or missing market data must be None (never False or 0).
    t_plus_5_direction_hit: Optional[bool] = None
    if t_plus_5_price is not None and isinstance(t_plus_5_price, (int, float)):
        # Optional forward evaluation if future price is explicitly supplied
        entry_val = None
        raw_entry = manager_verdict.get("entry") or result_data_or_state.get("target_price")
        if raw_entry:
            try:
                entry_val = float(str(raw_entry).split("-")[0].replace("元", "").strip())
            except (ValueError, TypeError):
                entry_val = None
        direction_str = str(manager_verdict.get("direction") or result_data_or_state.get("decision") or "").upper()
        if entry_val is not None and entry_val > 0:
            price_change = t_plus_5_price - entry_val
            if any(w in direction_str for w in ("BUY", "BULLISH", "多", "买入", "增持")):
                t_plus_5_direction_hit = bool(price_change > 0)
            elif any(w in direction_str for w in ("SELL", "BEARISH", "空", "卖出", "减持")):
                t_plus_5_direction_hit = bool(price_change < 0)
            elif any(w in direction_str for w in ("HOLD", "NEUTRAL", "中性", "观望", "持有")):
                t_plus_5_direction_hit = bool(abs(price_change / entry_val) <= 0.03)

    # ── 7. Model ID × Stance ──────────────────────────────────────────────────
    bull_model: Optional[str] = None
    bear_model: Optional[str] = None
    manager_model: Optional[str] = None

    round_messages = inv_state.get("round_messages") or result_data_or_state.get("round_messages") or []
    if isinstance(round_messages, list):
        for msg in round_messages:
            if not isinstance(msg, Mapping):
                continue
            m_name = msg.get("model_name") or msg.get("model_id") or msg.get("model")
            if m_name and isinstance(m_name, str) and m_name.strip():
                m_clean = m_name.strip()
                sp_key = str(msg.get("speaker_key") or msg.get("speaker") or "")
                st_val = str(msg.get("stance") or "")
                is_v = bool(msg.get("is_verdict") or "manager" in sp_key.lower() or "总监" in sp_key)
                if is_v:
                    if not manager_model:
                        manager_model = m_clean
                elif _is_bull(sp_key, st_val):
                    if not bull_model:
                        bull_model = m_clean
                elif _is_bear(sp_key, st_val):
                    if not bear_model:
                        bear_model = m_clean

    # Fallback to direct model mappings if provided
    role_models = (
        result_data_or_state.get("model_id_by_stance")
        or inv_state.get("model_id_by_stance")
        or result_data_or_state.get("role_models")
        or {}
    )
    if isinstance(role_models, Mapping):
        if not bull_model and role_models.get("bull"):
            bull_model = str(role_models["bull"]).strip()
        if not bear_model and role_models.get("bear"):
            bear_model = str(role_models["bear"]).strip()
        if not manager_model and role_models.get("manager"):
            manager_model = str(role_models["manager"]).strip()

    model_id_by_stance = {
        "bull": bull_model if (bull_model and bull_model != "unknown") else None,
        "bear": bear_model if (bear_model and bear_model != "unknown") else None,
        "manager": manager_model if (manager_model and manager_model != "unknown") else None,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "credit_weighting_enabled": False,
        "bull_verified_rate": bull_verified_rate,
        "bear_verified_rate": bear_verified_rate,
        "bull_challenge_adoption_rate": bull_challenge_adoption_rate,
        "bear_challenge_adoption_rate": bear_challenge_adoption_rate,
        "analyst_utilization_by_role": utilization_by_role,
        "manager_evidence_coverage": manager_evidence_coverage,
        "manager_consistency_gate_triggered": manager_consistency_gate_triggered,
        "t_plus_5_direction_hit": t_plus_5_direction_hit,
        "sample_count": 1,
        "protocol_version": protocol_version,
        "model_id_by_stance": model_id_by_stance,
    }
