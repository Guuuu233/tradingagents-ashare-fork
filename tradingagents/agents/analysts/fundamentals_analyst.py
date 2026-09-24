import logging
import asyncio
import datetime
import re
import time as _time
from typing import Any, Mapping, Optional, Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.agents.utils.price_ref_prompt import price_ref_prompt_suffix
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
from tradingagents.agents.utils.financial_period_compliance import (
    DATA_FAILURE_MARKERS,
    check_financial_period_compliance,
    parse_financial_statement_inputs,
)
from tradingagents.agents.utils.context_utils import get_cn_stock_name, format_phase1_reports
from tradingagents.agents.utils.knowledge_context import (
    resolve_industry_context,
    resolve_macro_event_context,
    resolve_historical_cases_context,
)
from tradingagents.dataflows.industry_linkage import (
    format_industry_linkage_for_prompt,
)
from tradingagents.graph.data_collector import _map_stock_to_industry
from api.database import log_llm_call
from tradingagents.agents.analysts.news_analyst import (
    BASELINE_CONSENSUS_EXPECTATION,
    BASELINE_IMPLICIT_MODEL,
    BASELINE_MANAGEMENT_GUIDANCE,
    BASELINE_NONE,
    BASELINE_PRIOR_SELF_FORECAST,
    CONTENT_HASHED,
    CONTENT_NOT_ATTEMPTED,
    CONTENT_NOT_OBTAINED,
    CONTENT_QUALIFIED,
    CONTENT_UNAVAILABLE,
    DOUBLE_COUNT_ACCOUNTED_FOR,
    DOUBLE_COUNT_NOT_ACCOUNTED_FOR,
    DOUBLE_COUNT_UNKNOWN,
    EVENT_TYPE_FUNDAMENTAL,
    EVENT_TYPE_NONE,
    PRICED_IN_NOT_SUPPORTED,
    PRICED_IN_SUPPORTED,
    PRICED_IN_UNKNOWN,
    REVISION_GAP,
    REVISION_NUMERIC,
    REVISION_QUALITATIVE,
    STATUS_AVAILABLE,
    STATUS_GAP,
    STATUS_NOT_APPLICABLE,
    STATUS_PARTIAL,
    make_default_expectation_revision,
    make_json_safe,
    validate_expectation_revision,
)

logger = logging.getLogger(__name__)


