import logging
from typing import Any, Mapping, Sequence, Optional
from tradingagents.agents.utils.context_utils import get_cn_stock_name, format_phase1_reports
import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.graph.intent_parser import (
    build_horizon_context,
    get_bound_research_horizon,
)
from tradingagents.agents.utils.agent_states import (
    current_tracker_var,
    extract_verdict,
    check_llm_output_degraded,
    check_stream_chunk_degraded,
)
from tradingagents.agents.utils.knowledge_context import (
    resolve_industry_context,
    resolve_macro_event_context,
)
from tradingagents.dataflows.news_event_evidence import (
    build_news_event_coverage,
    format_event_coverage_summary,
    parse_news_markdown_to_evidences,
)
from api.database import log_llm_call

# ── E-04: Expectation Revision Contract Constants & Validation ──────────────────
STATUS_AVAILABLE = "available"
STATUS_PARTIAL = "partial"
STATUS_GAP = "gap"
STATUS_NOT_APPLICABLE = "not_applicable"
VALID_STATUSES = {STATUS_AVAILABLE, STATUS_PARTIAL, STATUS_GAP, STATUS_NOT_APPLICABLE}

EVENT_TYPE_FUNDAMENTAL = "fundamental"
EVENT_TYPE_EVENT = "event"
EVENT_TYPE_NONE = "none"
VALID_EVENT_TYPES = {EVENT_TYPE_FUNDAMENTAL, EVENT_TYPE_EVENT, EVENT_TYPE_NONE}

BASELINE_MANAGEMENT_GUIDANCE = "management_guidance"
BASELINE_CONSENSUS_EXPECTATION = "consensus_expectation"
BASELINE_PRIOR_SELF_FORECAST = "prior_self_forecast"
BASELINE_IMPLICIT_MODEL = "implicit_model"
BASELINE_NONE = "none"
VALID_BASELINE_TYPES = {
    BASELINE_MANAGEMENT_GUIDANCE,
    BASELINE_CONSENSUS_EXPECTATION,
    BASELINE_PRIOR_SELF_FORECAST,
    BASELINE_IMPLICIT_MODEL,
    BASELINE_NONE,
}

REVISION_NUMERIC = "numeric"
REVISION_QUALITATIVE = "qualitative"
REVISION_GAP = "gap"
VALID_REVISION_TYPES = {REVISION_NUMERIC, REVISION_QUALITATIVE, REVISION_GAP}

PRICED_IN_SUPPORTED = "supported"
PRICED_IN_NOT_SUPPORTED = "not_supported"
PRICED_IN_UNKNOWN = "unknown"
VALID_PRICED_IN_STATUSES = {PRICED_IN_SUPPORTED, PRICED_IN_NOT_SUPPORTED, PRICED_IN_UNKNOWN}

DOUBLE_COUNT_ACCOUNTED_FOR = "accounted_for"
DOUBLE_COUNT_NOT_ACCOUNTED_FOR = "not_accounted_for"
DOUBLE_COUNT_UNKNOWN = "unknown"
VALID_DOUBLE_COUNT_STATUSES = {
    DOUBLE_COUNT_ACCOUNTED_FOR,
    DOUBLE_COUNT_NOT_ACCOUNTED_FOR,
    DOUBLE_COUNT_UNKNOWN,
}

CONTENT_QUALIFIED = "qualified"
CONTENT_HASHED = "hashed"
CONTENT_UNAVAILABLE = "unavailable"
CONTENT_NOT_ATTEMPTED = "not_attempted"
CONTENT_NOT_OBTAINED = "not_obtained"


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


def make_json_safe(obj: Any) -> Any:
    """Ensure data is strictly JSON-safe without custom objects or NaN/Infinity."""
    from typing import Mapping
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        import math
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (dict, Mapping)):
        return {str(k): make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_json_safe(x) for x in obj]
    return str(obj)


