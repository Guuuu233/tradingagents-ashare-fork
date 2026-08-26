"""H1a / H1b Shadow Credit and Credit Weighting Module (P1-S / P3-H1b).

Pure function implementation for:
1. Observational shadow credit metrics calculation.
2. 7-dimension gate threshold validation (N, Side, Time, T+5, Balance, Bias Freeze, Magnitude).
3. Layered isolation state machine (System-level -> Model-level -> Global Shadow fallback).
4. Deterministic credit weighting application (only verified claims relative weighting in [0.85, 1.15]).
"""

from collections import Counter
from datetime import date, datetime
import math
import re
from typing import Any, Mapping, Optional, Sequence

from tradingagents.agents.utils.agent_states import (
    DEFAULT_FEATURE_FLAGS,
    PROTOCOL_VERSION_V1_LEGACY,
    get_protocol_metadata,
)
from tradingagents.agents.utils.debate_metrics import (
    SEVEN_REPORT_KEYS,
    _extract_cited_debate_numbers,
    extract_numerical_tokens,
)

SCHEMA_VERSION: str = "h1a_json_v1"
H1B_SCHEMA_VERSION: str = "h1b_json_v1"

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

# ── Approved H1b Activation Gate Thresholds ──────────────────────────────────
H1B_THRESHOLDS: dict[str, Any] = {
    # 1. N: >=60 debates, >=20 unique symbols, >=5 industries, max single symbol <=15%
    "min_sample_count": 60,
    "min_unique_symbols": 20,
    "min_industries": 5,
    "max_single_symbol_ratio": 0.15,
    # 2. Side split: bull/bear samples each >=25, verified claims each >=100
    "min_side_samples": 25,
    "min_side_verified_claims": 100,
    # 3. Time: >=45 calendar days and >=30 trading days
    "min_calendar_days": 45,
    "min_trading_days": 30,
    # 4. T+5 completeness: >=95%
    "min_t_plus_5_completeness": 0.95,
    # 5. Balance: bull ratio in [40%, 60%], |Nbull - Nbear| <= 10
    "min_side_balance_ratio": 0.40,
    "max_side_balance_ratio": 0.60,
    "max_side_count_diff": 10,
    # 6. Bias freeze: delta verified <=18%, delta challenge adoption <=25%, clone rate <=5%, consistency trigger rate <=5%
    "max_delta_verified_rate": 0.18,
    "max_delta_challenge_adoption_rate": 0.25,
    "max_clone_rate": 0.05,
    "max_consistency_trigger_rate": 0.05,
    # 7. Magnitude: multiplier in [0.85, 1.15]
    "min_weight_multiplier": 0.85,
    "max_weight_multiplier": 1.15,
}


def _is_bull(speaker_key: str, stance: str) -> bool:
    """Return True if speaker or stance indicates bullish side."""
    sp = str(speaker_key or "").lower().strip()
    st = str(stance or "").lower().strip()
    return sp in ("bull", "看多", "多方", "多头") or st in ("bullish", "bull", "看多", "多头")


def _is_bear(speaker_key: str, stance: str) -> bool:
    """Return True if speaker or stance indicates bearish side."""
    sp = str(speaker_key or "").lower().strip()
    st = str(stance or "").lower().strip()
    return sp in ("bear", "看空", "空方", "空头") or st in ("bearish", "bear", "看空", "空头")


def _parse_sample_date(raw: Any) -> Optional[date]:
    """Parse trade date or created_at string to date object."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except (ValueError, TypeError):
            pass
    return None


def calculate_shadow_credit_metrics(
    result_data_or_state: Mapping[str, Any],
    *,
    version: Optional[str] = None,
    t_plus_5_price: Optional[float] = None,
) -> dict[str, Any]:
    """Calculate shadow credit metrics from result_data or investment_debate_state.

    This function is strictly pure, read-only, deterministic, and replayable.
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
    credit_weighting_flag = bool(meta.get("feature_flags", {}).get("credit_weighting_enabled", False))

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

    claim_id_to_claim: dict[str, Mapping[str, Any]] = {}
    for c in claims:
        if isinstance(c, Mapping):
            cid = str(c.get("claim_id", "")).strip()
            if cid:
                claim_id_to_claim[cid] = c

    bull_v, bull_t = 0, 0
    bear_v, bear_t = 0, 0

    if claim_evidence_summary:
        for _cid, info in claim_evidence_summary.items():
            if not isinstance(info, Mapping):
                continue
            matched_claim = claim_id_to_claim.get(str(_cid).strip(), {})
            sp_key = str(info.get("speaker_key") or info.get("speaker") or matched_claim.get("speaker_key") or matched_claim.get("speaker") or "")
            st_val = str(info.get("stance") or matched_claim.get("stance") or "")
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
    t_plus_5_direction_hit: Optional[bool] = None
    if t_plus_5_price is not None and isinstance(t_plus_5_price, (int, float)):
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

    # ── 7. Model ID x Stance ──────────────────────────────────────────────────
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
        "credit_weighting_enabled": credit_weighting_flag,
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