def _extract_structured_actual(
    outputs: dict[str, Any],
    current_date: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Extract structured financial actual record (E-04).

    Only populates value when metric, value, unit, report_period, as_of are simultaneously present
    in authentic structured financial records. Disallows headline, URL, PDF filename, or hash regex extraction.
    """
    gaps: list[str] = []

    # 1. Check for provider failure markers in any financial statement input
    _COMMON_ERRORS = ("【数据获取失败】", "未获取到报表数据", "接口返回的报表行不可解析", "调用失败", "数据源异常", "获取失败", "网络超时", "接口异常", "HTTP 5")
    for stmt_k, raw_v in outputs.items():
        raw_str = str(raw_v or "")
        matched_err = None
        for marker in DATA_FAILURE_MARKERS:
            if marker in raw_str:
                matched_err = marker
                break
        if not matched_err:
            for err in _COMMON_ERRORS:
                if err in raw_str:
                    matched_err = err
                    break
        if matched_err:
            gaps.append(f"provider_failure:{stmt_k}")
            return {
                "status": STATUS_GAP,
                "metric": None,
                "value": None,
                "unit": None,
                "report_period": None,
                "as_of": None,
                "source": stmt_k,
                "reason": f"报表数据获取失败（{stmt_k}包含失败标记: {matched_err}）",
            }, gaps

    # 2. Check for explicit structured financial record
    raw_actual = (
        outputs.get("structured_financials")
        or outputs.get("financial_records")
        or outputs.get("structured_actual")
        or outputs.get("actual")
    )
    explicit_actual = None
    if isinstance(raw_actual, list) and raw_actual:
        explicit_actual = raw_actual[0] if isinstance(raw_actual[0], dict) else None
    elif isinstance(raw_actual, dict):
        explicit_actual = raw_actual

    if isinstance(explicit_actual, dict):
        metric = explicit_actual.get("metric")
        val = explicit_actual.get("value")
        unit = explicit_actual.get("unit")
        period = explicit_actual.get("report_period") or explicit_actual.get("period")
        as_of = explicit_actual.get("as_of")
        source = explicit_actual.get("source") or outputs.get("source") or "structured_financials"
        has_all_elements = bool(
            metric and str(metric).strip()
            and val is not None
            and unit and str(unit).strip()
            and period and str(period).strip()
            and as_of and str(as_of).strip()
        )
        if has_all_elements:
            as_of_str = str(as_of).strip()
            is_valid_cal_date = False
            if re.match(r"^\d{4}-\d{2}-\d{2}$", as_of_str):
                try:
                    datetime.datetime.strptime(as_of_str, "%Y-%m-%d")
                    is_valid_cal_date = True
                except ValueError:
                    is_valid_cal_date = False
            if not is_valid_cal_date:
                gaps.append("invalid_as_of")
                return {
                    "status": STATUS_GAP,
                    "metric": str(metric).strip(),
                    "value": None,
                    "unit": str(unit).strip(),
                    "report_period": str(period).strip(),
                    "as_of": None,
                    "source": str(source).strip(),
                    "reason": f"截至日期不是合法公历日期({as_of_str})",
                }, gaps
            if current_date and as_of_str > str(current_date)[:10]:
                gaps.append("future_date")
                return {
                    "status": STATUS_GAP,
                    "metric": str(metric).strip(),
                    "value": None,
                    "unit": str(unit).strip(),
                    "report_period": str(period).strip(),
                    "as_of": as_of_str,
                    "source": str(source).strip(),
                    "reason": f"截至日期({as_of_str})晚于当前分析基准日({current_date})，属于未来数据",
                }, gaps
            try:
                numeric_val = float(val)
                return {
                    "status": STATUS_AVAILABLE,
                    "metric": str(metric).strip(),
                    "value": numeric_val,
                    "unit": str(unit).strip(),
                    "report_period": str(period).strip(),
                    "as_of": as_of_str,
                    "source": str(source).strip(),
                    "reason": None,
                }, gaps
            except (ValueError, TypeError):
                pass

    # 3. No authentic structured record: strictly return gap without text regex
    # Regular unstructured text from fundamentals/income_statement cannot generate actual
    gaps.append("actual_incomplete_elements")
    return {
        "status": STATUS_GAP,
        "metric": None,
        "value": None,
        "unit": None,
        "report_period": None,
        "as_of": None,
        "source": None,
        "reason": "缺少结构化财务记录（禁止使用非结构化自由文本正则提取实际值）",
    }, gaps


def _extract_baseline_and_revision(
    actual: dict[str, Any],
    pool: dict[str, Any] | None,
    current_date: str | None = None,
    compliance: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Extract baseline and compute revision with strict contract checks (E-04)."""
    gaps: list[str] = []

    # 1. Inspect compliance results (E-04 Defect 3)
    has_compliance_violation = False
    if isinstance(compliance, dict):
        st = compliance.get("status")
        violations = compliance.get("violations") or []
        if st == "violations_found" or len(violations) > 0:
            has_compliance_violation = True
            for v in violations:
                v_kind = v.get("kind") if isinstance(v, dict) else str(v)
                gaps.append(f"compliance_violation:{v_kind}")
            gaps.append("period_mismatch")

    ef = pool.get("earnings_forecast") if isinstance(pool, dict) else None
    if ef:
        ef_str = str(ef)
        for marker in DATA_FAILURE_MARKERS:
            if marker in ef_str:
                gaps.append("provider_failure:earnings_forecast")
                ef = None
                break

    def _validate_baseline_as_of(raw_as_of: Any) -> tuple[str | None, list[str]]:
        if not raw_as_of or not str(raw_as_of).strip():
            return None, ["baseline_as_of_missing", "missing_as_of"]
        as_of_str = str(raw_as_of).strip()
        is_valid_cal_date = False
        if re.match(r"^\d{4}-\d{2}-\d{2}$", as_of_str):
            try:
                datetime.datetime.strptime(as_of_str, "%Y-%m-%d")
                is_valid_cal_date = True
            except ValueError:
                is_valid_cal_date = False
        if not is_valid_cal_date:
            return as_of_str, ["invalid_as_of"]
        if current_date and as_of_str > str(current_date)[:10]:
            return as_of_str, ["future_date"]
        return as_of_str, []

    explicit_baseline = pool.get("baseline") if isinstance(pool, dict) else None

    baseline: dict[str, Any]
    if isinstance(explicit_baseline, dict):
        b_type = explicit_baseline.get("type")
        b_source = explicit_baseline.get("source")
        has_real_source = bool(b_source and str(b_source).strip())
        has_real_type = bool(b_type and str(b_type).strip() and str(b_type).strip() != BASELINE_NONE)
        b_as_of_val, b_as_of_gaps = _validate_baseline_as_of(explicit_baseline.get("as_of"))
        gaps.extend(b_as_of_gaps)
        has_real_as_of = (len(b_as_of_gaps) == 0)

        if not has_real_source or not has_real_type or not has_real_as_of:
            if not has_real_source:
                gaps.append("baseline_source_missing")
            baseline = {
                "type": BASELINE_NONE,
                "source": b_source if has_real_source else None,
                "metric": explicit_baseline.get("metric"),
                "value": None,
                "unit": explicit_baseline.get("unit"),
                "period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "report_period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "as_of": b_as_of_val if ("baseline_as_of_missing" not in b_as_of_gaps) else None,
            }
        else:
            baseline = {
                "type": str(b_type).strip(),
                "source": str(b_source).strip(),
                "metric": explicit_baseline.get("metric"),
                "value": explicit_baseline.get("value"),
                "unit": explicit_baseline.get("unit"),
                "period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "report_period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "as_of": b_as_of_val,
            }
    elif isinstance(ef, dict):
        raw_source = ef.get("source")
        raw_type = ef.get("type")
        has_real_source = bool(raw_source and str(raw_source).strip())
        has_real_type = bool(raw_type and str(raw_type).strip() and str(raw_type).strip() != BASELINE_NONE)
        b_as_of_val, b_as_of_gaps = _validate_baseline_as_of(ef.get("as_of"))
        gaps.extend(b_as_of_gaps)
        has_real_as_of = (len(b_as_of_gaps) == 0)

        if not has_real_source or not has_real_type or not has_real_as_of:
            if not has_real_source:
                gaps.append("baseline_source_missing")
            baseline = {
                "type": BASELINE_NONE,
                "source": raw_source if has_real_source else None,
                "metric": ef.get("metric"),
                "value": None,
                "unit": ef.get("unit"),
                "period": ef.get("period") or ef.get("report_period"),
                "report_period": ef.get("period") or ef.get("report_period"),
                "as_of": b_as_of_val if ("baseline_as_of_missing" not in b_as_of_gaps) else None,
            }
        else:
            baseline = {
                "type": str(raw_type).strip(),
                "source": str(raw_source).strip(),
                "metric": ef.get("metric"),
                "value": ef.get("value"),
                "unit": ef.get("unit"),
                "period": ef.get("period") or ef.get("report_period"),
                "report_period": ef.get("period") or ef.get("report_period"),
                "as_of": b_as_of_val,
            }
    else:
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

    b_type = baseline.get("type")
    b_val = baseline.get("value")
    act_val = actual.get("value")
    act_status = actual.get("status")

    if b_type == BASELINE_NONE or b_val is None:
        # Case 4: No old baseline -> revision cannot be numeric, value and percent must be None
        rev_type = REVISION_QUALITATIVE if (act_status == STATUS_AVAILABLE and act_val is not None) else REVISION_GAP
        revision = {
            "type": rev_type,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "unknown",
            "detail": "无旧基线，只能定性呈现，不计算数值修正幅度",
        }
        return baseline, revision, gaps

    if act_status != STATUS_AVAILABLE or act_val is None:
        revision = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": "实际财务数据缺失或存在缺口，无法计算修正",
        }
        return baseline, revision, gaps

    # Case 3 / Defect 3: Block numeric revision when compliance reports period violations
    if has_compliance_violation:
        revision = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": "财务期间合规校验存在违规（如累计值误标单季或期间错配），阻断数值修正计算",
        }
        return baseline, revision, gaps

    # Case 3: Period mismatch check
    act_period = str(actual.get("report_period") or "").strip()
    base_period = str(baseline.get("period") or baseline.get("report_period") or "").strip()
    if act_period != base_period:
        gaps.append("period_mismatch")
        revision = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": f"实际财报期间({act_period})与业绩预测期间({base_period})不同，不得互相替代",
        }
        return baseline, revision, gaps

    # Case 6: Unit and metric mismatch check
    act_unit = str(actual.get("unit") or "").strip()
    base_unit = str(baseline.get("unit") or "").strip()
    if act_unit != base_unit:
        gaps.append("unit_mismatch")
        revision = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": f"实际单位({act_unit})与预测单位({base_unit})不同，不可比较",
        }
        return baseline, revision, gaps

    act_metric = str(actual.get("metric") or "").strip()
    base_metric = str(baseline.get("metric") or "").strip()
    if act_metric != base_metric:
        gaps.append("metric_mismatch")
        revision = {
            "type": REVISION_GAP,
            "value": None,
            "percent": None,
            "unit": None,
            "direction": "gap",
            "detail": f"实际指标({act_metric})与预测指标({base_metric})不一致，不可比较",
        }
        return baseline, revision, gaps

    delta = float(act_val) - float(b_val)
    pct = round(delta / abs(float(b_val)) * 100.0, 2) if float(b_val) != 0 else None
    dir_str = "positive" if delta > 0 else ("negative" if delta < 0 else "neutral")
    revision = {
        "type": REVISION_NUMERIC,
        "value": round(delta, 4),
        "percent": pct,
        "unit": actual.get("unit"),
        "direction": dir_str,
        "detail": f"实际值较基线修正 {delta:+.2f}{actual.get('unit')} ({pct:+.2f}%)",
    }
    return baseline, revision, gaps