def make_default_expectation_revision(
    event_type: str = EVENT_TYPE_NONE,
    status: str = STATUS_NOT_APPLICABLE,
) -> dict[str, Any]:
    return {
        "status": status,
        "event_type": event_type,
        "publication": {
            "publish_time": None,
            "published_at": None,
            "source": None,
            "source_hash": None,
            "content_qualification": CONTENT_NOT_ATTEMPTED,
            "qualification_status": CONTENT_NOT_ATTEMPTED,
        },
        "actual": {
            "status": STATUS_GAP,
            "metric": None,
            "value": None,
            "unit": None,
            "report_period": None,
            "as_of": None,
            "source": None,
            "reason": "未获取到实际结构化数据",
        },
        "baseline": {
            "type": BASELINE_NONE,
            "source": None,
            "metric": None,
            "value": None,
            "unit": None,
            "period": None,
            "report_period": None,
            "as_of": None,
        },
        "revision": {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "unknown",
            "detail": "无可用基线或实际数据",
        },
        "priced_in": {
            "status": PRICED_IN_UNKNOWN,
            "evidence": None,
        },
        "double_count_guard": {
            "status": DOUBLE_COUNT_UNKNOWN,
            "prevent_double_voting": True,
            "description": "无计入状态证据，保持 unknown 且阻止再次加票",
        },
        "gaps": [],
    }


