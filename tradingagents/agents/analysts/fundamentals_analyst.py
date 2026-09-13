import logging
import asyncio
import re
import time as _time
from typing import Any, Mapping, Optional, Sequence

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

    Only populates value when metric, value, unit, report_period, as_of are simultaneously present.
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

    # 2. Search for a structured metric with all 5 elements
    fund_text = outputs.get("fundamentals")
    inc_stmt = outputs.get("income_statement")
    bal_sheet = outputs.get("balance_sheet")
    search_texts = [str(fund_text or ""), str(inc_stmt or ""), str(bal_sheet or "")]

    _DISQUALIFYING_LINE_MARKERS = (
        "公告", "预告", "澄清", "说明", "提示", "http:", "https:", ".pdf", ".html",
        "标题", "链接", "快报", "新闻", "意见", "方案", "预案", "摘要", "草案", "简况",
    )

    for text in search_texts:
        if not text or text == "无数据":
            continue
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            if any(marker in line_str for marker in _DISQUALIFYING_LINE_MARKERS):
                continue
            m = re.search(
                r"(?P<period>\d{4}(?:Q[1-4]|H[1-2]|年报|\-\d{2}\-\d{2}))[^\n]*?"
                r"(?P<metric>营业收入|营业总收入|主营业务收入|营收|净利润|归属于母公司所有者的净利润|归母净利润|毛利率)\s*"
                r"(?P<val>[0-9]+(?:\.[0-9]+)?)\s*"
                r"(?P<unit>亿元|万元|元|万|亿|%|万亿元)",
                line_str,
            )
            if m:
                period = m.group("period")
                metric = m.group("metric")
                val = float(m.group("val"))
                unit = m.group("unit")

                as_of = None
                if re.match(r"^\d{4}-\d{2}-\d{2}$", period):
                    as_of = period
                elif "Q1" in period:
                    as_of = f"{period[:4]}-03-31"
                elif "Q2" in period or "H1" in period:
                    as_of = f"{period[:4]}-06-30"
                elif "Q3" in period:
                    as_of = f"{period[:4]}-09-30"
                elif "Q4" in period or "年报" in period:
                    as_of = f"{period[:4]}-12-31"

                # Check future date: strictly block lookahead and forbid actual numbers!
                if as_of and current_date and str(as_of)[:10] > str(current_date)[:10]:
                    gaps.append("future_date")
                    return {
                        "status": STATUS_GAP,
                        "metric": metric,
                        "value": None,
                        "unit": unit,
                        "report_period": period,
                        "as_of": as_of,
                        "source": "fundamentals",
                        "reason": f"财务报表截至日期({as_of})晚于当前截断日期({current_date})，存在前瞻未来数据，记为缺口",
                    }, gaps

                if not as_of:
                    gaps.append("missing_as_of_date")
                    return {
                        "status": STATUS_GAP,
                        "metric": None,
                        "value": None,
                        "unit": None,
                        "report_period": period,
                        "as_of": None,
                        "source": "fundamentals",
                        "reason": "缺少明确截至日期五要素，记为缺口",
                    }, gaps

                return {
                    "status": STATUS_AVAILABLE,
                    "metric": metric,
                    "value": val,
                    "unit": unit,
                    "report_period": period,
                    "as_of": as_of,
                    "source": "fundamentals",
                    "reason": None,
                }, gaps

    # If no 5 elements simultaneously found:
    gaps.append("actual_incomplete_elements")
    return {
        "status": STATUS_GAP,
        "metric": None,
        "value": None,
        "unit": None,
        "report_period": None,
        "as_of": None,
        "source": "fundamentals",
        "reason": "缺少完整指标、数值、单位、报告期、截至日期五要素",
    }, gaps