def build_fundamentals_expectation_revision(
    outputs: dict[str, Any],
    pool: dict[str, Any] | None = None,
    current_date: str | None = None,
    compliance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build expectation_revision for fundamentals_analyst (E-04)."""
    all_gaps: list[str] = []

    actual, act_gaps = _extract_structured_actual(outputs, current_date=current_date)
    all_gaps.extend(act_gaps)

    baseline, revision, base_gaps = _extract_baseline_and_revision(
        actual=actual,
        pool=pool,
        current_date=current_date,
        compliance=compliance,
    )
    all_gaps.extend(base_gaps)

    has_provider_failure = any(g.startswith("provider_failure") for g in all_gaps)
    has_compliance_violation = any(g.startswith("compliance_violation") for g in all_gaps)
    if has_compliance_violation:
        all_gaps.append("compliance_violation")
        all_gaps.append("period_mismatch")

    real_pub_time = None
    real_source = None
    real_source_hash = None
    if isinstance(outputs, dict):
        real_pub_time = (
            outputs.get("publish_time")
            or outputs.get("published_at")
            or (outputs.get("structured_financials") or {}).get("publish_time")
            or (outputs.get("structured_financials") or {}).get("published_at")
        )
        real_source = (
            outputs.get("source")
            or (outputs.get("structured_financials") or {}).get("source")
        )
        real_source_hash = (
            outputs.get("source_hash")
            or outputs.get("content_hash")
            or (outputs.get("structured_financials") or {}).get("source_hash")
        )

    if real_pub_time and str(real_pub_time).strip():
        pub_time = str(real_pub_time).strip()
    else:
        pub_time = None
        all_gaps.append("missing_publish_time")

    if real_source and str(real_source).strip():
        pub_source = str(real_source).strip()
    else:
        pub_source = None
        all_gaps.append("missing_source")

    real_source_hash_str = str(real_source_hash).strip() if real_source_hash else ""
    is_fabricated_hash = bool(real_source_hash_str.startswith("fin_"))
    if real_source_hash_str and not is_fabricated_hash:
        source_hash = real_source_hash_str
    else:
        source_hash = None
        all_gaps.append("source_hash_missing")
        if is_fabricated_hash:
            all_gaps.append("fabricated_source_hash")

    # Qualification status
    if has_provider_failure:
        content_status = CONTENT_UNAVAILABLE
    elif source_hash and pub_time and pub_source and not has_compliance_violation and "invalid_as_of" not in all_gaps:
        content_status = CONTENT_QUALIFIED
    elif source_hash:
        content_status = CONTENT_HASHED
    else:
        content_status = CONTENT_UNAVAILABLE

    publication = {
        "publish_time": pub_time,
        "published_at": pub_time,
        "source": pub_source,
        "source_hash": source_hash,
        "content_qualification": content_status,
        "qualification_status": content_status,
    }

    priced_in = {
        "status": PRICED_IN_UNKNOWN,
        "evidence": None,
    }

    double_count_guard = {
        "status": DOUBLE_COUNT_UNKNOWN,
        "prevent_double_voting": True,
        "description": "无法证明该影响是否已被已有预测/事件栏位计入，保持 unknown 且阻止再次加票",
    }

    if (
        has_provider_failure
        or "future_date" in all_gaps
        or "invalid_as_of" in all_gaps
        or "missing_as_of" in all_gaps
        or "baseline_as_of_missing" in all_gaps
        or "source_hash_missing" in all_gaps
        or "fabricated_source_hash" in all_gaps
        or has_compliance_violation
        or actual.get("status") == STATUS_GAP
        or not source_hash
        or not pub_time
        or not pub_source
    ):
        status = STATUS_GAP
    elif revision.get("type") == REVISION_NUMERIC and actual.get("status") == STATUS_AVAILABLE and source_hash and pub_time and pub_source:
        status = STATUS_AVAILABLE
    elif actual.get("status") == STATUS_AVAILABLE and source_hash and pub_time and pub_source:
        status = STATUS_PARTIAL
    else:
        status = STATUS_GAP

    er = {
        "status": status,
        "event_type": EVENT_TYPE_FUNDAMENTAL,
        "publication": publication,
        "actual": actual,
        "baseline": baseline,
        "revision": revision,
        "priced_in": priced_in,
        "double_count_guard": double_count_guard,
        "gaps": sorted(list(set(all_gaps))),
    }
    return make_json_safe(er)



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


def create_fundamentals_analyst(llm, data_collector=None):
    async def _safe(tool, payload):
        try:
            if hasattr(tool, "invoke"):
                return await asyncio.to_thread(tool.invoke, payload)
            elif callable(tool):
                return await asyncio.to_thread(tool, **payload)
            return str(tool)
        except Exception as exc:
            return f"调用失败：{exc}"

    async def _fetch_optional_tool(tool_name: str, payload: dict) -> str:
        try:
            from tradingagents.agents.utils import agent_utils
            tool = getattr(agent_utils, tool_name, None)
            if tool is None:
                from tradingagents.dataflows import interface
                tool = getattr(interface, tool_name, None)
            if tool is not None:
                return await _safe(tool, payload)
        except Exception as exc:
            return f"调用失败：{exc}"
        return "无数据"

    async def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        stock_name = get_cn_stock_name(ticker)

        ticker_display = f"{ticker} ({stock_name})" if stock_name and stock_name != ticker else ticker
        logger.debug("[Fundamentals Analyst] START %s %s", ticker_display, current_date)
        observation_horizon = "medium"  # 基本面专业观察窗固定为中长期
        research_horizon = _resolve_research_horizon(state)
        data_window = "财报周期"
        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        config = get_config()
        system_message = get_prompt("fundamentals_system_message", config=config) or ""
        horizon_ctx = build_horizon_context(
            observation_horizon,
            focus_areas,
            specific_questions,
            agent_type="fundamentals",
            research_horizon=research_horizon,
        )

        pool = data_collector.get(ticker, current_date) if data_collector else None

        if pool is not None:
            outputs = {
                k: pool.get(k, "无数据")
                for k in ["fundamentals", "balance_sheet", "cashflow", "income_statement"]
            }
            global_indices = pool.get("global_indices", "无数据")
            major_assets = pool.get("major_assets", "无数据")
            cn_indices = pool.get("cn_indices", "无数据")
            industry_linkage_data = pool.get("industry_linkage")
        else:
            from tradingagents.agents.utils.agent_utils import (
                get_fundamentals,
                get_balance_sheet,
                get_cashflow,
                get_income_statement,
            )
            tasks = {
                "fundamentals": _safe(get_fundamentals, {"ticker": ticker, "curr_date": current_date}),
                "balance_sheet": _safe(get_balance_sheet, {"ticker": ticker, "freq": "quarterly", "curr_date": current_date}),
                "cashflow": _safe(get_cashflow, {"ticker": ticker, "freq": "quarterly", "curr_date": current_date}),
                "income_statement": _safe(get_income_statement, {"ticker": ticker, "freq": "quarterly", "curr_date": current_date}),
                "global_indices": _fetch_optional_tool("get_global_indices", {"curr_date": current_date}),
                "major_assets": _fetch_optional_tool("get_major_assets", {"curr_date": current_date}),
                "cn_indices": _fetch_optional_tool("get_cn_indices", {"curr_date": current_date}),
            }
            keys = list(tasks.keys())
            results = await asyncio.gather(*[tasks[k] for k in keys])
            res_dict = dict(zip(keys, results))
            outputs = {k: res_dict[k] for k in ["fundamentals", "balance_sheet", "cashflow", "income_statement"]}
            global_indices = res_dict.get("global_indices", "无数据")
            major_assets = res_dict.get("major_assets", "无数据")
            cn_indices = res_dict.get("cn_indices", "无数据")

            # Fallback 获取产业链数据
            mapped_ind = _map_stock_to_industry(ticker)
            if mapped_ind:
                try:
                    from tradingagents.dataflows.providers.industry_linkage_provider import (
                        IndustryLinkageProvider,
                    )
                    _provider = IndustryLinkageProvider()
                    industry_linkage_data = _provider.get_industry_linkage(mapped_ind, as_of=current_date)
                except Exception as exc:
                    logger.warning("[Fundamentals Analyst] 获取产业链数据异常: %s", exc)
                    industry_linkage_data = None
            else:
                industry_linkage_data = None

        # ── 行业常识知识库与宏观情景挂载 ──────────────────
        combined_text = "\n".join(
            str(v) for v in outputs.values() if v and v != "无数据"
        )
        _, industry_ctx = resolve_industry_context(
            ticker=ticker,
            stock_name=stock_name,
            extra_text=combined_text,
            state=state,
            fallback_on_miss=False,
        )
        _, macro_event_ctx = resolve_macro_event_context(
            text=combined_text,
            max_events=1,
            fallback_on_miss=False,
        )
        _, historical_cases_ctx = resolve_historical_cases_context(
            ticker=ticker,
            stock_name=stock_name,
            trade_date=current_date,
            state=state,
            max_cases=3,
            fallback_on_miss=False,
        )

        # ── 产业链联想数据段落 ──────────────────────
        industry_linkage_text = format_industry_linkage_for_prompt(industry_linkage_data)

        phase1_reports_text = format_phase1_reports(state)

        human_content_blocks = [
            horizon_ctx + "\n" + f"以下是 {ticker_display} 在 {current_date} 的基本面资料与产业链/宏观背景。",
            phase1_reports_text,
            f"{industry_linkage_text}",
        ]

        if industry_ctx:
            human_content_blocks.append(f"{industry_ctx}")
        else:
            human_content_blocks.append("【行业常识知识库】\n【知识库未命中】")

        if global_indices != "无数据" or major_assets != "无数据" or cn_indices != "无数据":
            macro_blocks = []
            if major_assets != "无数据":
                macro_blocks.append(f"大类资产与商品（成本端/通胀参考）：\n{major_assets}")
            if cn_indices != "无数据":
                macro_blocks.append(f"国内大盘核心指数：\n{cn_indices}")
            if global_indices != "无数据":
                macro_blocks.append(f"全球市场核心指数：\n{global_indices}")
            if macro_blocks:
                human_content_blocks.append("【大类资产与宏观大盘背景】\n" + "\n\n".join(macro_blocks))

        if macro_event_ctx:
            human_content_blocks.append(f"{macro_event_ctx}")
        else:
            human_content_blocks.append("【宏观事件传导图谱】\n【知识库未命中】")

        if historical_cases_ctx:
            human_content_blocks.append(f"{historical_cases_ctx}")
        else:
            human_content_blocks.append("【历史案例复盘】\n【历史案例未命中】")

        human_content_blocks.extend([
            f"【get_fundamentals】\n{outputs['fundamentals']}",
            f"【get_balance_sheet】\n{outputs['balance_sheet']}",
            f"【get_cashflow】\n{outputs['cashflow']}",
            f"【get_income_statement】\n{outputs['income_statement']}",
        ])

        messages = [
            SystemMessage(content=(
                system_message
                + "\n\n请严格基于提供的数据输出报告，全程使用中文。"
                + price_ref_prompt_suffix(state, config)
            )),
            HumanMessage(content="\n\n".join(human_content_blocks)),
        ]

        # ── 实现 Token 级流式输出（含降级保障） ──────────────────
        tracker = current_tracker_var.get()
        full_content = ""
        _last_chunk = None
        _t0 = _time.monotonic()

        try:
            async for chunk in llm.astream(messages):
                _last_chunk = chunk
                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                full_content += content
                if check_stream_chunk_degraded(full_content, "Fundamentals Analyst"):
                    break
                if tracker:
                    tracker._emit_token("Fundamentals Analyst", "fundamentals_report", content)
        except Exception as exc:
            logger.debug("[Fundamentals Analyst] Stream error: %s", exc)

        if not full_content.strip():
            logger.debug("[Fundamentals Analyst] Stream yielded empty text, attempting invoke fallback...")
            try:
                res = await asyncio.to_thread(llm.invoke, messages)
                full_content = res.content if hasattr(res, "content") else str(res)
                if tracker:
                    tracker._emit_token("Fundamentals Analyst", "fundamentals_report", full_content)
            except Exception as exc:
                full_content = f"分析报告生成失败：{exc}"

        logger.debug("[Fundamentals Analyst] DONE %s, report length=%s", ticker_display, len(full_content))
        if check_llm_output_degraded(full_content, "Fundamentals Analyst"):
            full_content = "基本面分析生成异常（输出退化），本项不可用"
        _elapsed = _time.monotonic() - _t0
        _meta = getattr(_last_chunk, "response_metadata", {}) or {}
        _usage = _meta.get("token_usage") or _meta.get("usage") or {}
        log_llm_call(
            agent_name="Fundamentals Analyst",
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
        try:
            compliance = check_financial_period_compliance(full_content, outputs)
        except Exception as exc:
            logger.warning("[Fundamentals Analyst] Financial period compliance check error: %s", exc)
            compliance = {
                "status": "not_checked",
                "not_checked_reason": f"校验运行异常: {exc}",
                "violations": [],
            }
        expectation_revision = build_fundamentals_expectation_revision(
            outputs=outputs,
            pool=pool,
            current_date=current_date,
            compliance=compliance,
        )
        return {
            "fundamentals_report": full_content,
            "analyst_traces": [{
                "agent": "fundamentals_analyst",
                "horizon": research_horizon,
                "research_horizon": research_horizon,
                "observation_horizon": observation_horizon,
                "data_window": data_window,
                "key_finding": f"基本面分析结论：{verdict}",
                "verdict": verdict,
                "confidence": confidence,
                "financial_period_compliance": compliance,
                "expectation_revision": expectation_revision,
            }],
        }

    return fundamentals_analyst_node