def validate_expectation_revision(
    er: Any,
    cutoff_date: str | None = None,
) -> tuple[bool, list[str]]:
    """Strict deterministic validation of expectation_revision contract (E-04).

    Returns:
        (is_valid, violations)
    """
    violations: list[str] = []
    if not isinstance(er, dict):
        return False, ["expectation_revision must be a dictionary"]

    for key in (
        "status",
        "event_type",
        "publication",
        "actual",
        "baseline",
        "revision",
        "priced_in",
        "double_count_guard",
        "gaps",
    ):
        if key not in er:
            violations.append(f"missing top-level key: {key}")

    if violations:
        return False, violations

    # 1. status
    status = er.get("status")
    if status not in VALID_STATUSES:
        violations.append(f"invalid status: {status!r} (must be one of {VALID_STATUSES})")

    # 2. event_type
    event_type = er.get("event_type")
    if event_type not in VALID_EVENT_TYPES:
        violations.append(f"invalid event_type: {event_type!r} (must be one of {VALID_EVENT_TYPES})")

    # 3. publication
    pub = er.get("publication")
    if not isinstance(pub, dict):
        violations.append("publication must be a dictionary")
    else:
        pub_time = pub.get("publish_time") or pub.get("published_at")
        content_qual = pub.get("content_qualification") or pub.get("qualification_status") or CONTENT_NOT_ATTEMPTED
        content_qual = str(content_qual).strip().lower()

        # Check future date
        if pub_time and cutoff_date:
            try:
                pub_date_str = str(pub_time)[:10]
                cut_date_str = str(cutoff_date)[:10]
                if pub_date_str > cut_date_str:
                    if "future_date" not in er.get("gaps", []):
                        violations.append(
                            f"publication date ({pub_time}) is later than cutoff ({cutoff_date}), must be recorded in gaps"
                        )
            except Exception:
                pass

        # Case 1 & 2: if text not obtained, actual MUST NOT have numeric values
        text_not_obtained = content_qual in (
            CONTENT_HASHED,
            CONTENT_UNAVAILABLE,
            CONTENT_NOT_ATTEMPTED,
            CONTENT_NOT_OBTAINED,
        )
        if text_not_obtained:
            actual = er.get("actual") or {}
            if isinstance(actual, dict) and actual.get("value") is not None:
                violations.append(
                    f"content_qualification is {content_qual!r} (full text not obtained); actual.value must be None"
                )

    # 4. actual
    actual = er.get("actual")
    if actual is not None and not isinstance(actual, dict):
        violations.append("actual must be a dictionary or None")
    elif isinstance(actual, dict):
        act_status = actual.get("status")
        act_val = actual.get("value")
        # Check all 5 required elements when value is present
        if act_val is not None:
            missing_elements = []
            for field in ("metric", "unit", "report_period", "as_of"):
                if not actual.get(field):
                    missing_elements.append(field)
            if missing_elements:
                violations.append(
                    f"actual has value {act_val} but is missing required structured elements: {missing_elements}"
                )
            if act_status != STATUS_AVAILABLE:
                violations.append(f"actual has value but status is {act_status!r} (must be 'available')")
        else:
            if act_status == STATUS_AVAILABLE:
                violations.append("actual status is 'available' but value is None")

    # 5. baseline
    baseline = er.get("baseline")
    if not isinstance(baseline, dict):
        violations.append("baseline must be a dictionary")
    else:
        b_type = baseline.get("type")
        if b_type not in VALID_BASELINE_TYPES:
            violations.append(f"invalid baseline type: {b_type!r} (must be one of {VALID_BASELINE_TYPES})")

        # Case 5: 4 baseline types must have source; missing source cannot masquerade
        if b_type != BASELINE_NONE:
            b_source = baseline.get("source")
            if not b_source or not str(b_source).strip():
                violations.append(
                    f"baseline type is {b_type!r} but source is missing or empty; cannot masquerade without source"
                )
        else:
            # Case 4: if b_type == none, value must be None
            if baseline.get("value") is not None:
                violations.append("baseline type is 'none', so baseline.value must be None")

    # 6. revision
    revision = er.get("revision")
    if not isinstance(revision, dict):
        violations.append("revision must be a dictionary")
    else:
        rev_type = revision.get("type")
        if rev_type not in VALID_REVISION_TYPES:
            violations.append(f"invalid revision type: {rev_type!r} (must be one of {VALID_REVISION_TYPES})")

        b_type = (er.get("baseline") or {}).get("type")
        # Case 4: No old baseline -> revision cannot be numeric, value and percent must be None
        if b_type == BASELINE_NONE:
            if rev_type == REVISION_NUMERIC:
                violations.append("baseline type is 'none'; revision cannot be 'numeric'")
            if revision.get("value") is not None:
                violations.append("baseline type is 'none'; revision.value must be None")
            if revision.get("percent") is not None:
                violations.append("baseline type is 'none'; revision.percent must be None")

        # Case 6: if revision is numeric, actual and baseline must be comparable in metric, unit, report_period, as_of
        if rev_type == REVISION_NUMERIC:
            actual = er.get("actual") or {}
            baseline = er.get("baseline") or {}
            if actual.get("value") is None or actual.get("status") != STATUS_AVAILABLE:
                violations.append("revision is 'numeric' but actual.value is missing or actual.status != 'available'")
            if baseline.get("value") is None or baseline.get("type") == BASELINE_NONE:
                violations.append("revision is 'numeric' but baseline.value is missing or baseline.type == 'none'")
            # Unit check
            act_unit = str(actual.get("unit") or "").strip()
            base_unit = str(baseline.get("unit") or "").strip()
            if act_unit != base_unit:
                violations.append(f"revision is 'numeric' but unit mismatch: actual unit {act_unit!r} vs baseline unit {base_unit!r}")
            # Period check (Case 3: different periods cannot substitute)
            act_period = str(actual.get("report_period") or "").strip()
            base_period = str(baseline.get("period") or baseline.get("report_period") or "").strip()
            if act_period != base_period:
                violations.append(
                    f"revision is 'numeric' but report period mismatch: actual {act_period!r} vs baseline {base_period!r}"
                )
            # Metric check
            act_metric = str(actual.get("metric") or "").strip()
            base_metric = str(baseline.get("metric") or "").strip()
            if act_metric != base_metric:
                violations.append(f"revision is 'numeric' but metric mismatch: actual {act_metric!r} vs baseline {base_metric!r}")

        # Case 7: Beat / Miss ("超预期" / "不及预期") requires comparable baseline
        rev_direction = str(revision.get("direction") or "").strip().lower()
        rev_detail = str(revision.get("detail") or "")
        is_beat_or_miss = (
            rev_direction in ("positive", "negative", "beat", "miss", "超预期", "不及预期")
            or "超预期" in rev_detail
            or "不及预期" in rev_detail
        )
        if is_beat_or_miss and (b_type == BASELINE_NONE or (er.get("baseline") or {}).get("value") is None):
            violations.append("cannot assert beat/miss ('超预期'/'不及预期') without a valid, comparable baseline")

    # 7. priced_in
    priced_in = er.get("priced_in")
    if not isinstance(priced_in, dict):
        violations.append("priced_in must be a dictionary")
    else:
        p_status = priced_in.get("status")
        if p_status not in VALID_PRICED_IN_STATUSES:
            violations.append(f"invalid priced_in status: {p_status!r} (must be one of {VALID_PRICED_IN_STATUSES})")
        # Case 8: priced_in supported/not_supported requires traceable evidence
        if p_status in (PRICED_IN_SUPPORTED, PRICED_IN_NOT_SUPPORTED):
            p_evidence = priced_in.get("evidence")
            if not p_evidence or not str(p_evidence).strip():
                violations.append(
                    f"priced_in status is {p_status!r} but evidence is missing; must be 'unknown' without traceable evidence"
                )

    # 8. double_count_guard
    dcg = er.get("double_count_guard")
    if not isinstance(dcg, dict):
        violations.append("double_count_guard must be a dictionary")
    else:
        dc_status = dcg.get("status")
        if dc_status not in VALID_DOUBLE_COUNT_STATUSES:
            violations.append(f"invalid double_count_guard status: {dc_status!r} (must be one of {VALID_DOUBLE_COUNT_STATUSES})")
        # Case 9: if accounted_for or unknown -> prevent_double_voting must be True
        if dc_status in (DOUBLE_COUNT_ACCOUNTED_FOR, DOUBLE_COUNT_UNKNOWN):
            if not dcg.get("prevent_double_voting"):
                violations.append(f"double_count_guard status is {dc_status!r}; prevent_double_voting must be True")

    # 9. gaps
    gaps = er.get("gaps")
    if not isinstance(gaps, list):
        violations.append("gaps must be a list of strings")

    return len(violations) == 0, violations