def _extract_baseline_and_revision(
    actual: dict[str, Any],
    pool: dict[str, Any] | None,
    current_date: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Extract baseline and compute revision with strict contract checks (E-04)."""
    gaps: list[str] = []

    ef = pool.get("earnings_forecast") if isinstance(pool, dict) else None
    if ef:
        ef_str = str(ef)
        for marker in DATA_FAILURE_MARKERS:
            if marker in ef_str:
                gaps.append("provider_failure:earnings_forecast")
                ef = None
                break

    explicit_baseline = pool.get("baseline") if isinstance(pool, dict) else None

    baseline: dict[str, Any]
    if isinstance(explicit_baseline, dict):
        b_type = explicit_baseline.get("type", BASELINE_NONE)
        b_source = explicit_baseline.get("source")
        if b_type != BASELINE_NONE and (not b_source or not str(b_source).strip()):
            gaps.append("baseline_source_missing")
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
        else:
            baseline = {
                "type": b_type,
                "source": b_source,
                "metric": explicit_baseline.get("metric"),
                "value": explicit_baseline.get("value"),
                "unit": explicit_baseline.get("unit"),
                "period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "report_period": explicit_baseline.get("period") or explicit_baseline.get("report_period"),
                "as_of": explicit_baseline.get("as_of"),
            }
    elif isinstance(ef, dict):
        b_source = ef.get("source")
        b_type = ef.get("type")
        if not b_source or not str(b_source).strip() or not b_type or b_type not in (
            BASELINE_MANAGEMENT_GUIDANCE,
            BASELINE_CONSENSUS_EXPECTATION,
            BASELINE_PRIOR_SELF_FORECAST,
            BASELINE_IMPLICIT_MODEL,
        ):
            gaps.append("baseline_source_missing")
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
        else:
            baseline = {
                "type": b_type,
                "source": b_source,
                "metric": ef.get("metric"),
                "value": ef.get("value"),
                "unit": ef.get("unit"),
                "period": ef.get("period") or ef.get("report_period"),
                "report_period": ef.get("period") or ef.get("report_period"),
                "as_of": ef.get("as_of"),
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
        rev_type = REVISION_QUALITATIVE if act_status == STATUS_AVAILABLE else REVISION_GAP
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
            "detail": "实际财务数据缺失，无法计算修正",
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
    )
    all_gaps.extend(base_gaps)

    # ── P1 Fix: 严格审查 compliance 合规结果，拦截期间错配与累计值冒充 ──
    if isinstance(compliance, dict):
        comp_status = str(compliance.get("compliance_status") or "").strip().lower()
        violations = compliance.get("violations") or []
        if comp_status == "violations_found" or violations:
            for v in violations:
                kind = v.get("kind") if isinstance(v, dict) else str(v)
                all_gaps.append(f"compliance_violation:{kind}")
            # 报告期违规（如累计值冒充单季度），严禁 numeric 修正并将实际数值置为缺口
            all_gaps.append("period_mismatch")
            if revision.get("type") == REVISION_NUMERIC:
                revision = {
                    "type": REVISION_GAP,
                    "value": None,
                    "percent": None,
                    "unit": None,
                    "direction": "gap",
                    "detail": "财务期间合规检测存在违规（累计值冒充单季度或期间错配），禁止数值修正",
                }
            if actual.get("status") == STATUS_AVAILABLE:
                actual["status"] = STATUS_GAP
                actual["value"] = None
                actual["reason"] = "财务期间合规检测存在违规，实际数值被标记为不可信缺口"

    # ── P2 Fix: 计算财务报表输入的真实 SHA-256 数据源 Hash ──────────
    import hashlib
    raw_content = ""
    for k in ("fundamentals", "income_statement", "balance_sheet", "cashflow"):
        v = outputs.get(k)
        if v and v != "无数据":
            raw_content += f"{k}:{v}\n"
    stmt_hash = f"sha256:{hashlib.sha256(raw_content.encode('utf-8')).hexdigest()}" if raw_content else None

    has_provider_failure = any(g.startswith("provider_failure") for g in all_gaps)
    pub_date = actual.get("as_of") or current_date
    has_actual_num = (actual.get("value") is not None and actual.get("status") == STATUS_AVAILABLE)

    publication = {
        "publish_time": pub_date,
        "published_at": pub_date,
        "source": "financial_statement",
        "source_hash": stmt_hash or f"fin_{actual.get('report_period') or 'NA'}_{pub_date or 'NA'}",
        "content_qualification": CONTENT_QUALIFIED if has_actual_num else (
            CONTENT_UNAVAILABLE if has_provider_failure else CONTENT_NOT_OBTAINED
        ),
        "qualification_status": CONTENT_QUALIFIED if has_actual_num else (
            CONTENT_UNAVAILABLE if has_provider_failure else CONTENT_NOT_OBTAINED
        ),
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

    if has_provider_failure or "future_date" in all_gaps:
        status = STATUS_GAP
    elif revision.get("type") == REVISION_NUMERIC and actual.get("status") == STATUS_AVAILABLE:
        status = STATUS_AVAILABLE
    elif actual.get("status") == STATUS_AVAILABLE:
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
