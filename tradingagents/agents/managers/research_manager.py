import hashlib
import logging
import re
import time
from typing import Any, Mapping, Sequence

from tradingagents.dataflows.config import get_config
from tradingagents.prompts import get_prompt
from tradingagents.prompts.catalog import _resolve_language
from tradingagents.agents.utils.agent_states import (
    current_tracker_var,
    is_credit_weighting_enabled,
)
from tradingagents.agents.utils.debate_utils import (
    build_debate_report_manifest,
    format_claim_subset_for_prompt,
    format_claims_for_prompt,
    safe_int,
    validate_debate_preconditions,
)
from tradingagents.agents.utils.evidence_summary import (
    build_dense_report_input,
    build_evidence_summary,
)
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    extract_and_validate_manager_verdict,
    format_battlefield_coverage,
    format_challenge_verification_summary,
    format_challenges_for_prompt,
    format_claims_with_verification_for_prompt,
)
from tradingagents.agents.utils.claim_cluster import (
    RELATION_GRAPH_STATUS_AVAILABLE,
    RELATION_GRAPH_STATUS_INVALID,
    RELATION_GRAPH_STATUS_PENDING,
    cluster_claims,
    format_claim_cluster_summary_for_prompt,
    tally_cluster_votes,
)
from tradingagents.agents.utils.prompt_injection import (
    DEFAULT_PLACEMENT,
    Placement,
    PromptGuardVerdict,
    build_injection_slots,
    lint_custom_prompt,
)
from tradingagents.agents.utils.decision_status import (
    ACTION_NO_TRADE,
    DIRECTION_NA,
    status_from_manager_verdict,
)
from tradingagents.agents.utils.run_integrity import (
    evaluate_state_integrity,
    fund_flow_guard_abstain_status,
)
from tradingagents.graph.intent_parser import (
    build_horizon_context,
    get_bound_research_horizon,
)
from tradingagents.agents.analysts.news_analyst import (
    STATUS_AVAILABLE,
    STATUS_PARTIAL,
    STATUS_GAP,
    STATUS_NOT_APPLICABLE,
    EVENT_TYPE_FUNDAMENTAL,
    EVENT_TYPE_EVENT,
    EVENT_TYPE_NONE,
    PRICED_IN_SUPPORTED,
    PRICED_IN_NOT_SUPPORTED,
    PRICED_IN_UNKNOWN,
    make_default_expectation_revision,
    make_json_safe,
    validate_expectation_revision,
)

_logger = logging.getLogger(__name__)

_RELATION_GRAPH_KEYS = ("evidence_relation_graph", "evidence_relations")


class RelationPromptGuardError(ValueError):
    """The research manager prompt template no longer matches the E-02 prompt guard."""


# E-02: the manager always passes an explicit relation status, so the legacy
# keyword-cluster tally and analyst weighting instructions are rewritten into
# relation-only contribution rules.  Every legacy segment must match exactly the
# expected number of times and no weighting instruction may survive, so template
# drift fails closed instead of silently restoring keyword voting.
_RELATION_PROMPT_REWRITES: dict[str, tuple[tuple[str, str, int], ...]] = {
    "zh": (
        (
            "1. **各分析师 Verdict 全景概览与动态加权**：",
            "1. **各分析师 Verdict 全景概览与证据贡献状态**：",
            1,
        ),
        (
            "与动态加权权重：",
            "与证据贡献状态（引用上文 E-02 关系折叠结果；无显式 E-01 关系时写 UNKNOWN，不得给出百分比或分值）：",
            1,
        ),
        ("：verdict 与权重", "：verdict 与证据贡献状态", 7),
        (
            "   - 计票按 cluster_id 去重计票：analyst_count 发言人数仅作解释，不得当作独立权重；"
            "同属价格冲击（收盘价/成交量/当日涨跌幅）的技术面、量价与主力资金只计一票 independent_cluster_count。",
            "   - E-02 仅使用上文显式 E-01 关系图折叠结果约束贡献；禁止根据关键词簇、分析师人数、"
            "供应商/来源差异或核验状态推断独立支持。PENDING/UNKNOWN claim 不得增加方向性支持。",
            1,
        ),
        (
            "需根据分析视角与本次研究档（horizon）与市场环境动态赋予权重，并按研究档明确区分核心裁决依据：",
            "不得为分析师或 claim 设定百分比、分值或加总比例，只按本次研究档（horizon）明确区分核心裁决依据"
            "（下列“侧重”仅表示裁决关注点，不产生方向性支持）：",
            1,
        ),
        ("技术面、资金面、情绪面高权重，", "裁决侧重技术面、资金面、情绪面，", 1),
        ("基本面、宏观与产业链权重高，", "裁决侧重基本面、宏观与产业链，", 1),
        (
            "趋势行情加权技术与资金，震荡市加权基本面与情绪，宏观大转折期加权宏观与产业链。",
            "趋势行情侧重技术与资金，震荡市侧重基本面与情绪，宏观大转折期侧重宏观与产业链"
            "（仅调整关注重点，不产生方向性支持）。",
            1,
        ),
    ),
    "en": (
        (
            "1) Tally independent evidence clusters (deduplicating claims by cluster_id) and compute "
            "cluster-based directional weight; analyst list serves as explanatory context only "
            "(analyst_count must not be used directly as independent voting weight).",
            "1) Use only the explicit E-01 relation reduction shown above for contribution grouping; "
            "do not infer independent support from keyword clusters, analyst count, provider/source "
            "differences, or verifier status. Pending/unknown claims must not add directional support.",
            1,
        ),
        (
            "2) Dynamically weight perspectives and distinguish core adjudication criteria by research horizon:",
            "2) Do not assign percentages or scores to analysts or claims; distinguish core adjudication "
            "criteria by research horizon (the focus below marks what is decisive and adds no directional support):",
            1,
        ),
        ("Primary weight on ", "Primary adjudication focus on ", 2),
    ),
}
_RELATION_PROMPT_FORBIDDEN = {
    "zh": re.compile(r"权重|加权|计票|cluster_id"),
    "en": re.compile(r"\bweight|\btally\b|\bvot(?:e|ing)|cluster_id", re.IGNORECASE),
}


def _resolve_relation_graph_context(
    state: Mapping[str, Any],
    investment_debate_state: Mapping[str, Any],
) -> tuple[Any, str, str]:
    """Locate an explicitly supplied E-01 graph, or mark the relation contribution as pending.

    The current graph pipeline has no relation producer.  This function therefore
    never derives edges from claim text, reports, event coverage, or verifier
    status; it only forwards a named E-01 graph payload.  Deserialization, E-01
    validation and the rejection audit (raw payload + structured error) happen in
    ``tally_cluster_votes`` so every manager path records the same evidence.

    Exactly one location may carry the graph.  More than one supplied payload
    (an explicit null included) is ambiguous: it is marked invalid with every
    payload kept for audit, instead of silently using whichever location is
    scanned first.
    """
    containers = (
        ("state", state),
        ("investment_debate_state", investment_debate_state),
        ("market_data_context", state.get("market_data_context")),
        ("event_coverage", state.get("event_coverage")),
    )
    supplied: list[tuple[str, Any]] = []
    for container_name, container in containers:
        if not isinstance(container, Mapping):
            continue
        for key in _RELATION_GRAPH_KEYS:
            if key in container:
                supplied.append((f"{container_name}.{key}", container.get(key)))

    if len(supplied) > 1:
        sources = [source for source, _ in supplied]
        return (
            dict(supplied),
            RELATION_GRAPH_STATUS_INVALID,
            f"multiple E-01 relation graph payloads supplied at {sources}; ambiguous relation input "
            "is not folded and claim contribution remains pending/unknown",
        )
    if supplied:
        source, raw_graph = supplied[0]
        if raw_graph is None:
            return (
                [],
                RELATION_GRAPH_STATUS_PENDING,
                f"E-01 relation graph at {source} is null; claim contribution remains pending/unknown",
            )
        return (
            raw_graph,
            RELATION_GRAPH_STATUS_AVAILABLE,
            f"explicit E-01 relation graph supplied at {source}",
        )

    return (
        [],
        RELATION_GRAPH_STATUS_PENDING,
        "E-01 relation graph is not produced in the current pipeline; claim contribution remains pending/unknown",
    )