def build_news_expectation_revision(
    event_coverage: dict[str, Any] | None,
    all_evidences: Any = None,
    all_unparseable: Any = None,
    cutoff_date: str | None = None,
) -> dict[str, Any]:
    """Build expectation_revision for news_analyst from event_coverage and news evidences."""
    from typing import Sequence
    gaps: list[str] = []
    cov = event_coverage or {}
    if isinstance(cov, dict) and isinstance(cov.get("event_coverage"), dict):
        cov = cov["event_coverage"]
    cutoff = str(cov.get("cutoff") or cutoff_date or "").strip()

    unverifiable_c = _safe_int(cov.get("unverifiable_count", 0), 0)
    future_c = _safe_int(cov.get("future_rejected_count", 0), 0)
    cninfo_st = cov.get("cninfo_status")

    if unverifiable_c > 0:
        gaps.append("unparseable_publish_time")
    if future_c > 0:
        gaps.append("future_date")
    if cninfo_st == "provider_failure" or cov.get("failure_reason") or cov.get("error"):
        gaps.append("provider_failure")

    ev_list: list[Any] = list(all_evidences or [])
    if not ev_list and isinstance(cov, dict):
        if cov.get("news_items"):
            ev_list = list(cov["news_items"])
        elif cov.get("items"):
            ev_list = list(cov["items"])
        elif cov.get("evidences"):
            ev_list = list(cov["evidences"])
        elif cov.get("all_evidences"):
            ev_list = list(cov["all_evidences"])

    primary_ev = None
    if ev_list:
        # 遍历证据列表，优先选取包含可信发布时间与来源的合格证据
        for ev in ev_list:
            t = (
                getattr(ev, "published_at", None)
                or getattr(ev, "publish_time", None)
                or (ev.get("published_at") if isinstance(ev, dict) else None)
                or (ev.get("publish_time") if isinstance(ev, dict) else None)
            )
            s = getattr(ev, "source", None) or (ev.get("source") if isinstance(ev, dict) else None)
            if t and s:
                primary_ev = ev
                break
        if primary_ev is None:
            primary_ev = ev_list[0]
    elif cov.get("clusters"):
        clusters = cov["clusters"]
        if isinstance(clusters, list) and clusters:
            c0 = clusters[0]
            c_evs = getattr(c0, "evidences", None) or (c0.get("evidences") if isinstance(c0, dict) else None)
            if c_evs and isinstance(c_evs, list) and c_evs:
                primary_ev = c_evs[0]

    pub_time = None
    source = None
    source_hash = None
    content_status = CONTENT_NOT_ATTEMPTED

    if primary_ev is not None:
        pub_time = (
            getattr(primary_ev, "published_at", None)
            or getattr(primary_ev, "publish_time", None)
            or (primary_ev.get("published_at") if isinstance(primary_ev, dict) else None)
            or (primary_ev.get("publish_time") if isinstance(primary_ev, dict) else None)
        )
        source = getattr(primary_ev, "source", None) or (
            primary_ev.get("source") if isinstance(primary_ev, dict) else None
        )
        source_hash = (
            getattr(primary_ev, "source_hash", None)
            or getattr(primary_ev, "content_hash", None)
            or (primary_ev.get("source_hash") if isinstance(primary_ev, dict) else None)
            or (primary_ev.get("content_hash") if isinstance(primary_ev, dict) else None)
        )
        content_status = (
            getattr(primary_ev, "content_status", None)
            or getattr(primary_ev, "content_qualification", None)
            or (primary_ev.get("content_status") if isinstance(primary_ev, dict) else None)
            or (primary_ev.get("content_qualification") if isinstance(primary_ev, dict) else None)
            or CONTENT_NOT_ATTEMPTED
        )
        content_status = str(content_status or CONTENT_NOT_ATTEMPTED).strip().lower()

    if pub_time:
        pub_str = str(pub_time).strip()
        import re
        if not re.match(r"^\d{4}-\d{2}-\d{2}", pub_str):
            if "invalid_publish_time" not in gaps:
                gaps.append("invalid_publish_time")
        elif cutoff:
            try:
                if pub_str[:10] > str(cutoff)[:10] and "future_date" not in gaps:
                    gaps.append("future_date")
            except Exception:
                pass

    if content_status in (CONTENT_HASHED, CONTENT_UNAVAILABLE, CONTENT_NOT_ATTEMPTED, CONTENT_NOT_OBTAINED):
        if "content_not_obtained" not in gaps:
            gaps.append("content_not_obtained")

    publication = {
        "publish_time": pub_time,
        "published_at": pub_time,
        "source": source,
        "source_hash": source_hash,
        "content_qualification": content_status,
        "qualification_status": content_status,
    }

    actual = {
        "status": STATUS_GAP,
        "metric": None,
        "value": None,
        "unit": None,
        "report_period": None,
        "as_of": None,
        "source": "news",
        "reason": "新闻仅包含标题/摘要/元数据，未抽取正文财务结构化指标",
    }

    baseline = {
        "type": BASELINE_NONE,
        "source": None,
        "metric": None,
        "value": None,
        "unit": None,
        "period": None,
        "report_period": None,
        "as_of": None,
    }

    has_fatal_gap = any(
        g in gaps for g in ("provider_failure", "future_date", "unparseable_publish_time", "invalid_publish_time")
    )
    revision = {
        "type": REVISION_QUALITATIVE if (pub_time and not has_fatal_gap and content_status not in (CONTENT_HASHED, CONTENT_UNAVAILABLE, CONTENT_NOT_OBTAINED)) else REVISION_GAP,
        "value": None,
        "percent": None,
        "unit": None,
        "direction": "unknown",
        "detail": "无旧基线，仅作定性事实记录与传导推演，不计算数值修正幅度",
    }

    priced_in = {
        "status": PRICED_IN_UNKNOWN,
        "evidence": None,
    }

    double_count_guard = {
        "status": DOUBLE_COUNT_UNKNOWN,
        "prevent_double_voting": True,
        "description": "无法证明是否已被既有预测/事件栏位计入，阻止重复加票",
    }

    if has_fatal_gap or "content_not_obtained" in gaps:
        status = STATUS_GAP
    elif not pub_time and not ev_list and _safe_int(cov.get("hit_count", 0), 0) == 0:
        status = STATUS_NOT_APPLICABLE
    elif pub_time and not has_fatal_gap:
        status = STATUS_PARTIAL
    else:
        status = STATUS_GAP

    er = {
        "status": status,
        "event_type": EVENT_TYPE_EVENT if (pub_time or ev_list) else EVENT_TYPE_NONE,
        "publication": publication,
        "actual": actual,
        "baseline": baseline,
        "revision": revision,
        "priced_in": priced_in,
        "double_count_guard": double_count_guard,
        "gaps": sorted(list(set(gaps))),
    }
    return make_json_safe(er)