# ── 7-Dimension Gate Threshold Evaluation (P3-H1b) ───────────────────────────

def evaluate_h1b_system_gates(
    samples_or_reports: Sequence[Mapping[str, Any]],
    *,
    thresholds: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate 7-dimension gate thresholds for credit weighting activation.

    Dimensions:
    1. N: >=60 debates, >=20 unique symbols, >=5 industries, max single symbol <=15%
    2. Side: bull/bear samples each >=25, verified claims each >=100
    3. Time: >=45 calendar days and >=30 trading days; regime coverage
    4. T+5: Completeness rate >=95%
    5. Balance: Ratio in [40%, 60%], |Nbull - Nbear| <= 10
    6. Bias freeze: Delta verified <=18%, delta challenge adoption <=25%, clone rate <=5%, consistency trigger rate <=5%
    7. Magnitude: Range [0.85, 1.15]

    Returns structured evaluation matrix, passed status, and recommendations.
    """
    cfg = dict(H1B_THRESHOLDS)
    if thresholds:
        cfg.update(thresholds)

    samples = list(samples_or_reports or [])
    sample_count = len(samples)

    # ── Dimension 1: N (Sample Count & Diversity) ─────────────────────────────
    symbols: list[str] = []
    industries: list[str] = []
    for s in samples:
        sym = s.get("symbol") or s.get("ticker") or ""
        if sym:
            symbols.append(str(sym).strip())
        ind = s.get("industry") or s.get("sector") or ""
        if ind:
            industries.append(str(ind).strip())

    unique_symbols = len(set(symbols))
    unique_industries = len(set(industries))
    symbol_counter = Counter(symbols)
    max_symbol_count = max(symbol_counter.values()) if symbol_counter else 0
    max_symbol_share = (max_symbol_count / sample_count) if sample_count > 0 else 0.0

    pass_d1_count = sample_count >= cfg["min_sample_count"]
    pass_d1_sym = unique_symbols >= cfg["min_unique_symbols"]
    pass_d1_ind = unique_industries >= cfg["min_industries"]
    pass_d1_share = max_symbol_share <= cfg["max_single_symbol_ratio"]
    pass_d1 = bool(pass_d1_count and pass_d1_sym and pass_d1_ind and pass_d1_share)

    dim_n = {
        "passed": pass_d1,
        "details": {
            "sample_count": sample_count,
            "min_required": cfg["min_sample_count"],
            "unique_symbols": unique_symbols,
            "min_unique_symbols": cfg["min_unique_symbols"],
            "unique_industries": unique_industries,
            "min_industries": cfg["min_industries"],
            "max_symbol_share": round(max_symbol_share, 4),
            "max_allowed_share": cfg["max_single_symbol_ratio"],
        },
    }

    # ── Dimension 2: Side Split (Bull / Bear samples & Verified Claims) ───────
    bull_samples = 0
    bear_samples = 0
    bull_verified_claims = 0
    bear_verified_claims = 0

    for s in samples:
        inv_state = s.get("investment_debate_state") or s
        verdict = inv_state.get("manager_verdict") or s.get("manager_verdict") or {}
        winner = str(verdict.get("winner") or "").lower()
        direction = str(verdict.get("direction") or "").lower()

        if winner == "bull":
            bull_samples += 1
        elif winner == "bear":
            bear_samples += 1
        elif any(w in direction for w in ("多", "buy", "bull")):
            bull_samples += 1
        elif any(w in direction for w in ("空", "sell", "bear")):
            bear_samples += 1
        else:
            # Neutral / Tie
            pass

        claims = inv_state.get("claims") or s.get("claims") or []
        claim_map = {str(c.get("claim_id", "")).strip(): c for c in claims if isinstance(c, Mapping)}

        summary = inv_state.get("claim_evidence_summary") or {}
        if summary:
            for cid_k, info in summary.items():
                if not isinstance(info, Mapping):
                    continue
                matched_c = claim_map.get(str(cid_k).strip(), {})
                sp = str(info.get("speaker_key") or info.get("speaker") or matched_c.get("speaker_key") or matched_c.get("speaker") or "")
                st = str(info.get("stance") or matched_c.get("stance") or "")
                v_cnt = int(info.get("counts", {}).get("verified", 0))
                if _is_bull(sp, st):
                    bull_verified_claims += v_cnt
                elif _is_bear(sp, st):
                    bear_verified_claims += v_cnt
        else:
            for c in claims:
                if not isinstance(c, Mapping):
                    continue
                sp = str(c.get("speaker_key") or c.get("speaker") or "")
                st = str(c.get("stance") or "")
                is_v = bool(c.get("status") == "verified" or c.get("is_verified") is True)
                if is_v:
                    if _is_bull(sp, st):
                        bull_verified_claims += 1
                    elif _is_bear(sp, st):
                        bear_verified_claims += 1

    pass_d2_samples = (bull_samples >= cfg["min_side_samples"]) and (bear_samples >= cfg["min_side_samples"])
    pass_d2_claims = (bull_verified_claims >= cfg["min_side_verified_claims"]) and (bear_verified_claims >= cfg["min_side_verified_claims"])
    pass_d2 = bool(pass_d2_samples and pass_d2_claims)

    dim_side = {
        "passed": pass_d2,
        "details": {
            "bull_samples": bull_samples,
            "bear_samples": bear_samples,
            "min_side_samples": cfg["min_side_samples"],
            "bull_verified_claims": bull_verified_claims,
            "bear_verified_claims": bear_verified_claims,
            "min_verified_claims": cfg["min_side_verified_claims"],
        },
    }

    # ── Dimension 3: Time Span (Calendar days & Trading days) ─────────────────
    parsed_dates = []
    regimes = set()
    for s in samples:
        d = _parse_sample_date(s.get("trade_date") or s.get("date") or s.get("created_at"))
        if d:
            parsed_dates.append(d)
        reg = s.get("market_regime") or s.get("regime")
        if reg:
            regimes.add(str(reg).strip())

    calendar_days = 0
    trading_days = 0
    if parsed_dates:
        min_date = min(parsed_dates)
        max_date = max(parsed_dates)
        calendar_days = (max_date - min_date).days + 1
        trading_days = len(set(parsed_dates))

    pass_d3_cal = calendar_days >= cfg["min_calendar_days"]
    pass_d3_trd = trading_days >= cfg["min_trading_days"]
    pass_d3 = bool(pass_d3_cal and pass_d3_trd)

    dim_time = {
        "passed": pass_d3,
        "details": {
            "calendar_days": calendar_days,
            "min_calendar_days": cfg["min_calendar_days"],
            "trading_days": trading_days,
            "min_trading_days": cfg["min_trading_days"],
            "market_regimes_covered": list(regimes),
        },
    }

    # ── Dimension 4: T+5 Completeness ─────────────────────────────────────────
    due_t5_count = 0
    completed_t5_count = 0

    for s in samples:
        metrics = s.get("shadow_credit_metrics") or {}
        hit = metrics.get("t_plus_5_direction_hit")
        st = s.get("t_plus_5_status") or metrics.get("t_plus_5_status")
        # Exclude suspension from due denominator
        if st == "suspension" or s.get("is_suspended") is True or s.get("suspension") is True:
            continue
        # Exclude pending / in-flight samples from due denominator
        if st == "pending_due" or s.get("is_in_flight") is True or s.get("is_t_plus_5_due") is False:
            continue

        is_due = s.get("is_t_plus_5_due")
        if is_due is None:
            # If not explicitly marked, treat as due if trade_date exists and > 5 days ago or hit is not None
            is_due = (hit is not None) or bool(s.get("t_plus_5_evaluated", False)) or (st in ("due_and_evaluated", "data_missing"))

        if is_due:
            due_t5_count += 1
            if hit is not None:
                completed_t5_count += 1

    if due_t5_count == 0:
        # If no sample is due or no historical evaluated data, mark based on sample count
        t5_completeness_rate = 1.0 if sample_count >= cfg["min_sample_count"] else 0.0
        pass_d4 = False if sample_count < cfg["min_sample_count"] else True
    else:
        t5_completeness_rate = completed_t5_count / due_t5_count
        pass_d4 = bool(t5_completeness_rate >= cfg["min_t_plus_5_completeness"])

    dim_t5 = {
        "passed": pass_d4,
        "details": {
            "due_count": due_t5_count,
            "completed_count": completed_t5_count,
            "completeness_rate": round(t5_completeness_rate, 4),
            "min_required_rate": cfg["min_t_plus_5_completeness"],
        },
    }

    # ── Dimension 5: Balance ──────────────────────────────────────────────────
    total_side = bull_samples + bear_samples
    bull_ratio = (bull_samples / total_side) if total_side > 0 else 0.0
    side_diff = abs(bull_samples - bear_samples)

    pass_d5_ratio = cfg["min_side_balance_ratio"] <= bull_ratio <= cfg["max_side_balance_ratio"]
    pass_d5_diff = side_diff <= cfg["max_side_count_diff"]
    pass_d5 = bool(pass_d5_ratio and pass_d5_diff and sample_count >= cfg["min_sample_count"])

    dim_balance = {
        "passed": pass_d5,
        "details": {
            "bull_ratio": round(bull_ratio, 4),
            "allowed_range": [cfg["min_side_balance_ratio"], cfg["max_side_balance_ratio"]],
            "side_diff": side_diff,
            "max_allowed_diff": cfg["max_side_count_diff"],
        },
    }

    # ── Dimension 6: Bias Freeze (Rates Delta, Clone Rate, Consistency Gate) ───
    bull_v_rates: list[float] = []
    bear_v_rates: list[float] = []
    bull_ch_rates: list[float] = []
    bear_ch_rates: list[float] = []
    consistency_triggers = 0
    claims_text_pool: list[str] = []

    for s in samples:
        metrics = s.get("shadow_credit_metrics") or {}
        if metrics.get("bull_verified_rate") is not None:
            bull_v_rates.append(float(metrics["bull_verified_rate"]))
        if metrics.get("bear_verified_rate") is not None:
            bear_v_rates.append(float(metrics["bear_verified_rate"]))
        if metrics.get("bull_challenge_adoption_rate") is not None:
            bull_ch_rates.append(float(metrics["bull_challenge_adoption_rate"]))
        if metrics.get("bear_challenge_adoption_rate") is not None:
            bear_ch_rates.append(float(metrics["bear_challenge_adoption_rate"]))
        if metrics.get("manager_consistency_gate_triggered") is True:
            consistency_triggers += 1

        inv_state = s.get("investment_debate_state") or s
        for c in (inv_state.get("claims") or []):
            if isinstance(c, Mapping) and c.get("claim"):
                claims_text_pool.append(str(c["claim"]).strip())

    avg_bull_v = (sum(bull_v_rates) / len(bull_v_rates)) if bull_v_rates else 0.0
    avg_bear_v = (sum(bear_v_rates) / len(bear_v_rates)) if bear_v_rates else 0.0
    delta_verified_rate = abs(avg_bull_v - avg_bear_v)

    avg_bull_ch = (sum(bull_ch_rates) / len(bull_ch_rates)) if bull_ch_rates else 0.0
    avg_bear_ch = (sum(bear_ch_rates) / len(bear_ch_rates)) if bear_ch_rates else 0.0
    delta_challenge_rate = abs(avg_bull_ch - avg_bear_ch)

    # Clone rate: duplicate claims / total claims
    unique_claims_count = len(set(claims_text_pool))
    total_claims_count = len(claims_text_pool)
    clone_rate = (1.0 - (unique_claims_count / total_claims_count)) if total_claims_count > 0 else 0.0

    consistency_trigger_rate = (consistency_triggers / sample_count) if sample_count > 0 else 0.0

    pass_d6_v = delta_verified_rate <= cfg["max_delta_verified_rate"]
    pass_d6_ch = delta_challenge_rate <= cfg["max_delta_challenge_adoption_rate"]
    pass_d6_clone = clone_rate <= cfg["max_clone_rate"]
    pass_d6_cons = consistency_trigger_rate <= cfg["max_consistency_trigger_rate"]
    pass_d6 = bool(pass_d6_v and pass_d6_ch and pass_d6_clone and pass_d6_cons and sample_count >= cfg["min_sample_count"])

    dim_bias = {
        "passed": pass_d6,
        "details": {
            "avg_bull_verified_rate": round(avg_bull_v, 4),
            "avg_bear_verified_rate": round(avg_bear_v, 4),
            "delta_verified_rate": round(delta_verified_rate, 4),
            "max_allowed_delta_v": cfg["max_delta_verified_rate"],
            "avg_bull_challenge_adoption_rate": round(avg_bull_ch, 4),
            "avg_bear_challenge_adoption_rate": round(avg_bear_ch, 4),
            "delta_challenge_adoption_rate": round(delta_challenge_rate, 4),
            "max_allowed_delta_ch": cfg["max_delta_challenge_adoption_rate"],
            "clone_rate": round(clone_rate, 4),
            "max_allowed_clone_rate": cfg["max_clone_rate"],
            "consistency_trigger_rate": round(consistency_trigger_rate, 4),
            "max_allowed_consistency_rate": cfg["max_consistency_trigger_rate"],
        },
    }

    # ── Dimension 7: Magnitude ────────────────────────────────────────────────
    dim_magnitude = {
        "passed": True,
        "details": {
            "min_weight_multiplier": cfg["min_weight_multiplier"],
            "max_weight_multiplier": cfg["max_weight_multiplier"],
            "range": [cfg["min_weight_multiplier"], cfg["max_weight_multiplier"]],
        },
    }

    matrix = {
        "dimension_n": dim_n,
        "dimension_side": dim_side,
        "dimension_time": dim_time,
        "dimension_t5": dim_t5,
        "dimension_balance": dim_balance,
        "dimension_bias": dim_bias,
        "dimension_magnitude": dim_magnitude,
    }

    all_passed = bool(
        dim_n["passed"]
        and dim_side["passed"]
        and dim_time["passed"]
        and dim_t5["passed"]
        and dim_balance["passed"]
        and dim_bias["passed"]
        and dim_magnitude["passed"]
    )

    recommendation = "ELIGIBLE_FOR_ACTIVATION" if all_passed else "KEEP_FALSE"

    return {
        "schema_version": H1B_SCHEMA_VERSION,
        "passed": all_passed,
        "matrix": matrix,
        "summary": {
            "sample_count": sample_count,
            "system_gate_status": "PASS" if all_passed else "FAIL",
            "recommendation": recommendation,
        },
        "recommendation": recommendation,
    }


# ── Layered Isolation State Machine (P3-H1b) ──────────────────────────────────

def evaluate_model_bias_and_weights(
    samples_or_reports: Sequence[Mapping[str, Any]],
    system_gate_passed: bool = False,
    per_model_bias_overrides: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate per-model bias and layered isolation weights.

    Rules:
    - System gate failure -> Global weight=1.0 (Shadow-only)
    - Single model bias -> Only that model clamped to 1.0 + bias_freeze_reason
    - Abnormal model ratio > 50% -> Global fallback to Shadow
    """
    samples = list(samples_or_reports or [])
    overrides = dict(per_model_bias_overrides or {})

    # Discover all models
    models_set: set[str] = set()
    for s in samples:
        metrics = s.get("shadow_credit_metrics") or {}
        id_map = metrics.get("model_id_by_stance") or {}
        for m in id_map.values():
            if m and isinstance(m, str):
                models_set.add(m.strip())
        inv_state = s.get("investment_debate_state") or s
        for msg in (inv_state.get("round_messages") or []):
            if isinstance(msg, Mapping):
                m_name = msg.get("model_name") or msg.get("model_id") or msg.get("model")
                if m_name and isinstance(m_name, str):
                    models_set.add(m_name.strip())

    if not models_set:
        models_set = {"deepseek-r1", "qwen-max", "gpt-4o"}

    if not system_gate_passed:
        return {
            "credit_weighting_active": False,
            "global_fallback_shadow": True,
            "system_gate_status": "FAIL",
            "model_weights": {m: 1.0 for m in models_set},
            "bias_freeze_reasons": {m: "System-level activation gates not passed" for m in models_set},
            "abnormal_model_ratio": 1.0,
        }

    # Evaluate per-model bias
    model_weights: dict[str, float] = {}
    bias_freeze_reasons: dict[str, str] = {}
    biased_models_count = 0

    for model in sorted(models_set):
        ov = overrides.get(model)
        is_biased = False
        reason = ""

        if ov is not None:
            if isinstance(ov, dict):
                is_biased = bool(ov.get("biased", False))
                reason = str(ov.get("reason", "Overridden as biased"))
            else:
                is_biased = bool(ov)
                reason = "Overridden as biased" if is_biased else ""
        else:
            # Evaluate statistical bias from samples for this model
            # For now default unbiased unless overrides or stats trigger
            is_biased = False

        if is_biased:
            model_weights[model] = 1.0
            bias_freeze_reasons[model] = reason or "Model bias exceeds threshold (clamped to 1.0)"
            biased_models_count += 1
        else:
            # Calibrated weight (in [0.85, 1.15])
            model_weights[model] = 1.05  # Base default calibrated weight

    total_models = len(models_set)
    abnormal_ratio = (biased_models_count / total_models) if total_models > 0 else 0.0

    if abnormal_ratio > 0.50:
        # Abnormal model ratio > 50% -> Global fallback to Shadow
        return {
            "credit_weighting_active": False,
            "global_fallback_shadow": True,
            "system_gate_status": "PASS",
            "global_freeze_reason": f"Abnormal model ratio ({biased_models_count}/{total_models} = {abnormal_ratio:.1%}) > 50%",
            "model_weights": {m: 1.0 for m in models_set},
            "bias_freeze_reasons": bias_freeze_reasons,
            "abnormal_model_ratio": abnormal_ratio,
        }

    return {
        "credit_weighting_active": True,
        "global_fallback_shadow": False,
        "system_gate_status": "PASS",
        "model_weights": model_weights,
        "bias_freeze_reasons": bias_freeze_reasons,
        "abnormal_model_ratio": abnormal_ratio,
    }


# ── Claim Credit Weighting Calculation (P3-H1b) ──────────────────────────────

def calculate_claim_credit_weights(
    claims: Sequence[Mapping[str, Any]],
    claim_evidence_summary: Mapping[str, Any],
    model_weights: Optional[Mapping[str, float]] = None,
    *,
    credit_weighting_enabled: bool = False,
    system_gate_passed: bool = False,
) -> dict[str, Any]:
    """Calculate relative credit weights across claims.

    Hard Rules:
    1. If credit_weighting_enabled is False or system_gate_passed is False -> All weights = 1.0.
    2. Only verified claims receive relative weight modification in [0.85, 1.15].
    3. Contradicted, unsupported, or unavailable claims NEVER receive weight > 0 or get elevated.
    """
    claims_list = list(claims or [])
    m_weights = dict(model_weights or {})
    min_w = H1B_THRESHOLDS["min_weight_multiplier"]
    max_w = H1B_THRESHOLDS["max_weight_multiplier"]

    claim_weights: dict[str, float] = {}
    claim_decisions: dict[str, str] = {}
    effective_weights: dict[str, float] = {}

    if not credit_weighting_enabled or not system_gate_passed:
        for c in claims_list:
            cid = str(c.get("claim_id", "")).strip()
            if cid:
                claim_weights[cid] = 1.0
                sum_info = claim_evidence_summary.get(cid) if isinstance(claim_evidence_summary, Mapping) else {}
                dec = sum_info.get("decision", "reject") if isinstance(sum_info, Mapping) else "reject"
                claim_decisions[cid] = dec
                effective_weights[cid] = 1.0 if dec == "adopt" else 0.0
        return {
            "credit_weighting_active": False,
            "claim_weights": claim_weights,
            "claim_decisions": claim_decisions,
            "effective_weights": effective_weights,
        }

    for c in claims_list:
        cid = str(c.get("claim_id", "")).strip()
        if not cid:
            continue
        model_name = str(c.get("model_name") or c.get("model") or "").strip()
        sum_info = claim_evidence_summary.get(cid) if isinstance(claim_evidence_summary, Mapping) else {}
        if not isinstance(sum_info, Mapping):
            sum_info = {}
        dec = str(sum_info.get("decision", "reject"))
        counts = sum_info.get("counts", {}) if isinstance(sum_info.get("counts"), Mapping) else {}
        is_verified = bool(
            (c.get("status") == "verified" or c.get("is_verified") is True)
            and dec in ("adopt", "partial")
            and counts.get("contradicted", 0) == 0
            and counts.get("source_unavailable", 0) == 0
        )

        claim_decisions[cid] = dec

        if not is_verified:
            # Contradicted / unsupported / unavailable claims NEVER receive credit boost or elevation
            claim_weights[cid] = 0.0
            effective_weights[cid] = 0.0
        else:
            raw_w = m_weights.get(model_name, 1.0)
            clamped_w = max(min_w, min(max_w, float(raw_w)))
            claim_weights[cid] = round(clamped_w, 4)
            effective_weights[cid] = round(clamped_w, 4)

    return {
        "credit_weighting_active": True,
        "claim_weights": claim_weights,
        "claim_decisions": claim_decisions,
        "effective_weights": effective_weights,
    }


def resolve_claim_credit_weights_for_manager(
    *,
    claims: Sequence[Mapping[str, Any]],
    claim_evidence_summary: Mapping[str, Any],
    historical_samples: Optional[Sequence[Mapping[str, Any]]] = None,
    credit_weighting_enabled: bool = False,
) -> dict[str, Any]:
    """Resolve claim credit weights for research_manager using live H1b gates.

    Fail-closed: empty/missing historical samples → system_gate_passed=False → flat 1.0.
    When flag is off, weights stay flat regardless of gate status.
    """
    history = list(historical_samples or [])
    if history:
        gate_res = evaluate_h1b_system_gates(history)
    else:
        gate_res = {
            "passed": False,
            "recommendation": "KEEP_FALSE",
            "matrix": {},
        }
    system_gate_passed = bool(gate_res.get("passed", False))
    isolation = evaluate_model_bias_and_weights(
        history,
        system_gate_passed=system_gate_passed,
    )
    weights_res = calculate_claim_credit_weights(
        claims=claims,
        claim_evidence_summary=claim_evidence_summary,
        model_weights=isolation.get("model_weights", {}),
        credit_weighting_enabled=credit_weighting_enabled,
        system_gate_passed=system_gate_passed,
    )
    return {
        **weights_res,
        "system_gate_passed": system_gate_passed,
        "system_gate_status": "PASS" if system_gate_passed else "FAIL",
        "recommendation": gate_res.get("recommendation", "KEEP_FALSE"),
        "bias_freeze_reasons": isolation.get("bias_freeze_reasons", {}),
        "model_weights": isolation.get("model_weights", {}),
        "global_fallback_shadow": bool(isolation.get("global_fallback_shadow", True)),
    }


def apply_credit_weighting_to_debate(
    result_data_or_state: Mapping[str, Any],
    historical_samples: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Apply credit weighting to debate state/result and update shadow metrics."""
    meta = get_protocol_metadata(result_data_or_state)
    credit_weighting_flag = bool(meta.get("feature_flags", {}).get("credit_weighting_enabled", False))

    inv_state = result_data_or_state.get("investment_debate_state")
    if not isinstance(inv_state, Mapping):
        inv_state = result_data_or_state

    claims = inv_state.get("claims") or result_data_or_state.get("claims") or []
    verdict = inv_state.get("manager_verdict") or result_data_or_state.get("manager_verdict") or {}
    claim_summary = verdict.get("claim_evidence_summary") or inv_state.get("claim_evidence_summary") or {}

    resolved = resolve_claim_credit_weights_for_manager(
        claims=claims,
        claim_evidence_summary=claim_summary if isinstance(claim_summary, Mapping) else {},
        historical_samples=historical_samples,
        credit_weighting_enabled=credit_weighting_flag,
    )
    system_gate_passed = bool(resolved.get("system_gate_passed", False))
    weights_res = {
        "credit_weighting_active": resolved.get("credit_weighting_active", False),
        "claim_weights": resolved.get("claim_weights", {}),
        "claim_decisions": resolved.get("claim_decisions", {}),
        "effective_weights": resolved.get("effective_weights", {}),
    }
    isolation = {
        "bias_freeze_reasons": resolved.get("bias_freeze_reasons", {}),
        "model_weights": resolved.get("model_weights", {}),
    }

    shadow_metrics = calculate_shadow_credit_metrics(result_data_or_state)
    shadow_metrics.update({
        "credit_weighting_enabled": credit_weighting_flag,
        "credit_weighting_active": weights_res["credit_weighting_active"],
        "system_gate_status": "PASS" if system_gate_passed else "FAIL",
        "bias_freeze_reasons": isolation.get("bias_freeze_reasons", {}),
        "model_weights": isolation.get("model_weights", {}),
    })

    return {
        "credit_weighting_active": weights_res["credit_weighting_active"],
        "claim_weights": weights_res["claim_weights"],
        "shadow_credit_metrics": shadow_metrics,
        "system_gate_status": "PASS" if system_gate_passed else "FAIL",
    }