def _apply_relation_prompt_guard(prompt_template: str, language: str) -> str:
    """Rewrite legacy keyword-cluster weighting instructions into E-02 contribution rules.

    Raises ``RelationPromptGuardError`` when the template drifts: each legacy segment
    must match the expected number of times and no weighting/tally instruction may
    survive the rewrite.
    """
    lang = "en" if language == "en" else "zh"
    guarded = prompt_template
    for legacy, replacement, expected in _RELATION_PROMPT_REWRITES[lang]:
        found = guarded.count(legacy)
        if found != expected:
            raise RelationPromptGuardError(
                f"{lang} legacy segment matched {found} time(s), expected {expected}: {legacy[:48]!r}"
            )
        guarded = guarded.replace(legacy, replacement)
    survivors = [
        line.strip()
        for line in guarded.splitlines()
        if _RELATION_PROMPT_FORBIDDEN[lang].search(line)
    ]
    if survivors:
        raise RelationPromptGuardError(
            f"{lang} weighting/tally instruction survived E-02 rewrite: {survivors[:3]}"
        )
    return guarded


def _format_relation_prompt_guard(relation_graph_status: str, language: str) -> str:
    """Final E-02 status line; the status comes from the reduction seam, not graph discovery."""
    status = str(relation_graph_status or RELATION_GRAPH_STATUS_PENDING).upper()
    if language == "en":
        return (
            "E-02 relation guard: status=" + status + ". Treat independence as UNKNOWN; "
            "the global contribution cap is conservative and no unconnected claim is an independent vote."
        )
    return (
        "E-02 关系贡献硬闸：状态=" + status + "。独立性恒为 UNKNOWN；遵守保守全局贡献上限，"
        "未被显式关系连接的 claim 不得作为独立票。"
    )