logger = logging.getLogger(__name__)


def _resolve_research_horizon(state: dict | None) -> str:
    """Resolve the active research horizon for the current run.

    Priority:
    1. state["horizon"] if present and truthy
    2. state["horizon_run_metadata"]["resolved"][0] if present
    3. state["horizon_run_metadata"]["requested"][0] if present
    4. get_bound_research_horizon() from H-04a thread binding
    5. fallback to "short"
    """
    if state:
        if state.get("horizon"):
            return state["horizon"]
        metadata = state.get("horizon_run_metadata")
        if isinstance(metadata, dict):
            resolved = metadata.get("resolved")
            if resolved and isinstance(resolved, list) and len(resolved) > 0:
                return resolved[0]
            requested = metadata.get("requested")
            if requested and isinstance(requested, list) and len(requested) > 0:
                return requested[0]
    bound = get_bound_research_horizon()
    if bound:
        return bound
    return "short"


async def _safe_fetch(tool, payload):
    try:
        if hasattr(tool, "invoke"):
            return await asyncio.to_thread(tool.invoke, payload)
        elif callable(tool):
            return await asyncio.to_thread(tool, **payload)
        return str(tool)
    except Exception as exc:
        return f"调用失败：{exc}"


async def _fetch_direct(ticker: str, current_date: str, horizon: str):
    from datetime import datetime, timedelta
    from tradingagents.agents.utils.agent_utils import get_news, get_global_news

    days = 14 if horizon == "short" else 30
    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=days)

    # Parallelize fallback fetches
    results = await asyncio.gather(
        _safe_fetch(get_news, {
            "ticker": ticker, "start_date": start_dt.strftime("%Y-%m-%d"), "end_date": current_date,
        }),
        _safe_fetch(get_global_news, {
            "curr_date": current_date, "look_back_days": days, "limit": 10,
        })
    )
    stock_news, global_news = results
    data_window = f"{days}天"
    return stock_news, global_news, data_window