def extract_expectation_revisions_from_traces(
    traces: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Extract structured E-04 expectation_revision payloads from analyst_traces."""
    fund_er = None
    news_er = None
    for trace in (traces or []):
        if not isinstance(trace, Mapping):
            continue
        agent = trace.get("agent")
        er = trace.get("expectation_revision")
        if agent == "fundamentals_analyst" and isinstance(er, Mapping):
            fund_er = dict(er)
        elif agent == "news_analyst" and isinstance(er, Mapping):
            news_er = dict(er)

    if fund_er is None:
        fund_er = make_default_expectation_revision(
            event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_NOT_APPLICABLE
        )
    if news_er is None:
        news_er = make_default_expectation_revision(
            event_type=EVENT_TYPE_EVENT, status=STATUS_NOT_APPLICABLE
        )

    return {
        "fundamentals": make_json_safe(fund_er),
        "news": make_json_safe(news_er),
    }


def format_expectation_revisions_for_prompt(
    expectation_revisions: Mapping[str, Any],
    language: str = "zh",
) -> str:
    """Format structured E-04 expectation_revision context for research manager prompt."""
    fund_er = expectation_revisions.get("fundamentals") or {}
    news_er = expectation_revisions.get("news") or {}

    if language == "en":
        lines = [
            "- Structured Expectation Revision Context (E-04 Contract):",
            "  * Fundamentals Analyst (fundamentals_analyst):",
            f"    - status: {fund_er.get('status', 'not_applicable')}",
        ]
        act = fund_er.get("actual") or {}
        if act.get("value") is not None:
            lines.append(
                f"    - actual: {act.get('metric')}={act.get('value')}{act.get('unit')} "
                f"(period={act.get('report_period')}, as_of={act.get('as_of')})"
            )
        else:
            lines.append(f"    - actual: gap ({act.get('reason') or 'no verified metric'})")
        base = fund_er.get("baseline") or {}
        if base.get("type") and base.get("type") != "none":
            lines.append(
                f"    - baseline: {base.get('type')} source={base.get('source')} "
                f"{base.get('metric')}={base.get('value')}{base.get('unit')} (period={base.get('period')})"
            )
        else:
            lines.append("    - baseline: none (no historical baseline)")
        rev = fund_er.get("revision") or {}
        lines.append(
            f"    - revision: type={rev.get('type')} dir={rev.get('direction')} "
            f"val={rev.get('value')} pct={rev.get('percent')}% detail={rev.get('detail')}"
        )
        lines.append(
            f"    - priced_in: status={fund_er.get('priced_in', {}).get('status', 'unknown')}"
        )
        lines.append(
            f"    - double_count_guard: status={fund_er.get('double_count_guard', {}).get('status', 'unknown')} "
            f"(prevent_double_voting={fund_er.get('double_count_guard', {}).get('prevent_double_voting', True)})"
        )
        if fund_er.get("gaps"):
            lines.append(f"    - gaps: {', '.join(str(g) for g in fund_er.get('gaps', []))}")

        lines.append("  * News Analyst (news_analyst):")
        lines.append(f"    - status: {news_er.get('status', 'not_applicable')}")
        pub = news_er.get("publication") or {}
        lines.append(
            f"    - publication: time={pub.get('publish_time')} source={pub.get('source')} "
            f"hash={pub.get('source_hash')} qualification={pub.get('content_qualification')}"
        )
        n_act = news_er.get("actual") or {}
        lines.append(f"    - actual: {n_act.get('status', 'gap')} ({n_act.get('reason', 'no metrics extracted')})")
        lines.append(f"    - baseline: {news_er.get('baseline', {}).get('type', 'none')}")
        n_rev = news_er.get("revision") or {}
        lines.append(f"    - revision: type={n_rev.get('type')} detail={n_rev.get('detail')}")
        lines.append(f"    - priced_in: status={news_er.get('priced_in', {}).get('status', 'unknown')}")
        lines.append(
            f"    - double_count_guard: status={news_er.get('double_count_guard', {}).get('status', 'unknown')} "
            f"(prevent_double_voting={news_er.get('double_count_guard', {}).get('prevent_double_voting', True)})"
        )
        if news_er.get("gaps"):
            lines.append(f"    - gaps: {', '.join(str(g) for g in news_er.get('gaps', []))}")

        lines.append(
            "  * [E-04 Output Discipline] The manager consumes only structured expectation_revision fields; "
            "do not fabricate numbers from free text. When baseline is none, no percentage or numeric revision is allowed. "
            "Priced-in status without traceable evidence remains unknown. Double count guard prevents duplicate voting."
        )
        return "\n".join(lines)

    lines = [
        "- 预期修正分栏与事件结构化记录（E-04 契约）：",
        "  * 基本面分析师（fundamentals_analyst）：",
        f"    - 状态: {fund_er.get('status', 'not_applicable')}",
    ]
    act = fund_er.get("actual") or {}
    if act.get("value") is not None:
        lines.append(
            f"    - 实际数值 (actual): {act.get('metric')}={act.get('value')}{act.get('unit')} "
            f"（报告期={act.get('report_period')}，截至={act.get('as_of')}，来源={act.get('source')}）"
        )
    else:
        lines.append(f"    - 实际数值 (actual): 缺口 gap（{act.get('reason') or '未同时具备指标、数值、单位、报告期、截至日期五要素'}）")
    base = fund_er.get("baseline") or {}
    if base.get("type") and base.get("type") != "none":
        lines.append(
            f"    - 旧基线 (baseline): 类型={base.get('type')}，来源={base.get('source')}，"
            f"{base.get('metric')}={base.get('value')}{base.get('unit')}（期间={base.get('period')}）"
        )
    else:
        lines.append("    - 旧基线 (baseline): 无 none（无旧基线，严禁计算数值修正幅度）")
    rev = fund_er.get("revision") or {}
    lines.append(
        f"    - 修正分栏 (revision): 类型={rev.get('type')}，方向={rev.get('direction')}，"
        f"差额={rev.get('value')}，百分比={rev.get('percent')}%，详情={rev.get('detail')}"
    )
    lines.append(
        f"    - 已定价状态 (priced_in): {fund_er.get('priced_in', {}).get('status', 'unknown')} "
        f"（证据: {fund_er.get('priced_in', {}).get('evidence') or '无可回溯证据'}）"
    )
    lines.append(
        f"    - 防重复计入 (double_count_guard): 状态={fund_er.get('double_count_guard', {}).get('status', 'unknown')} "
        f"（prevent_double_voting={fund_er.get('double_count_guard', {}).get('prevent_double_voting', True)}）"
    )
    if fund_er.get("gaps"):
        lines.append(f"    - 数据缺口 (gaps): {', '.join(str(g) for g in fund_er.get('gaps', []))}")

    lines.append("  * 新闻事件分析师（news_analyst）：")
    lines.append(f"    - 状态: {news_er.get('status', 'not_applicable')}")
    pub = news_er.get("publication") or {}
    lines.append(
        f"    - 发布与资质 (publication): 发布时间={pub.get('publish_time')}，来源={pub.get('source')}，"
        f"哈希={pub.get('source_hash')}，正文资质={pub.get('content_qualification')}"
    )
    n_act = news_er.get("actual") or {}
    lines.append(f"    - 实际数值 (actual): {n_act.get('status', 'gap')}（{n_act.get('reason', '正文未抽取财务数字')}）")
    lines.append(f"    - 旧基线 (baseline): {news_er.get('baseline', {}).get('type', 'none')}")
    n_rev = news_er.get("revision") or {}
    lines.append(f"    - 修正分栏 (revision): 类型={n_rev.get('type')}，详情={n_rev.get('detail')}")
    lines.append(
        f"    - 已定价状态 (priced_in): {news_er.get('priced_in', {}).get('status', 'unknown')} "
        f"（无回溯证据时严禁断言为已定价事实）"
    )
    lines.append(
        f"    - 防重复计入 (double_count_guard): 状态={news_er.get('double_count_guard', {}).get('status', 'unknown')} "
        f"（prevent_double_voting={news_er.get('double_count_guard', {}).get('prevent_double_voting', True)}）"
    )
    if news_er.get("gaps"):
        lines.append(f"    - 数据缺口 (gaps): {', '.join(str(g) for g in news_er.get('gaps', []))}")

    lines.append(
        "  * 【E-04 纪律约束】经理只能引用已提供结构化栏位，严禁自行填数、重复计入或把 unknown 改成事实；"
        "缺少基线时不得输出数值修正幅度；正文未取得时公告存在不构成财务数字。"
    )
    return "\n".join(lines)


def validate_manager_expectation_revision_consumption(
    manager_verdict: Mapping[str, Any],
    raw_response: str,
    expectation_revisions: Any,
) -> tuple[bool, list[str]]:
    """Validate that research manager only consumed structured expectation_revision fields without hallucination (E-04).

    Guards full raw_response, reason, and structured fields:
    1. 'Priced in' cannot be asserted as fact without traceable evidence.
    2. 'Above/below expectations' cannot be asserted without comparable baseline.
    3. Financial metrics/numbers cannot be hallucinated when analyst reported gap/missing actual.
    4. Duplicate voting/double support cannot be claimed when double_count_guard is active.
    """
    violations: list[str] = []
    if isinstance(expectation_revisions, Sequence) and not isinstance(expectation_revisions, Mapping):
        fund_er = None
        news_er = None
        for item in expectation_revisions:
            if isinstance(item, Mapping):
                ev_type = item.get("event_type")
                if ev_type == EVENT_TYPE_FUNDAMENTAL:
                    fund_er = item
                elif ev_type in (EVENT_TYPE_EVENT, EVENT_TYPE_NONE):
                    news_er = item
        exp_dict = {"fundamentals": fund_er or {}, "news": news_er or {}}
    elif isinstance(expectation_revisions, Mapping):
        exp_dict = dict(expectation_revisions)
    else:
        exp_dict = {}

    fund_er = exp_dict.get("fundamentals") or {}
    news_er = exp_dict.get("news") or {}

    fund_base_type = (fund_er.get("baseline") or {}).get("type", "none")
    fund_base_val = (fund_er.get("baseline") or {}).get("value")

    fund_pi = (fund_er.get("priced_in") or {}).get("status", "unknown")
    news_pi = (news_er.get("priced_in") or {}).get("status", "unknown")

    # Combine all manager outputs: full raw response, verdict reason, and investment plan
    texts_to_check = [str(raw_response or ""), str(manager_verdict.get("reason") or "")]
    if manager_verdict.get("investment_plan"):
        texts_to_check.append(str(manager_verdict.get("investment_plan")))
    full_text = "\n".join(t for t in texts_to_check if t)

    # 1. Check if priced_in claimed as supported fact without traceable evidence
    if fund_pi != PRICED_IN_SUPPORTED and news_pi != PRICED_IN_SUPPORTED:
        has_pi_asserted = False
        for pat in ("已充分定价", "已完全定价", "市场已定价", "已基本定价", "股价已完全反映", "股价已充分反应", "市场已完全反映"):
            if pat in full_text:
                has_pi_asserted = True
                break
        if not has_pi_asserted:
            if re.search(r"(?<!未)(?<!尚未)(?<!不能确定)(?<!无法确认)已定价", full_text):
                has_pi_asserted = True
        if has_pi_asserted:
            violations.append("E-04 守卫拦截：缺乏可回溯证据，经理不得将“已定价”当作已确证事实引用")

    # 2. Check if beat/miss claimed without comparable baseline
    if fund_base_type == "none" or fund_base_val is None:
        for kw in ("超预期", "不及预期"):
            if kw in full_text:
                violations.append(f"E-04 守卫拦截：基本面无有效旧基线，经理不得在正文或裁决理由中断言业绩“{kw}”")
                break

    # 3. Check if financial numbers hallucinated when analyst reported gap
    fund_act_val = (fund_er.get("actual") or {}).get("value")
    if fund_act_val is None:
        m = re.search(
            r"(?:营业收入|营业总收入|主营业务收入|营收|净利润|归母净利润|毛利率)[^\d\n]{0,15}([0-9]+(?:\.[0-9]+)?\s*(?:亿元|万元|元|万|亿|%))",
            full_text,
        )
        if m:
            violations.append(f"E-04 守卫拦截：分析师未提供结构化实际财务数值，经理不得擅自断言财务指标数值（{m.group(0)}）")

    # 4. Check if double_count_guard is violated by claiming double voting / extra support
    fund_dc = (fund_er.get("double_count_guard") or {})
    news_dc = (news_er.get("double_count_guard") or {})
    fund_dc_prevent = bool(fund_dc.get("prevent_double_voting", True)) or fund_dc.get("status") in ("accounted_for", "unknown")
    news_dc_prevent = bool(news_dc.get("prevent_double_voting", True)) or news_dc.get("status") in ("accounted_for", "unknown")

    if fund_dc_prevent or news_dc_prevent:
        for dc_pat in ("双重支持", "额外支持", "双重加票", "额外加票", "两项独立票", "重复计入", "双重印证加票"):
            if dc_pat in full_text:
                violations.append(f"E-04 守卫拦截：double_count_guard 生效，已计入或未确证事件不得作为额外支持再次加票/计入（命中“{dc_pat}”）")
                break

    return len(violations) == 0, violations


def apply_manager_double_count_guard(
    claim_cluster_metrics: dict[str, Any],
    expectation_revisions: Any,
    claims: Sequence[Mapping[str, Any]] | None = None,
    manager_verdict: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """Enforce double_count_guard in research manager consumption gate (E-04 Defect 5).

    When double_count_guard status is accounted_for or unknown (prevent_double_voting=True):
    1. Blocks duplicate voting from overlapping/accounted-for events in claim_cluster_metrics.
    2. Strips duplicate event claims from manager_verdict['adopted_claim_ids'] into excluded_evidence.
    3. Records structured audit metadata in metrics.
    """
    if isinstance(expectation_revisions, Sequence) and not isinstance(expectation_revisions, Mapping):
        fund_er = None
        news_er = None
        for item in expectation_revisions:
            if isinstance(item, Mapping):
                ev_type = item.get("event_type")
                if ev_type == EVENT_TYPE_FUNDAMENTAL:
                    fund_er = item
                elif ev_type in (EVENT_TYPE_EVENT, EVENT_TYPE_NONE):
                    news_er = item
        exp_dict = {"fundamentals": fund_er or {}, "news": news_er or {}}
    elif isinstance(expectation_revisions, Mapping):
        exp_dict = dict(expectation_revisions)
    else:
        exp_dict = {}

    fund_er = exp_dict.get("fundamentals") or {}
    news_er = exp_dict.get("news") or {}

    fund_dc = (fund_er.get("double_count_guard") or {})
    news_dc = (news_er.get("double_count_guard") or {})
    fund_st = fund_dc.get("status", "unknown")
    news_st = news_dc.get("status", "unknown")

    prevent_fund = bool(fund_dc.get("prevent_double_voting", True)) or fund_st in ("accounted_for", "unknown")
    prevent_news = bool(news_dc.get("prevent_double_voting", True)) or news_st in ("accounted_for", "unknown")
    is_active = prevent_fund or prevent_news

    metrics = dict(claim_cluster_metrics or {})
    if not is_active:
        metrics["double_count_guard_active"] = False
        return metrics, manager_verdict, []

    metrics["double_count_guard_active"] = True
    metrics["duplicate_voting_prevented"] = True

    # Count how many extra votes/claims are duplicates
    blocked_count = 0
    claims_list = list(claims or [])
    if claims_list:
        event_claims = [
            c for c in claims_list
            if str(c.get("event_type", "")).lower() in ("event", "fundamental")
            or any(kw in str(c.get("claim_text") or c.get("text") or "") for kw in ("预告", "预测", "快报", "业绩", "财报"))
        ]
        if len(event_claims) > 1:
            blocked_count = len(event_claims) - 1

    if blocked_count == 0 and metrics.get("independent_cluster_count", 0) > 1:
        blocked_count = 1

    if blocked_count > 0:
        orig_indep = metrics.get("independent_cluster_count", 0)
        metrics["independent_cluster_count"] = max(1, orig_indep - blocked_count)
        if metrics.get("bull_cluster_count", 0) > 1:
            metrics["bull_cluster_count"] = max(1, metrics["bull_cluster_count"] - blocked_count)
        elif metrics.get("bear_cluster_count", 0) > 1:
            metrics["bear_cluster_count"] = max(1, metrics["bear_cluster_count"] - blocked_count)

    metrics["double_count_guard_audit"] = {
        "status": "blocked",
        "reason": "accounted_for 或 unknown 时不得把同一事件作为额外支持/票再次计入",
        "prevent_double_voting": True,
        "blocked_duplicate_votes": blocked_count,
    }

    if manager_verdict and isinstance(manager_verdict, dict):
        adopted = list(manager_verdict.get("adopted_claim_ids") or [])
        if len(adopted) > 1 and blocked_count > 0:
            num_to_strip = min(blocked_count, len(adopted) - 1)
            stripped_ids = adopted[-num_to_strip:]
            manager_verdict["adopted_claim_ids"] = adopted[:-num_to_strip]
            excluded = list(manager_verdict.get("excluded_evidence") or [])
            for cid in stripped_ids:
                excluded.append({
                    "claim_id": cid,
                    "reason": "double_count_guard: 同一事件已被既有预测/事件栏位计入，阻止重复加票",
                })
            manager_verdict["excluded_evidence"] = excluded

    return metrics, manager_verdict, []


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
        val = state.get("horizon")
        if val and isinstance(val, str) and val.strip():
            return val.strip()
        metadata = state.get("horizon_run_metadata")
        if isinstance(metadata, dict):
            resolved = metadata.get("resolved")
            if resolved and isinstance(resolved, list) and len(resolved) > 0 and resolved[0]:
                return str(resolved[0]).strip()
            requested = metadata.get("requested")
            if requested and isinstance(requested, list) and len(requested) > 0 and requested[0]:
                return str(requested[0]).strip()
    bound = get_bound_research_horizon()
    if bound and isinstance(bound, str) and bound.strip():
        return bound.strip()
    return "short"


def _blocked_manager_payload(
    *,
    investment_debate_state: dict,
    report_manifest: dict,
    fund_flow_guard: dict,
    decision_status: dict,
    blocked_plan: str,
    manager_reason: str,
    run_integrity: dict | None = None,
    claim_evidence_summary: dict | None = None,
    consistency_check_passed: bool = True,
    failed_checks: list | None = None,
    evidence_verification: list | None = None,
    claim_cluster_metrics: dict | None = None,
    research_horizon: str = "short",
    reports: Mapping[str, str] | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    relation_graph: Any = None,
    relation_graph_status: str | None = None,
    relation_graph_reason: str | None = None,
    expectation_revision: Mapping[str, Any] | None = None,
) -> dict:
    """Shared early-return shape for INVALID/ABSTAIN manager short-circuits."""
    if claim_cluster_metrics is None:
        claim_cluster_metrics = tally_cluster_votes(
            claims=investment_debate_state.get("claims", []),
            reports=reports,
            claims_verification=claims_verification,
            relation_graph=relation_graph,
            relation_graph_status=relation_graph_status,
            relation_graph_reason=relation_graph_reason,
        )
    if expectation_revision:
        claim_cluster_metrics, _, _ = apply_manager_double_count_guard(
            claim_cluster_metrics=claim_cluster_metrics,
            expectation_revisions=expectation_revision,
            claims=investment_debate_state.get("claims", []),
        )
    summary_dict = dict(claim_evidence_summary) if isinstance(claim_evidence_summary, dict) else {}
    if not summary_dict and isinstance(investment_debate_state.get("claim_evidence_summary"), dict):
        summary_dict = dict(investment_debate_state["claim_evidence_summary"])
    ev_list = list(evidence_verification if evidence_verification is not None else (claims_verification or []))
    manager_verdict = {
        "direction": DIRECTION_NA,
        "winner": "tie",
        "reason": manager_reason,
        "position_pct": 0,
        "entry": None,
        "target": None,
        "stop_loss": None,
        "upside": None,
        "downside": None,
        "odds": None,
        "adopted_claim_ids": [],
        "partially_adopted_claims": [],
        "rejected_claim_ids": [],
        "excluded_evidence": [],
        "claim_evidence_summary": summary_dict,
        "consistency_check_passed": consistency_check_passed,
        "failed_checks": list(failed_checks or []),
        "decision_status": decision_status,
        "analysis_status": decision_status.get("analysis_status"),
        "trade_action": decision_status.get("trade_action", ACTION_NO_TRADE),
        "risk_status": decision_status.get("risk_status"),
        "confirmation_state": decision_status.get("confirmation_state", "UNRESOLVED"),
        "horizon": research_horizon,
        "research_horizon": research_horizon,
        "evidence_relation_status": claim_cluster_metrics.get("relation_graph_status", "pending"),
        "evidence_relation_reason": claim_cluster_metrics.get("relation_graph_reason", ""),
        "expectation_revision": dict(expectation_revision or {}),
    }
    payload = {
        "fund_flow_consensus_guard": fund_flow_guard,
        "investment_plan": blocked_plan,
        "manager_verdict": manager_verdict,
        "evidence_verification": ev_list,
        "claim_evidence_summary": summary_dict,
        "report_manifest": report_manifest,
        "decision_status": decision_status,
        "analysis_status": decision_status.get("analysis_status"),
        "trade_action": decision_status.get("trade_action", ACTION_NO_TRADE),
        "risk_status": decision_status.get("risk_status"),
        "confirmation_state": decision_status.get("confirmation_state", "UNRESOLVED"),
        "final_trade_decision": blocked_plan,
        "investment_debate_state": {
            **investment_debate_state,
            "judge_decision": blocked_plan,
            "current_response": blocked_plan,
            "manager_verdict": manager_verdict,
            "expectation_revision": dict(expectation_revision or {}),
            "evidence_verification": ev_list,
            "claim_evidence_summary": summary_dict,
            "report_manifest": report_manifest,
            "claim_cluster_metrics": claim_cluster_metrics,
            "evidence_relation_status": claim_cluster_metrics.get("relation_graph_status", "pending"),
            "evidence_relation_reduction": claim_cluster_metrics.get("relation_audit", {}),
            "independent_cluster_count": claim_cluster_metrics.get("independent_cluster_count", 0),
            "analyst_count": claim_cluster_metrics.get("analyst_count", 0),
            "verified_evidence_count": claim_cluster_metrics.get("verified_evidence_count", 0),
        },
    }
    if run_integrity is not None:
        payload["run_integrity"] = run_integrity
    return payload


def create_research_manager(llm, memory, custom_prompt: str = "", placement: Placement = DEFAULT_PLACEMENT):
    async def research_manager_node(state) -> dict:
        research_horizon = _resolve_research_horizon(state)
        analyst_traces = state.get("analyst_traces") or []
        expectation_revisions = extract_expectation_revisions_from_traces(analyst_traces)
        config = get_config()
        prompt_language = _resolve_language(config)

        user_intent = state.get("user_intent") or {}
        focus_areas = user_intent.get("focus_areas", [])
        specific_questions = user_intent.get("specific_questions", [])

        history = state["investment_debate_state"].get("history", "")
        macro_report = state.get("macro_report", "")
        market_research_report = state.get("market_report", "")
        sentiment_report = state.get("sentiment_report", "")
        news_report = state.get("news_report", "")
        fundamentals_report = state.get("fundamentals_report", "")
        smart_money_report = state.get("smart_money_report", "")
        volume_price_report = state.get("volume_price_report", "")
        fund_flow_guard = state.get("fund_flow_consensus_guard") or {
            "blocked": True,
            "direction_allowed": False,
            "status": "not_checked",
        }
        market_data_context = state.get("market_data_context") or {}
        analysis_baseline_date = (
            state.get("trade_date")
            or state.get("analysis_baseline_date")
            or (market_data_context.get("analysis_baseline_date") if isinstance(market_data_context, dict) else "")
            or (market_data_context.get("trade_date") if isinstance(market_data_context, dict) else "")
            or (market_data_context.get("data_as_of") if isinstance(market_data_context, dict) else "")
            or ""
        )
        expected_symbol = (
            (market_data_context.get("symbol") if isinstance(market_data_context, dict) else None)
            or state.get("symbol")
            or state.get("ticker")
        )
        symbol_val = expected_symbol
        effective_market_data_context = dict(market_data_context) if isinstance(market_data_context, dict) else {}
        if analysis_baseline_date and not effective_market_data_context.get("analysis_baseline_date"):
            effective_market_data_context["analysis_baseline_date"] = analysis_baseline_date
        if expected_symbol and not effective_market_data_context.get("symbol"):
            effective_market_data_context["symbol"] = expected_symbol
        data_gaps = state.get("data_gaps") or (market_data_context.get("data_gaps") if isinstance(market_data_context, dict) else []) or []

        investment_debate_state = state["investment_debate_state"]
        claims = investment_debate_state.get("claims", [])
        unresolved_claim_ids = investment_debate_state.get("unresolved_claim_ids", [])
        round_summary = investment_debate_state.get("round_summary", "")
        relation_graph, relation_graph_status, relation_graph_reason = _resolve_relation_graph_context(
            state,
            investment_debate_state,
        )

        curr_situation = (
            f"{macro_report}\n\n"
            f"{market_research_report}\n\n"
            f"{sentiment_report}\n\n"
            f"{news_report}\n\n"
            f"{fundamentals_report}\n\n"
            f"{smart_money_report}\n\n"
            f"{volume_price_report}"
        )
        past_memories = memory.get_memories(curr_situation, n_matches=2)

        past_memory_str = ""
        for i, rec in enumerate(past_memories, 1):
            past_memory_str += rec["recommendation"] + "\n\n"

        claims_text = format_claims_for_prompt(claims)
        unresolved_claims_text = format_claim_subset_for_prompt(claims, unresolved_claim_ids)
        round_summary_text = round_summary or "暂无轮次摘要。"

        # ── Seven Reports Input Extraction & Manifest ──────────────────────
        macro_input, macro_mode, macro_chars = build_dense_report_input(macro_report, max_chars=1800, role_name="macro")
        market_input, market_mode, market_chars = build_dense_report_input(market_research_report, max_chars=1800, role_name="market")
        sentiment_input, sentiment_mode, sentiment_chars = build_dense_report_input(sentiment_report, max_chars=1800, role_name="sentiment")
        news_input, news_mode, news_chars = build_dense_report_input(news_report, max_chars=1800, role_name="news")
        fundamentals_input, fundamentals_mode, fundamentals_chars = build_dense_report_input(fundamentals_report, max_chars=1800, role_name="fundamentals")
        smart_money_input, smart_money_mode, smart_money_chars = build_dense_report_input(smart_money_report, max_chars=1800, role_name="smart_money")
        volume_price_input, volume_price_mode, volume_price_chars = build_dense_report_input(volume_price_report, max_chars=1800, role_name="volume_price")

        seven_reports = {
            "macro_report": macro_report,
            "market_report": market_research_report,
            "sentiment_report": sentiment_report,
            "news_report": news_report,
            "fundamentals_report": fundamentals_report,
            "smart_money_report": smart_money_report,
            "volume_price_report": volume_price_report,
        }

        pass_info = {
            "macro_report": (macro_mode, macro_chars),
            "market_report": (market_mode, market_chars),
            "sentiment_report": (sentiment_mode, sentiment_chars),
            "news_report": (news_mode, news_chars),
            "fundamentals_report": (fundamentals_mode, fundamentals_chars),
            "smart_money_report": (smart_money_mode, smart_money_chars),
            "volume_price_report": (volume_price_mode, volume_price_chars),
        }
        report_manifest = build_debate_report_manifest(seven_reports, pass_info=pass_info)

        # ── D-015 Layer 3: Research manager assembly fail-closed guard ───────────
        if custom_prompt:
            custom_linter_res = lint_custom_prompt(custom_prompt)
            if custom_linter_res.verdict in (PromptGuardVerdict.VIOLATION, PromptGuardVerdict.AMBIGUOUS):
                prompt_hash = hashlib.sha256(custom_prompt.encode("utf-8")).hexdigest()[:12]
                guard_failure = (
                    f"E-02 自定义提示词守卫未通过：判定为 {custom_linter_res.verdict.value}"
                    f"（{custom_linter_res.detail}）"
                )
                _logger.error(
                    "[research_manager] E-02 custom prompt guard intercepted: verdict=%s reason=%s hash=%s",
                    custom_linter_res.verdict.value,
                    custom_linter_res.reason_code,
                    prompt_hash,
                )
                from tradingagents.agents.utils.decision_status import abstain_status

                decision_status = abstain_status(
                    reason_codes=[
                        "e02_custom_prompt_guard_failed",
                        "e02_relation_prompt_guard_failed",
                        f"prompt_hash:{prompt_hash}",
                    ],
                    trade_action="NO_TRADE",
                    risk_status="BLOCKED",
                ).to_dict()
                blocked_plan = f"{guard_failure}。状态=ABSTAIN，动作=NO_TRADE；已阻断进入 Trader 执行阶段。"
                tracker = current_tracker_var.get()
                if tracker:
                    tracker.emit_debate_message(
                        debate="research", agent="Research Manager",
                        round_num=-1, content=blocked_plan, is_verdict=True,
                    )
                return _blocked_manager_payload(
                    investment_debate_state=investment_debate_state,
                    report_manifest=report_manifest,
                    fund_flow_guard=fund_flow_guard,
                    decision_status=decision_status,
                    blocked_plan=blocked_plan,
                    manager_reason=guard_failure,
                    run_integrity={},
                    consistency_check_passed=False,
                    failed_checks=[guard_failure],
                    research_horizon=research_horizon,
                    reports=seven_reports,
                    relation_graph=relation_graph,
                    relation_graph_status=relation_graph_status,
                    relation_graph_reason=relation_graph_reason,
                    expectation_revision=expectation_revisions,
                )

        # ── P0-1: Run integrity before any Neutral/HOLD collapse ────────────
        run_integrity = evaluate_state_integrity(state)
        if run_integrity.all_required_failed and run_integrity.decision_status:
            blocked_plan = (
                "运行完整性判定：必需分析师报告全部失败（INVALID_RUN/DATA_ERROR）。"
                "不得输出方向、概率、百分比区间或 Neutral/HOLD 观点；执行动作 NO_TRADE。"
                f" 失败角色={','.join(run_integrity.failed_required)}"
            )
            _logger.warning(
                "[research_manager] run integrity INVALID: %s",
                run_integrity.reason_codes,
            )
            return _blocked_manager_payload(
                investment_debate_state=investment_debate_state,
                report_manifest=report_manifest,
                fund_flow_guard=fund_flow_guard,
                decision_status=run_integrity.decision_status,
                blocked_plan=blocked_plan,
                manager_reason="必需分析师上游全部失败",
                run_integrity=run_integrity.to_dict(),
                consistency_check_passed=False,
                failed_checks=list(run_integrity.reason_codes),
                research_horizon=research_horizon,
                reports=seven_reports,
                relation_graph=relation_graph,
                relation_graph_status=relation_graph_status,
                relation_graph_reason=relation_graph_reason,
                expectation_revision=expectation_revisions,
            )

        # ── Provenance & Data Failure Context ──────────────────────────────
        prov_lines = [f"- 基准分析日期 (analysis_baseline_date): {analysis_baseline_date or '未明确指定'}"]
        source_provenance = market_data_context.get("source_provenance") if isinstance(market_data_context, dict) else {}
        if isinstance(source_provenance, dict) and source_provenance:
            prov_lines.append("- 数据源可用状态 (source_provenance):")
            for src, info in source_provenance.items():
                st = info.get("status", "unknown") if isinstance(info, dict) else str(info)
                prov_lines.append(f"  * {src}: {st}")
        failure_ledger = market_data_context.get("data_failure_ledger") if isinstance(market_data_context, dict) else []
        if isinstance(failure_ledger, list) and failure_ledger:
            prov_lines.append("- 数据失败账本 (data_failure_ledger - 严禁采纳以下不可用/失败指标):")
            for entry in failure_ledger:
                if isinstance(entry, dict):
                    prov_lines.append(f"  * [UNAVAILABLE] {entry.get('source', '')}: {entry.get('reason', entry.get('status', 'failed'))}")

        social_data_context = state.get("social_data_context") or {}
        if isinstance(social_data_context, dict) and social_data_context:
            social_mode = social_data_context.get("mode", "disabled")
            social_status = social_data_context.get("status", "unknown")
            social_dir_allowed = bool(social_data_context.get("direction_allowed", False))
            social_reasons = social_data_context.get("reason_codes", [])
            bundle = social_data_context.get("bundle") if isinstance(social_data_context.get("bundle"), dict) else {}
            bundle_id = (bundle.get("bundle_id") if isinstance(bundle, dict) else None) or social_data_context.get("bundle_id") or "none"

            reasons_str = f" ({', '.join(str(r) for r in social_reasons)})" if social_reasons else ""
            prov_lines.append(
                f"- 社交数据状态 (social_data_context): mode={social_mode}, status={social_status}, "
                f"direction_allowed={social_dir_allowed}, bundle_id={bundle_id}{reasons_str}"
            )
            if not social_dir_allowed:
                prov_lines.append(
                    f"  * [DIRECTION_GUARD] 明确禁止：当前社交数据 direction_allowed=false（状态: {social_status}{reasons_str}），"
                    f"严禁将社交分数、散户情绪得分或讨论热度作为多空方向证据或交易依据！"
                )

            social_ledger = social_data_context.get("data_failure_ledger")
            if isinstance(social_ledger, list) and social_ledger:
                for entry in social_ledger:
                    if isinstance(entry, dict):
                        prov_lines.append(f"  * [UNAVAILABLE] {entry.get('source', 'social_archive')}: {entry.get('reason', entry.get('status', 'failed'))}")

        if data_gaps:
            prov_lines.append(f"- 已知数据缺口 (data_gaps): {', '.join(str(g) for g in data_gaps)}")
        prov_lines.append(format_expectation_revisions_for_prompt(expectation_revisions, language=prompt_language))
        provenance_context = "\n".join(prov_lines)

        market_evidence_summary = build_evidence_summary(market_research_report)
        news_evidence_summary = build_evidence_summary(news_report)
        fundamentals_evidence_summary = build_evidence_summary(fundamentals_report)
        macro_evidence_summary = build_evidence_summary(macro_report)

        macro_evidence_line = ""
        if macro_evidence_summary:
            label = (
                "宏观/板块证据摘要："
                if prompt_language == "zh"
                else "Macro/sector evidence summary: "
            )
            macro_evidence_line = f"{label}{macro_evidence_summary}"

        if fund_flow_guard.get("blocked") or not fund_flow_guard.get("direction_allowed"):
            decision_status = fund_flow_guard_abstain_status(fund_flow_guard).to_dict()
            blocked_plan = (
                "资金流来源选择 guard 已阻断：不得输出增持、减持、吸筹或其他方向性投资计划。"
                "状态=ABSTAIN，动作=NO_TRADE（不是 Neutral/HOLD 观点）。"
            )
            return _blocked_manager_payload(
                investment_debate_state=investment_debate_state,
                report_manifest=report_manifest,
                fund_flow_guard=fund_flow_guard,
                decision_status=decision_status,
                blocked_plan=blocked_plan,
                manager_reason="资金流来源选择 guard 已阻断",
                run_integrity=run_integrity.to_dict(),
                consistency_check_passed=True,
                research_horizon=research_horizon,
                reports=seven_reports,
                relation_graph=relation_graph,
                relation_graph_status=relation_graph_status,
                relation_graph_reason=relation_graph_reason,
                expectation_revision=expectation_revisions,
            )

        # ── 辩论前置硬闸检查 (Debate Pre-Gate Hard Gate - fail-closed before LLM) ──
        gate_errors = validate_debate_preconditions(investment_debate_state, claims=claims)
        if gate_errors:
            failed_reasons = "; ".join(f"辩论前置硬闸未通过: {err}" for err in gate_errors)
            _logger.warning("[research_manager] debate pre-gate check failed: %s", failed_reasons)
            blocked_plan = (
                f"研究总监裁决自洽硬闸未通过：{failed_reasons}。"
                "状态=ABSTAIN，动作=NO_TRADE；已阻断进入 Trader 执行阶段。"
            )
            truth_evaluator = EvidenceFactualTruthEvaluator()
            claims_verification = truth_evaluator.evaluate_claims(
                claims=claims,
                seven_reports=seven_reports,
                market_data_context=effective_market_data_context,
                analysis_baseline_date=analysis_baseline_date,
                social_data_context=social_data_context,
            )
            claim_evidence_summary = truth_evaluator.aggregate_claim_evidence(
                claims=claims,
                claims_verification=claims_verification,
                analysis_baseline_date=analysis_baseline_date or None,
                expected_symbol=expected_symbol or None,
                market_data_context=effective_market_data_context,
            )
            from tradingagents.agents.utils.decision_status import abstain_status

            decision_status = abstain_status(
                reason_codes=[f"debate_pre_gate:{err}" for err in gate_errors],
                trade_action="NO_TRADE",
                risk_status="BLOCKED",
            ).to_dict()
            tracker = current_tracker_var.get()
            if tracker:
                tracker.emit_debate_message(
                    debate="research", agent="Research Manager",
                    round_num=-1, content=blocked_plan, is_verdict=True,
                )
            payload = _blocked_manager_payload(
                investment_debate_state=investment_debate_state,
                report_manifest=report_manifest,
                fund_flow_guard=fund_flow_guard,
                decision_status=decision_status,
                blocked_plan=blocked_plan,
                manager_reason=f"辩论前置硬闸未通过: {failed_reasons}",
                run_integrity=run_integrity.to_dict(),
                claim_evidence_summary=claim_evidence_summary,
                consistency_check_passed=False,
                failed_checks=[f"辩论前置硬闸未通过: {err}" for err in gate_errors],
                evidence_verification=claims_verification,
                research_horizon=research_horizon,
                reports=seven_reports,
                relation_graph=relation_graph,
                relation_graph_status=relation_graph_status,
                relation_graph_reason=relation_graph_reason,
                expectation_revision=expectation_revisions,
            )
            # Preserve pre-gate debate bookkeeping fields
            debate_state = payload["investment_debate_state"]
            debate_state.update(
                {
                    "history": investment_debate_state.get("history", ""),
                    "bear_history": investment_debate_state.get("bear_history", ""),
                    "bull_history": investment_debate_state.get("bull_history", ""),
                    "current_speaker": investment_debate_state.get("current_speaker", ""),
                    "count": investment_debate_state.get("count", 0),
                    "claims": claims,
                    "round_messages": investment_debate_state.get("round_messages", []),
                    "focus_claim_ids": investment_debate_state.get("focus_claim_ids", []),
                    "open_claim_ids": investment_debate_state.get("open_claim_ids", []),
                    "resolved_claim_ids": investment_debate_state.get("resolved_claim_ids", []),
                    "unresolved_claim_ids": unresolved_claim_ids,
                    "round_summary": round_summary,
                    "round_goal": investment_debate_state.get("round_goal", ""),
                    "claim_counter": investment_debate_state.get("claim_counter", 0),
                    "claim_evidence_summary": claim_evidence_summary,
                    "evidence_verification": claims_verification,
                }
            )
            return payload

        # ── 事实核验与 Claim 证据链聚合 (Fact Checking & Claim Evidence Aggregation) ──
        truth_evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = truth_evaluator.evaluate_claims(
            claims=claims,
            seven_reports=seven_reports,
            market_data_context=effective_market_data_context,
            analysis_baseline_date=analysis_baseline_date,
            social_data_context=social_data_context,
        )
        claim_evidence_summary = truth_evaluator.aggregate_claim_evidence(
            claims=claims,
            claims_verification=claims_verification,
            analysis_baseline_date=analysis_baseline_date or None,
            expected_symbol=expected_symbol or None,
            market_data_context=effective_market_data_context,
        )

        claims = cluster_claims(
            claims,
            symbol=symbol_val,
            date=analysis_baseline_date,
            claims_verification=claims_verification,
        )
        claim_cluster_metrics = tally_cluster_votes(
            claims=claims,
            reports=seven_reports,
            claims_verification=claims_verification,
            symbol=symbol_val,
            trade_date=analysis_baseline_date,
            relation_graph=relation_graph,
            relation_graph_status=relation_graph_status,
            relation_graph_reason=relation_graph_reason,
        )
        claim_cluster_metrics, _, _ = apply_manager_double_count_guard(
            claim_cluster_metrics=claim_cluster_metrics,
            expectation_revisions=expectation_revisions,
            claims=claims,
        )

        claims_text = format_claims_with_verification_for_prompt(
            claims=claims,
            claims_verification=claims_verification,
            claim_evidence_summary=claim_evidence_summary,
        )
        cluster_summary = format_claim_cluster_summary_for_prompt(
            claim_cluster_metrics,
            language=_resolve_language(get_config()),
        )
        if cluster_summary:
            claims_text = f"{cluster_summary}\n\n{claims_text}"
        unresolved_claims_subset = [c for c in claims if str(c.get("claim_id", "")).strip() in set(unresolved_claim_ids)]
        unresolved_claims_text = format_claims_with_verification_for_prompt(
            claims=unresolved_claims_subset,
            claims_verification=claims_verification,
            claim_evidence_summary=claim_evidence_summary,
            empty_message="当前没有未决 claim。",
        )

        # ── Parameterize actual messages, stages, challenges, and battlefield coverage ──
        round_messages = investment_debate_state.get("round_messages", [])
        actual_message_count = len(round_messages) if round_messages else safe_int(investment_debate_state.get("count", 0), 0)

        stages_list = [
            str(m.get("stage") or m.get("protocol_stage") or "").strip()
            for m in round_messages
            if m.get("stage") or m.get("protocol_stage")
        ]
        unique_stages = list(dict.fromkeys([s for s in stages_list if s]))
        if not unique_stages:
            unique_stages = ["opening", "challenge"]
        actual_stages_desc = f"覆盖阶段: {', '.join(unique_stages)}"

        is_tb_skipped = bool(investment_debate_state.get("tiebreak_skipped", False))
        if is_tb_skipped:
            tiebreak_status_desc = "已跳过加赛(证据足以裁决)"
        elif "tiebreak" in unique_stages:
            tiebreak_status_desc = "已执行加赛"
        else:
            tiebreak_status_desc = "标准流程"

        challenges = investment_debate_state.get("challenges", [])
        challenges_verification = truth_evaluator.evaluate_challenges(
            challenges=challenges,
            seven_reports=seven_reports,
            market_data_context=effective_market_data_context,
            analysis_baseline_date=analysis_baseline_date,
            social_data_context=social_data_context,
        )
        challenges_text = format_challenges_for_prompt(
            challenges=challenges,
            challenge_verification=challenges_verification,
        )
        challenge_verification_text = format_challenge_verification_summary(
            challenges=challenges,
            challenge_verification=challenges_verification,
        )
        battlefield_coverage_text = format_battlefield_coverage(claims)

        config = get_config()
        horizon_ctx = build_horizon_context(
            research_horizon,
            focus_areas,
            specific_questions,
            agent_type="research_manager",
            research_horizon=research_horizon,
        )

        # D-015 Contract 2 & Layer 3: Research manager assembly fail-closed guard
        if custom_prompt:
            custom_linter_res = lint_custom_prompt(custom_prompt)
            if custom_linter_res.verdict in (PromptGuardVerdict.VIOLATION, PromptGuardVerdict.AMBIGUOUS):
                prompt_hash = hashlib.sha256(custom_prompt.encode("utf-8")).hexdigest()[:12]
                guard_failure = (
                    f"E-02 自定义提示词守卫未通过：判定为 {custom_linter_res.verdict.value}"
                    f"（{custom_linter_res.detail}）"
                )
                _logger.error(
                    "[research_manager] E-02 custom prompt guard intercepted: verdict=%s reason=%s hash=%s",
                    custom_linter_res.verdict.value,
                    custom_linter_res.reason_code,
                    prompt_hash,
                )
                from tradingagents.agents.utils.decision_status import abstain_status

                return _blocked_manager_payload(
                    investment_debate_state=investment_debate_state,
                    report_manifest=report_manifest,
                    fund_flow_guard=fund_flow_guard,
                    decision_status=abstain_status(
                        reason_codes=[
                            "e02_custom_prompt_guard_failed",
                            "e02_relation_prompt_guard_failed",
                            f"prompt_hash:{prompt_hash}",
                        ],
                        trade_action="NO_TRADE",
                        risk_status="BLOCKED",
                    ).to_dict(),
                    blocked_plan=f"{guard_failure}。状态=ABSTAIN，动作=NO_TRADE；已阻断进入 Trader 执行阶段。",
                    manager_reason=guard_failure,
                    run_integrity=run_integrity.to_dict() if hasattr(run_integrity, "to_dict") else (run_integrity or {}),
                    claim_evidence_summary=claim_evidence_summary,
                    consistency_check_passed=False,
                    failed_checks=[guard_failure],
                    evidence_verification=claims_verification,
                    claim_cluster_metrics=claim_cluster_metrics,
                    research_horizon=research_horizon,
                    expectation_revision=expectation_revisions,
                )

        injection_slots = build_injection_slots(custom_prompt, placement, role_key="research_manager")
        prompt_language = _resolve_language(config)
        try:
            prompt_template = _apply_relation_prompt_guard(
                get_prompt("research_manager_prompt", config=config),
                language=prompt_language,
            )
        except RelationPromptGuardError as exc:
            guard_failure = f"E-02 关系提示词守卫未通过：{exc}"
            _logger.error("[research_manager] %s", guard_failure)
            from tradingagents.agents.utils.decision_status import abstain_status

            return _blocked_manager_payload(
                investment_debate_state=investment_debate_state,
                report_manifest=report_manifest,
                fund_flow_guard=fund_flow_guard,
                decision_status=abstain_status(
                    reason_codes=["e02_relation_prompt_guard_failed"],
                    trade_action="NO_TRADE",
                    risk_status="BLOCKED",
                ).to_dict(),
                blocked_plan=f"{guard_failure}。状态=ABSTAIN，动作=NO_TRADE；已阻断进入 Trader 执行阶段。",
                manager_reason=guard_failure,
                run_integrity=run_integrity.to_dict(),
                claim_evidence_summary=claim_evidence_summary,
                consistency_check_passed=False,
                failed_checks=[guard_failure],
                evidence_verification=claims_verification,
                claim_cluster_metrics=claim_cluster_metrics,
                research_horizon=research_horizon,
                expectation_revision=expectation_revisions,
            )
        base_prompt = prompt_template.format(
            past_memory_str=past_memory_str,
            provenance_context=provenance_context,
            history=history,
            smart_money_report=smart_money_input,
            volume_price_report=volume_price_input,
            sentiment_report=sentiment_input,
            market_evidence_summary=market_evidence_summary,
            news_evidence_summary=news_evidence_summary,
            fundamentals_evidence_summary=fundamentals_evidence_summary,
            macro_evidence_line=macro_evidence_line,
            claims_text=claims_text,
            unresolved_claims_text=unresolved_claims_text,
            round_summary=round_summary_text,
            actual_message_count=actual_message_count,
            actual_stages_desc=actual_stages_desc,
            tiebreak_status_desc=tiebreak_status_desc,
            challenges_text=challenges_text,
            challenge_verification_text=challenge_verification_text,
            battlefield_coverage_text=battlefield_coverage_text,
            **injection_slots,
        )
        relation_guard = _format_relation_prompt_guard(
            claim_cluster_metrics.get("relation_graph_status", RELATION_GRAPH_STATUS_PENDING),
            prompt_language,
        )
        base_prompt = f"{base_prompt}\n\n{relation_guard}"
        prompt = f"{horizon_ctx}\n\n{base_prompt}"

        _logger.info(
            "[research_manager] prompt size: total=%d chars | "
            "history=%d, smart_money=%d, volume_price=%d, sentiment=%d, "
            "evidence(market/news/fund/macro)=%d/%d/%d/%d, "
            "provenance=%d, memory=%d, claims=%d, unresolved=%d, round_summary=%d",
            len(prompt),
            len(history or ""),
            len(smart_money_input or ""),
            len(volume_price_input or ""),
            len(sentiment_input or ""),
            len(market_evidence_summary),
            len(news_evidence_summary),
            len(fundamentals_evidence_summary),
            len(macro_evidence_summary),
            len(provenance_context),
            len(past_memory_str or ""),
            len(claims_text or ""),
            len(unresolved_claims_text or ""),
            len(round_summary_text or ""),
        )

        # ── 实现 Token 级流式输出 ──────────────────
        tracker = current_tracker_var.get()
        model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        full_content = ""
        reasoning_buf: list[str] = []
        first_token_at: float | None = None
        first_reasoning_at: float | None = None
        start = time.monotonic()

        async for chunk in llm.astream(prompt):
            now = time.monotonic()
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            full_content += content

            # reasoning_content (thinking 模型) 仅做 server 端日志，不发前端
            reasoning = None
            extra = getattr(chunk, "additional_kwargs", None) or {}
            if isinstance(extra, dict):
                reasoning = extra.get("reasoning_content")
            if reasoning:
                if first_reasoning_at is None:
                    first_reasoning_at = now
                reasoning_buf.append(reasoning)

            if content:
                if first_token_at is None:
                    first_token_at = now
                if tracker:
                    tracker._emit_token("Research Manager", "investment_plan", content)
                    tracker.emit_debate_token(
                        debate="research", agent="Research Manager",
                        round_num=-1, token=content,
                    )

        total_elapsed = time.monotonic() - start
        reasoning_text = "".join(reasoning_buf)
        _logger.info(
            "[research_manager] streaming done: total_elapsed=%.2fs | "
            "ttft_reasoning=%.2fs ttft_content=%.2fs | "
            "reasoning_chars=%d content_chars=%d",
            total_elapsed,
            (first_reasoning_at - start) if first_reasoning_at else -1,
            (first_token_at - start) if first_token_at else -1,
            len(reasoning_text),
            len(full_content),
        )
        if reasoning_text:
            _logger.debug(
                "[research_manager] reasoning preview (%d chars): %s",
                len(reasoning_text),
                reasoning_text[:1500],
            )

        # ── 事实核验与裁决自洽硬闸 ──────────────────
        truth_evaluator = EvidenceFactualTruthEvaluator()
        claims_verification = truth_evaluator.evaluate_claims(
            claims=claims,
            seven_reports=seven_reports,
            market_data_context=effective_market_data_context,
            analysis_baseline_date=analysis_baseline_date,
            social_data_context=social_data_context,
        )

        manager_verdict = extract_and_validate_manager_verdict(
            raw_response=full_content,
            claims_verification=claims_verification,
            claims=claims,
            challenges=challenges,
            challenges_verification=challenges_verification,
            market_data_context=effective_market_data_context,
        )
        claim_evidence_summary = truth_evaluator.aggregate_claim_evidence(
            claims=claims,
            claims_verification=claims_verification,
            analysis_baseline_date=analysis_baseline_date or None,
            expected_symbol=expected_symbol or None,
            market_data_context=effective_market_data_context,
        )
        manager_verdict["claim_evidence_summary"] = claim_evidence_summary
        manager_verdict["horizon"] = research_horizon
        manager_verdict["research_horizon"] = research_horizon
        manager_verdict["evidence_relation_status"] = claim_cluster_metrics.get("relation_graph_status", "pending")
        manager_verdict["evidence_relation_reason"] = claim_cluster_metrics.get("relation_graph_reason", "")
        manager_verdict["expectation_revision"] = expectation_revisions

        claim_cluster_metrics, manager_verdict, _ = apply_manager_double_count_guard(
            claim_cluster_metrics=claim_cluster_metrics,
            expectation_revisions=expectation_revisions,
            claims=claims,
            manager_verdict=manager_verdict,
        )

        er_valid, er_violations = validate_manager_expectation_revision_consumption(
            manager_verdict=manager_verdict,
            raw_response=full_content,
            expectation_revisions=expectation_revisions,
        )
        if not er_valid:
            manager_verdict["consistency_check_passed"] = False
            manager_verdict["failed_checks"].extend(er_violations)

        if not manager_verdict["consistency_check_passed"]:
            failed_reasons = "; ".join(manager_verdict["failed_checks"])
            _logger.warning("[research_manager] consistency check failed: %s", failed_reasons)
            blocked_plan = f"研究总监裁决自洽硬闸未通过：{failed_reasons}。已阻断进入 Trader 执行阶段。"
            final_plan = blocked_plan
            final_decision = f"{full_content}\n\n[系统硬闸告警] 裁决自洽硬闸未通过：{failed_reasons}，已阻断后续交易。"
        else:
            final_plan = full_content
            final_decision = full_content

        # ── 推送辩论裁决（标记流式结束）──
        if tracker:
            tracker.emit_debate_message(
                debate="research", agent="Research Manager",
                round_num=-1, content=final_decision, is_verdict=True,
            )

        claim_weights = None
        credit_weight_audit = None
        if is_credit_weighting_enabled(investment_debate_state) or is_credit_weighting_enabled(state):
            # Live gate evaluation (fail-closed without h1b_gate_samples).
            # Do not hardcode system_gate_passed=False — that made flag-on still flat.
            from tradingagents.agents.utils.shadow_credit import (
                resolve_claim_credit_weights_for_manager,
            )

            historical_samples = state.get("h1b_gate_samples") or []
            if not isinstance(historical_samples, list):
                historical_samples = []
            weights_res = resolve_claim_credit_weights_for_manager(
                claims=claims,
                claim_evidence_summary=claim_evidence_summary,
                historical_samples=historical_samples,
                credit_weighting_enabled=True,
            )
            claim_weights = weights_res.get("claim_weights")
            credit_weight_audit = {
                "credit_weighting_active": bool(weights_res.get("credit_weighting_active", False)),
                "system_gate_passed": bool(weights_res.get("system_gate_passed", False)),
                "system_gate_status": weights_res.get("system_gate_status", "FAIL"),
                "recommendation": weights_res.get("recommendation", "KEEP_FALSE"),
                "bias_freeze_reasons": weights_res.get("bias_freeze_reasons") or {},
                "model_weights": weights_res.get("model_weights") or {},
                "global_fallback_shadow": bool(weights_res.get("global_fallback_shadow", True)),
            }
            if not credit_weight_audit["system_gate_passed"]:
                _logger.info(
                    "[research_manager] credit weighting stay flat: system_gate=%s recommendation=%s",
                    credit_weight_audit["system_gate_status"],
                    credit_weight_audit["recommendation"],
                )

        new_investment_debate_state = {
            **investment_debate_state,
            "judge_decision": final_decision,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_speaker": investment_debate_state.get("current_speaker", ""),
            "current_response": final_decision,
            "count": investment_debate_state["count"],
            "claims": claims,
            "round_messages": investment_debate_state.get("round_messages", []),
            "focus_claim_ids": investment_debate_state.get("focus_claim_ids", []),
            "open_claim_ids": investment_debate_state.get("open_claim_ids", []),
            "resolved_claim_ids": investment_debate_state.get("resolved_claim_ids", []),
            "unresolved_claim_ids": unresolved_claim_ids,
            "round_summary": round_summary,
            "round_goal": investment_debate_state.get("round_goal", ""),
            "claim_counter": investment_debate_state.get("claim_counter", 0),
            "manager_verdict": manager_verdict,
            "expectation_revision": expectation_revisions,
            "evidence_verification": claims_verification,
            "claim_evidence_summary": claim_evidence_summary,
            "challenge_verification": challenges_verification,
            "report_manifest": report_manifest,
            "claim_cluster_metrics": claim_cluster_metrics,
            "evidence_relation_status": claim_cluster_metrics.get("relation_graph_status", "pending"),
            "evidence_relation_reduction": claim_cluster_metrics.get("relation_audit", {}),
            "independent_cluster_count": claim_cluster_metrics.get("independent_cluster_count", 0),
            "analyst_count": claim_cluster_metrics.get("analyst_count", 0),
            "verified_evidence_count": claim_cluster_metrics.get("verified_evidence_count", 0),
        }
        if claim_weights is not None:
            new_investment_debate_state["claim_weights"] = claim_weights
        if credit_weight_audit is not None:
            new_investment_debate_state["credit_weight_audit"] = credit_weight_audit

        # D-009 P0-1/P0-5b/P1-2: every successful terminal path emits canonical decision_status.
        vpa_ctx = effective_market_data_context.get("vpa_context") if isinstance(effective_market_data_context, dict) else None
        if (
            manager_verdict.get("position_pct") is not None
            and isinstance(vpa_ctx, dict)
            and vpa_ctx.get("reversal_state") == "reversal_confirmed"
        ):
            from tradingagents.agents.utils.decision_status import resolve_staged_entry_position
            manager_verdict["position_pct"] = resolve_staged_entry_position(
                manager_verdict["position_pct"],
                vpa_context=vpa_ctx,
            )

        terminal_status = status_from_manager_verdict(
            manager_verdict,
            prior_analysis_status=state.get("analysis_status"),
            investment_debate_state=new_investment_debate_state,
            claims_verification=claims_verification,
            claim_evidence_summary=claim_evidence_summary,
            focus_claim_ids=new_investment_debate_state.get("focus_claim_ids"),
            unresolved_claim_ids=unresolved_claim_ids,
            claims=claims,
            market_data_context=effective_market_data_context,
            vpa_context=vpa_ctx,
        )
        status_dict = terminal_status.to_dict()
        manager_verdict = {
            **manager_verdict,
            "decision_status": status_dict,
            "analysis_status": status_dict["analysis_status"],
            "trade_action": status_dict["trade_action"],
            "risk_status": status_dict["risk_status"],
            "confirmation_state": status_dict["confirmation_state"],
            "horizon": research_horizon,
            "research_horizon": research_horizon,
            "expectation_revision": expectation_revisions,
        }
        new_investment_debate_state["manager_verdict"] = manager_verdict
        new_investment_debate_state["expectation_revision"] = expectation_revisions

        payload = {
            "investment_debate_state": new_investment_debate_state,
            "investment_plan": final_plan,
            "manager_verdict": manager_verdict,
            "expectation_revision": expectation_revisions,
            "evidence_verification": claims_verification,
            "claim_evidence_summary": claim_evidence_summary,
            "challenge_verification": challenges_verification,
            "report_manifest": report_manifest,
            "decision_status": status_dict,
            "analysis_status": status_dict["analysis_status"],
            "trade_action": status_dict["trade_action"],
            "risk_status": status_dict["risk_status"],
            "confirmation_state": status_dict["confirmation_state"],
            "run_integrity": run_integrity.to_dict(),
        }
        return payload

    return research_manager_node