def create_news_analyst(llm, data_collector=None):
    async def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        stock_name = get_cn_stock_name(ticker)

        ticker_display = f"{ticker} ({stock_name})" if stock_name and stock_name != ticker else ticker
        observation_horizon = "short"  # 新闻面专业观察窗固定为短期
        research_horizon = _resolve_research_horizon(state)
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("news_system_message", config=config) or ""
        horizon_ctx = build_horizon_context(
            observation_horizon,
            focus_areas,
            specific_questions,
            agent_type="news",
            research_horizon=research_horizon,
        )

        pool = data_collector.get(ticker, current_date) if data_collector else None

        if pool is not None:
            data_window = pool.get("_data_window", "14天" if observation_horizon == "short" else "90天")
            stock_news = pool.get("news", "无数据")
            global_news = pool.get("global_news", "无数据")
        else:
            stock_news, global_news, data_window = await _fetch_direct(ticker, current_date, observation_horizon)

        # ── 结构化新闻事件证据与覆盖率计算 ──────────────────
        stock_evidences, stock_unparseable = parse_news_markdown_to_evidences(
            stock_news, default_entity=ticker
        )
        global_evidences, global_unparseable = parse_news_markdown_to_evidences(
            global_news, default_entity="宏观/行业"
        )
        all_evidences = stock_evidences + global_evidences
        all_unparseable = stock_unparseable + global_unparseable

        requested_themes = focus_areas if focus_areas else None
        event_coverage = build_news_event_coverage(
            all_evidences + all_unparseable,
            requested_themes=requested_themes,
            cutoff=current_date,
            window=data_window,
            default_entity=ticker,
        )
        coverage_summary = format_event_coverage_summary(event_coverage)

        # ── 宏观事件情景图谱与行业知识库挂载 ──────────────────
        extra_event_text = f"{stock_news}\n{global_news}"
        macro_report = state.get("macro_report", "")
        if macro_report and macro_report != "无数据":
            extra_event_text += f"\n{macro_report}"
        _, macro_event_ctx = resolve_macro_event_context(
            text=extra_event_text,
            max_events=2,
            fallback_on_miss=False,
        )
        _, industry_ctx = resolve_industry_context(
            ticker=ticker,
            stock_name=stock_name,
            extra_text=extra_event_text,
            state=state,
            fallback_on_miss=False,
        )

        phase1_reports_text = format_phase1_reports(state)

        human_content_blocks = [
            horizon_ctx + "\n" + f"以下是 {ticker_display} 在 {current_date} 的新闻资料（{data_window}）。",
            phase1_reports_text,
            f"{coverage_summary}",
            f"【get_news】\n{stock_news}",
            f"【get_global_news】\n{global_news}",
        ]

        if industry_ctx:
            human_content_blocks.append(f"{industry_ctx}")
        else:
            human_content_blocks.append("【行业常识知识库】\n【知识库未命中】")

        if macro_event_ctx:
            human_content_blocks.append(f"{macro_event_ctx}")
        else:
            human_content_blocks.append("【宏观事件传导图谱】\n【知识库未命中】")

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的新闻资料输出报告，全程使用中文。"
            )),
            HumanMessage(content="\n\n".join(human_content_blocks)),
        ]

        # ── 实现 Token 级流式输出（含降级保障） ──────────────────
        tracker = current_tracker_var.get()
        import time as _time
        full_content = ""
        _last_chunk = None
        _t0 = _time.monotonic()

        try:
            async for chunk in llm.astream(messages):
                _last_chunk = chunk
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                full_content += content
                if check_stream_chunk_degraded(full_content, "News Analyst"):
                    break
                if tracker:
                    tracker._emit_token("News Analyst", "news_report", content)
        except Exception as exc:
            logger.debug("[News Analyst] Stream error: %s", exc)

        if not full_content.strip():
            logger.debug("[News Analyst] Stream yielded empty text, attempting invoke fallback...")
            try:
                res = await asyncio.to_thread(llm.invoke, messages)
                full_content = res.content if hasattr(res, "content") else str(res)
                if tracker:
                    tracker._emit_token("News Analyst", "news_report", full_content)
            except Exception as exc:
                full_content = f"分析报告生成失败：{exc}"

        if check_llm_output_degraded(full_content, "News Analyst"):
            full_content = "新闻分析生成异常（输出退化），本项不可用"
        _elapsed = _time.monotonic() - _t0
        _meta = getattr(_last_chunk, "response_metadata", {}) or {}
        _usage = _meta.get("token_usage") or _meta.get("usage") or {}
        log_llm_call(
            agent_name="News Analyst",
            model_name=getattr(llm, "model_name", None) or getattr(llm, "model", None),
            finish_reason=_meta.get("finish_reason"),
            prompt_tokens=_usage.get("prompt_tokens"),
            completion_tokens=_usage.get("completion_tokens"),
            total_tokens=_usage.get("total_tokens"),
            elapsed_seconds=round(_elapsed, 2),
            response_chars=len(full_content),
            degraded=full_content.endswith("本项不可用"),
        )
        verdict, confidence = extract_verdict(full_content)
        expectation_revision = build_news_expectation_revision(
            event_coverage=event_coverage,
            all_evidences=all_evidences,
            all_unparseable=all_unparseable,
            cutoff_date=current_date,
        )
        return {
            "news_report": full_content,
            "event_coverage": event_coverage,
            "analyst_traces": [{
                "agent": "news_analyst",
                "horizon": research_horizon,
                "research_horizon": research_horizon,
                "observation_horizon": observation_horizon,
                "data_window": data_window,
                "key_finding": f"新闻分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
                "expectation_revision": expectation_revision,
            }],
        }

    return news_analyst_node
