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


# DAV-1068 缺陷B：E-04 priced-in 守卫按 occurrence 判定，否定/不确定/明确驳回的引述不算肯定断言。
# 否定词表覆盖非紧邻否定（同一子句内）；双重否定（偶数个否定词）仍视为断言。
_PRICED_IN_CLAUSE_BREAKS = "。！？；，、：,.;:!?—–\n"
_PRICED_IN_NEG_ZH = re.compile(
    r"尚未|并未|未被|并没有|未有|无法|不能|难以|无从|没有|并非|不会|不应|未(?!来)|(?<!得)不(?!断|但|得)"
)
_PRICED_IN_NEG_EN = re.compile(
    r"\b(?:not|no|never|cannot|can't|cant|isn't|isnt|aren't|arent|wasn't|wasnt|weren't|werent|"
    r"hasn't|hasnt|haven't|havent|hadn't|hadnt|won't|wont|wouldn't|wouldnt|shouldn't|shouldnt|"
    r"didn't|didnt|doesn't|doesnt|don't|dont|unable|unclear|uncertain|unconfirmed|yet\s+to\s+be)\b",
    re.IGNORECASE,
)
_PRICED_IN_REJECT_ZH = re.compile(r"驳回|不成立|不予采纳|不能成立|予以否定|并不采纳|未被采纳|被否定")
# DAV-1071 缺陷1：条件/假设句引导的命中不算断言（若/如果/一旦…引导的条件从句是情景推演而非事实断言）
_E04_COND_ZH = re.compile(r"若(?!干)|如果|倘若|假若|倘使|一旦|假设|除非|万一")
_E04_COND_EN = re.compile(
    r"\b(?:if|unless|in\s+case|assuming|provided\s+that|providing\s+that|should\s+there\s+be)\b",
    re.IGNORECASE,
)
# DAV-1110：条件句中合取并列连词（且/并/及/与/and）延续条件域，直到后果标记或硬边界
_E04_COND_CONJ_ZH = re.compile(r"^\s*(?:且|并且|并|以及|及|与|同|还|同时|包括)")
_E04_COND_CONJ_EN = re.compile(r"^\s*(?:and|as\s+well\s+as|along\s+with)\b", re.IGNORECASE)
# 条件后承接的后果断言/主句引导词（若出现则切出条件域，进入后果断言域）
_E04_CONSEQ_ZH = re.compile(
    r"则|那么|乃至|即可|定会|必将|必然|因而|是以|"
    r"就(?!业|近|是|要|绪|范|地|便|医|诊|读|学|职|任|寝|餐|座|坐|位|算|势|手|教|擒|隅)|"
    r"便(?!宜|利|秘|签|当|条|携|衣|帽|饭|桥|民|警)|"
    r"^\s*将(?:会|要|令|致|使|对|为|在|于|把)?"
)
_E04_CONSEQ_EN = re.compile(r"\b(?:then|therefore|thus|hence|consequently)\b", re.IGNORECASE)
# DAV-1110 rerun：情景标签开启条件域（情景推演段内的命中属情景假设，非事实断言）
# 中文：悲观情景/乐观情景/基准情景/极端情景/情景测试/压力测试（容忍 markdown 加粗与冒号/括号间隔）
_E04_SCENARIO_ZH = re.compile(
    # 限定词必选（悲观/乐观...情景）或子句首复合标签（^情景推演/测试/假设/分析、^压力测试）
    # 🟢-1：复合标签收窄为子句首标签形态，避免「此情景分析已被证伪」等中间词泄漏
    r"(?:悲观|乐观|基准|极端|中性|牛市|熊市)\s*情景(?:\s*[（(][^）)]*[）)])?\s*[:：下中里]?|"
    r"^\s*[*_#\s-]*情景\s*(?:推演|测试|假设|分析)\s*[:：下中里]?|"
    r"^\s*[*_#\s-]*压力\s*测试\s*[:：下中里]?"
)
# 英文：base|bull|bear|worst|best|downside|upside case/scenarios / scenario analysis/test/simulation / stress case/test
# 🟡-A：裸 scenario(s) 必须带限定词或子句首复合标签，避免 "the above scenario was falsified" 等泄漏
_E04_SCENARIO_EN = re.compile(
    r"\b(?:base|bull|bear|worst|best|downside|upside)[\s-]+(?:case|scenarios?)\b\s*[:：]?|"
    r"^\s*[*_#\s-]*(?:scenario\s+(?:analysis|test|simulation)|stress[\s-]+(?:case|test))\b\s*[:：]?",
    re.IGNORECASE,
)
# 情景域内回转标记：情景段结束、回到现实断言（如「悲观情景：…，但多头已确认将超预期」后半必须拦）
# 注：回转标记仅锚定子句首（^\s*）；句中转折不切断情景域属可接受边界（防止情景内容自身转折被误切）
_E04_SCENARIO_BREAK_ZH = re.compile(
    r"^\s*(?:但(?:是)?|然而|不过|相反|反之|实际上|事实上|现实(?:中|情况)|"
    r"回到现实|当前(?:市场|盘面)|目前(?:市场|盘面)|已确认|业已|已经|现实情况)"
)
_E04_SCENARIO_BREAK_EN = re.compile(
    r"^\s*(?:but|however|yet|in\s+reality|in\s+fact|actually|as\s+it\s+stands|confirmed)\b",
    re.IGNORECASE,
)
# DAV-1110 🟡-1：情景证伪/否定语境（多头已证伪/排除/否定该情景…），不开启或立即终止情景域
# 🟡-NEW-1：否定词严格限定为对情景/假设的处置动词形态，避免普通形容词（negative/偏否定）误判
_E04_SCENARIO_REJECT_ZH = re.compile(
    r"证伪|已被证伪|已证伪|被证伪|"
    r"予以否定|被否定|否定了|"
    r"(?<!偏)否定(?:\s*此|\s*该|\s*上述|\s*前述|\s*各类|\s*相关)?\s*(?:(?:悲观|乐观|基准|极端|中性|牛市|熊市)\s*)?(?:情景|假设|推演|预测|观点|结论)|"
    r"不予采纳|不能成立|不成立|驳回|未被采纳|并不采纳|推翻|抛弃|"
    r"排除(?:\s*此|\s*该|\s*上述|\s*前述|\s*各类|\s*相关)?\s*(?:(?:悲观|乐观|基准|极端|中性|牛市|熊市)\s*)?情景|"
    r"(?:情景|假设).{0,6}(?:排除|证伪|(?<!偏)否定|不成立|推翻)|"
    r"排除.*(?:情景|假设)"
)
_E04_SCENARIO_REJECT_EN = re.compile(
    r"\b(?:ruled\s+out|falsif\w*|reject\w*|dismiss\w*|disprov\w*|eliminat\w*|invalidat\w*|negat(?:e|es|ed|ing))\b|"
    r"\bexclud\w*\s+(?:the\s+|this\s+|that\s+|such\s+)?(?:(?:base|bull|bear|worst|best|downside|upside)[\s-]+(?:case|scenarios?)|stress[\s-]+(?:case|test)|scenarios?)\b|"
    r"\b(?:case|scenarios?|assumptions?)\s+(?:was|were|is|are|has\s+been|have\s+been)?\s*(?:excluded|ruled\s+out|falsified|rejected|dismissed)\b",
    re.IGNORECASE,
)
# DAV-1071 缺陷1：beat/miss 关键词后紧跟名词时构成名词性偏正短语（如“超预期幅度/信息”），是列举未知项而非断言
_BEAT_MISS_NOUN_SUFFIX = re.compile(
    r"^\s*(?:幅度|空间|概率|可能性|程度|水平|信息|情形|情况|风险|因素|变量|情景)"
)
# DAV-1071 缺陷2：附带可回溯披露依据（明确披露日或已过交易日数）且用于降权的 priced-in 引用放行
_E04_DISCLOSURE_WORD = re.compile(r"披露|公告|公布|发布|公开|财报|中报|年报|季报|业绩快报|业绩预告|龙虎榜")
_E04_DATE_PAT = re.compile(
    r"\d{4}\s*[-年/.]\s*\d{1,2}\s*[-月/.]\s*\d{1,2}\s*日?|\d{1,2}\s*月\s*\d{1,2}\s*日"
)
_E04_TRADING_DAYS_PAT = re.compile(r"超(?:过|出)?\s*\d+\s*个?交易日|\d+\s*个?交易日")
# DAV-1074 🟡-1：移除「谨慎|观望」高频弱词，收窄降权豁免口径
_E04_DOWNWEIGHT = re.compile(
    r"降权|降格|弱化|解释力|不作为|不得作为|不能作为|不应作为|不计入|不纳入|剔除|排除|"
    r"降低.{0,4}权重|减仓|止损|不(?:宜|可|建议|应)?\s*追高|不加仓|不追涨"
)
# claim_id 引用语域：id 与命中之间出现自主判断/转折标记时视为独立断言而非引用
_E04_OWN_VOICE = re.compile(
    r"我方|我认为|我觉得|笔者|本人|独立判断|独立认为|即判|即认为|据此判|但|然而|不过|相反"
)
# 反向把已定价当作方向支撑（做多/加仓/追高…且非否定语境）仍拦
_E04_DIRECTIONAL = re.compile(r"做多|做空|加仓|买入|建仓|追高|追涨|抄底|看多|看空|满仓|荐股")
_E04_NEG_PREFIX = re.compile(r"(?:不|勿|莫|未|严禁|禁止|避免|不宜|不可|不应|杜绝|防止)\s*$")
_PRICED_IN_SENTENCE_BREAKS = "。！？!?;；\n"
# 引用豁免要求匹配短语之外至少带这么多上下文（去空白/标点后），防止裸断言撞 claim 子串被误放行
_QUOTE_MIN_CONTEXT_CHARS = 4
# DAV-1073 复审：生产上 manager 以「claim ID + 改写」引用，逐字子串零覆盖。
# 放宽为：命中所在整句与某 claim 归一文本最长公共子串 ≥阈值，且该 claim 本身含同类关键词；
# 或句内出现含同类关键词的 claim_id。阈值 10 字保证句子主体源自 claim 而非巧合重叠。
_QUOTE_LCS_MIN = 10
_PI_QUOTE_KW = re.compile(
    r"已定价|完全定价|充分定价|已被市场消化|已被.{0,2}消化|已在股价中反映|已反映在股价中|"
    r"priced[\s-]*in|discounted|reflected",
    re.IGNORECASE,
)
_BM_QUOTE_KW = re.compile(
    r"超预期|超出预期|超越预期|好于预期|优于预期|高于预期|不及预期|低于预期|未达预期|"
    r"差于预期|弱于预期|逊于预期|落后于预期|beat|miss|expectation|consensus",
    re.IGNORECASE,
)
# 「已定价状态为 unknown」等显式标注引用形态（引用栏位标注而非断言事实）。
# DAV-1074 🔴-1：仅当标注 match 覆盖命中或与命中同子句时生效，同句独立断言不连带豁免。
_PI_ANNOTATION = re.compile(
    r"已定价\s*(?:状态|栏位|标注|标记|评级|结论)?\s*(?:为|是|：|:|=|标为|标成|记为)\s*"
    r"(?:unknown|未知|不确定|待验证|待确认)",
    re.IGNORECASE,
)


def _normalize_quote_text(s: str) -> str:
    return re.sub(r"[\s　]+", "", s or "")


def _lcs_len(a: str, b: str, cap: int = 600) -> int:
    """Longest common substring length (capped inputs to bound cost)."""
    a, b = a[:cap], b[:cap]
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ch in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            v = prev[j - 1] + 1 if ch == cb else 0
            cur.append(v)
            if v > best:
                best = v
        prev = cur
        if best >= min(len(a), len(b)):
            break
    return best


def _claim_quote_index(claims: Sequence[Mapping[str, Any]] | None) -> list[dict]:
    """归一化 claim 语料：每条带 norm 文本与 claim_id（供 ID 引用路径判定）。"""
    items: list[dict] = []
    for c in claims or []:
        if not isinstance(c, Mapping):
            continue
        cid = str(c.get("claim_id") or "").strip()
        norms: list[str] = []
        for key in ("claim", "claim_text", "text"):
            v = c.get(key)
            if v:
                norms.append(_normalize_quote_text(str(v)))
        ev = c.get("evidence")
        if isinstance(ev, list):
            norms.extend(_normalize_quote_text(str(x)) for x in ev)
        elif ev:
            norms.append(_normalize_quote_text(str(ev)))
        for n_ in norms:
            if n_:
                items.append({"norm": n_, "claim_id": cid})
    return items


def _is_claim_quotation(
    text: str,
    start: int,
    end: int,
    claim_index: Sequence[dict],
    kw_in_claim: re.Pattern,
) -> bool:
    """DAV-1071/DAV-1073: a priced-in / beat-miss mention inside analyst claim text is a quotation,
    not the manager's own assertion. Quote forms (any suffices):
      1. enclosing clause/sentence is a verbatim substring of a claim text (with ≥4 chars context);
      2. enclosing sentence shares an LCS ≥ _QUOTE_LCS_MIN with a claim that itself contains the
         same kind of keyword (production「claim ID + 改写」paraphrase form);
      3. enclosing sentence names a claim_id whose claim contains the same kind of keyword.
    The claim must itself carry the keyword — quotation exemption never invents priced-in/beat-miss
    content the analyst didn't write."""
    if not claim_index:
        return False
    n = len(text)
    matched = _normalize_quote_text(text[start:end])
    # clause-level span (weak breaks) — covers quotes embedded mid-sentence
    cl, cr = start, end
    while cl > 0 and not _is_clause_break(text, cl - 1):
        cl -= 1
    while cr < n and not _is_clause_break(text, cr):
        cr += 1
    # sentence-level span (strong breaks) — covers verbatim copy / paraphrase of a whole sentence
    sl, sr = start, end
    while sl > 0 and text[sl - 1] not in _PRICED_IN_SENTENCE_BREAKS:
        sl -= 1
    while sr < n and text[sr] not in _PRICED_IN_SENTENCE_BREAKS:
        sr += 1
    clause_span = text[cl:cr]
    sentence_span = text[sl:sr]
    sentence_norm = _normalize_quote_text(sentence_span)
    for span, span_off in ((clause_span, cl), (sentence_span, sl)):
        norm = _normalize_quote_text(span)
        # 🟢：按命中偏移删除匹配段，而非首个出现位置
        hit_off_norm = len(_normalize_quote_text(text[span_off:start]))
        ctx = norm[:hit_off_norm] + norm[hit_off_norm + len(matched):]
        ctx_alnum = re.sub(r"[，。！？；、：,.;:!?—–（）()「」『』【】\[\]《》<>\"'“”‘’]+", "", ctx)
        if len(ctx_alnum) < _QUOTE_MIN_CONTEXT_CHARS:
            continue
        if any(norm and norm in item["norm"] for item in claim_index):
            return True
    # paraphrase path: sentence largely derives from a claim carrying the same keyword.
    # 但句子含非否定语境的方向词（可积极做多/建议追高…）时属反向支撑自断言，不予豁免。
    # DAV-1074 🔴-2：claim_id 路径锚定——id 与命中同子句，或 id 在句内先于命中且两者之间
    # 无自主判断/转折标记（「提及INV-3后，我方独立判断利好已定价」不再放行）。
    for item in claim_index:
        if not kw_in_claim.search(item["norm"]):
            continue
        cid = item["claim_id"]
        id_hit = False
        if cid:
            # DAV-1080 🔴：同子句分支与句级分支同一口径——id 须先于命中，且 id 与命中之间
            # 不得有自主判断/转折标记；豁免判定不依赖标点有无（「采纳CLM-PI但我方独立判断…」拦截）。
            pos_in_clause = clause_span.find(cid)
            if pos_in_clause >= 0:
                id_end = cl + pos_in_clause + len(cid)
                if id_end <= start and not _E04_OWN_VOICE.search(text[id_end:start]):
                    id_hit = True
            if not id_hit:
                id_pos = sentence_span.find(cid)
                if 0 <= id_pos < start - sl and not _E04_OWN_VOICE.search(
                    sentence_span[id_pos + len(cid): start - sl]
                ):
                    id_hit = True
        # DAV-1076 🔴-4：LCS 分支与 ID 分支同一语域检查——句内含自主判断/转折标记即非引用
        lcs_hit = _lcs_len(sentence_norm, item["norm"]) >= _QUOTE_LCS_MIN
        fuzzy_hit = id_hit or (lcs_hit and not _E04_OWN_VOICE.search(sentence_span))
        if fuzzy_hit and not _has_directional_support(sentence_span):
            return True
    return False


def _has_directional_support(sentence: str) -> bool:
    """句内存在未被否定词修饰的方向性操作建议（做多/追高/加仓…）。"""
    for m in _E04_DIRECTIONAL.finditer(sentence):
        if not _E04_NEG_PREFIX.search(sentence[: m.start()]):
            return True
    return False


def _is_clause_break(text: str, i: int) -> bool:
    """Punctuation break, but '.', ',' inside a number (e.g. 88.80 / 1,000) is not a break."""
    ch = text[i]
    if ch not in _PRICED_IN_CLAUSE_BREAKS:
        return False
    if ch in ".,，" and i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
        return False
    return True


def _priced_in_clause(text: str, start: int, end: int) -> tuple[str, str]:
    """Return (clause_before_match, clause_after_match) bounded by nearest punctuation breaks."""
    left = start
    while left > 0 and not _is_clause_break(text, left - 1):
        left -= 1
    right = end
    n = len(text)
    while right < n and not _is_clause_break(text, right):
        right += 1
    return text[left:start], text[end:right]


def _in_conditional_clause(text: str, hit_start: int, hit_end: int) -> bool:
    """DAV-1071/DAV-1073/DAV-1110: hit is non-assertive when sitting in a conditional premise domain.

    - A conditional domain is opened by a conditional marker (若/如果/if...).
    - It covers the conditional clause and extends across conjunction-headed clauses (且/并/及/and...).
    - It is terminated by a consequence marker (则/那么/就/便/then...) or sentence boundaries.
    - If a consequence marker intervenes between the conditional marker and the hit,
      the hit belongs to the consequence/main clause assertion (e.g. 「若A则B，C已定价」、「若A则B超预期」)
      -> NOT exempt (must flag).
    """
    # 找到整句的起点
    sl = hit_start
    while sl > 0 and text[sl - 1] not in _PRICED_IN_SENTENCE_BREAKS:
        sl -= 1
    sentence_up_to_hit = text[sl:hit_start]

    # 切量子句（按 _is_clause_break）
    clause_spans: list[tuple[int, int]] = []
    c_start = 0
    for idx in range(len(sentence_up_to_hit)):
        if _is_clause_break(sentence_up_to_hit, idx):
            clause_spans.append((c_start, idx))
            c_start = idx + 1
    clause_spans.append((c_start, len(sentence_up_to_hit)))

    clauses = [sentence_up_to_hit[s:e].strip() for s, e in clause_spans]
    current_clause = clauses[-1]

    # 1. 紧邻前缀子句（同一子句内）
    # 如果同子句内有条件词：
    m_cond_zh = list(_E04_COND_ZH.finditer(current_clause))
    m_cond_en = list(_E04_COND_EN.finditer(current_clause))
    if m_cond_zh or m_cond_en:
        last_cond_pos = max(
            [m.start() for m in m_cond_zh] + [m.start() for m in m_cond_en]
        )
        # 检查条件词与 hit 之间是否出现了后果标记（如「若A则B超预期」）
        after_cond = current_clause[last_cond_pos:]
        if _E04_CONSEQ_ZH.search(after_cond) or _E04_CONSEQ_EN.search(after_cond):
            return False
        return True

    # 2. 情景标签条件域（DAV-1110 rerun）
    # 同句内前序子句若含情景标签（悲观情景/基准情景/scenario/bear case/压力测试…），
    # 则其后各子句均属情景假设域——直到回转标记（但/然而/不过/but…）、后果标记、证伪/否定标记或句界为止。
    # 先查当前子句自身是否已是回转子句（情景结束后的真实断言不得豁免）。
    if _E04_SCENARIO_BREAK_ZH.search(current_clause) or _E04_SCENARIO_BREAK_EN.search(current_clause):
        pass  # 明确回转：直接落到合取链判定，不进入情景域
    elif _E04_SCENARIO_REJECT_ZH.search(current_clause) or _E04_SCENARIO_REJECT_EN.search(current_clause):
        pass  # 🟡-1：当前子句自身处于证伪/否定语境，不开启情景条件域
    else:
        # 🟡-2：当前子句自身即以情景标签引导（标签在命中之前，如「悲观情景下中报将超预期」无标点形态）
        m_scen_zh = list(_E04_SCENARIO_ZH.finditer(current_clause))
        m_scen_en = list(_E04_SCENARIO_EN.finditer(current_clause))
        if m_scen_zh or m_scen_en:
            # 🟢-2：取 m.end() 使切片起点严格在标签自身之后
            last_scen_end = max([m.end() for m in m_scen_zh] + [m.end() for m in m_scen_en])
            after_scen = current_clause[last_scen_end:]
            # 标签与命中之间出现后果标记（如「悲观情景下则业绩超预期」）→ 后果断言，不豁免
            if not (_E04_CONSEQ_ZH.search(after_scen) or _E04_CONSEQ_EN.search(after_scen)):
                return True

        # 🟡-B：跨子句后果标记检测：若当前子句自身由后果标记引导（如「悲观情景：…，则中报业绩超预期」），
        # 属后果断言，不予情景域豁免（与第 3 段合取链 760 行对称检测）
        if not (_E04_CONSEQ_ZH.search(current_clause) or _E04_CONSEQ_EN.search(current_clause)):
            for prev_clause in reversed(clauses[:-1]):
                if not prev_clause:
                    continue
                # 前序子句出现回转标记、后果标记或证伪/否定标记 → 情景域已终止/未开启
                if (
                    _E04_SCENARIO_BREAK_ZH.search(prev_clause)
                    or _E04_SCENARIO_BREAK_EN.search(prev_clause)
                    or _E04_CONSEQ_ZH.search(prev_clause)
                    or _E04_CONSEQ_EN.search(prev_clause)
                    or _E04_SCENARIO_REJECT_ZH.search(prev_clause)
                    or _E04_SCENARIO_REJECT_EN.search(prev_clause)
                ):
                    break
                if _E04_SCENARIO_ZH.search(prev_clause) or _E04_SCENARIO_EN.search(prev_clause):
                    return True
                # 未命中标签也未终止：继续向前倒查，允许情景内容跨多子句
            # 情景标签搜索无果 → 继续合取链判定

    # 3. 跨子句并列条件合取链（DAV-1110）
    # 当前子句无条件词，必须以合取连词引导（且/并/及/and...）
    if not (_E04_COND_CONJ_ZH.search(current_clause) or _E04_COND_CONJ_EN.search(current_clause)):
        return False
    # 当前合取子句内如果已经出现了后果标记（如「且...则...超预期」），也不属于条件域
    if _E04_CONSEQ_ZH.search(current_clause) or _E04_CONSEQ_EN.search(current_clause):
        return False

    # 向前倒查：前面每一级子句必须也是合取子句，直到找到条件起始子句；
    # 期间若遇到后果标记（则/那么/就/then...）或非合取独立分句，则链条断开。
    for prev_clause in reversed(clauses[:-1]):
        if not prev_clause:
            continue
        # 如果前序子句含后果标记，说明已经由条件转入推论/主句
        if _E04_CONSEQ_ZH.search(prev_clause) or _E04_CONSEQ_EN.search(prev_clause):
            return False
        # 如果前序子句包含条件标记，确认合取链成立
        if _E04_COND_ZH.search(prev_clause) or _E04_COND_EN.search(prev_clause):
            return True
        # 否则该前序子句本身也必须是合取子句以继续向前传递条件域
        if not (_E04_COND_CONJ_ZH.search(prev_clause) or _E04_COND_CONJ_EN.search(prev_clause)):
            return False

    return False


def _is_priced_in_assertion(text: str, start: int, end: int) -> bool:
    """Per-occurrence check: a priced-in mention is an assertion unless its own clause negates/rejects it."""
    before, after = _priced_in_clause(text, start, end)
    # 引用对方观点但同句内（跨越逗号子句）明确驳回/判不成立 -> 非本人断言
    strong_right = end
    n = len(text)
    while strong_right < n and text[strong_right] not in _PRICED_IN_SENTENCE_BREAKS:
        strong_right += 1
    after_sentence = text[end:strong_right]
    if _PRICED_IN_REJECT_ZH.search(after_sentence) or _PRICED_IN_REJECT_ZH.search(before):
        return False
    # DAV-1071 缺陷1 / DAV-1110：条件/假设从句内命中（含「若A，且B」并列条件从句）属情景推演，非断言
    if _in_conditional_clause(text, start, end):
        return False
    # 同一句内一处否定不豁免另一处肯定：按出现位置所在子句计数否定词，奇数为否定、偶数为双重否定
    neg_count = len(_PRICED_IN_NEG_ZH.findall(before)) + len(_PRICED_IN_NEG_EN.findall(before))
    return neg_count % 2 == 0


def _is_beat_miss_assertion(text: str, start: int, end: int) -> bool:
    """DAV-1071 缺陷1：beat/miss 与 priced-in 同一套 per-occurrence 判别，另加名词性偏正短语豁免
    （“超预期幅度/信息/风险”等是列举未知项而非断言）。"""
    n = len(text)
    right = end
    while right < n and not _is_clause_break(text, right):
        right += 1
    if _BEAT_MISS_NOUN_SUFFIX.match(text[end:right]):
        return False
    return _is_priced_in_assertion(text, start, end)


def _has_traceable_pricing_basis(sentence: str) -> bool:
    """DAV-1071 缺陷2 / DAV-1074 🟡-1：句子含可回溯披露依据 = 披露类词与（明确日期 或 已过交易日数）共现。
    单独一个日期（如“9月18日美联储降息”）不构成披露依据。"""
    return bool(
        _E04_DISCLOSURE_WORD.search(sentence)
        and (_E04_DATE_PAT.search(sentence) or _E04_TRADING_DAYS_PAT.search(sentence))
    )


def _is_downweighting_pricing(sentence: str) -> bool:
    """ priced-in 引用用于对该 claim 降权/解释力弱化，而非反向支撑方向。
    方向词仅在非否定语境下出现（如“可追高/应加仓”）时视为反向支撑，不予豁免。"""
    if not _E04_DOWNWEIGHT.search(sentence):
        return False
    for m in _E04_DIRECTIONAL.finditer(sentence):
        if not _E04_NEG_PREFIX.search(sentence[: m.start()]):
            return False
    return True


def _sentence_span(text: str, start: int, end: int) -> str:
    left = start
    while left > 0 and text[left - 1] not in _PRICED_IN_SENTENCE_BREAKS:
        left -= 1
    right = end
    n = len(text)
    while right < n and text[right] not in _PRICED_IN_SENTENCE_BREAKS:
        right += 1
    return text[left:right]


def validate_manager_expectation_revision_consumption(
    manager_verdict: Mapping[str, Any],
    raw_response: str,
    expectation_revisions: Any,
    claims: Sequence[Mapping[str, Any]] | None = None,
    seven_reports: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Validate that research manager only consumed structured expectation_revision fields without hallucination (E-04).

    Guards full raw_response, reason, and structured fields across Chinese and English:
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

    # DAV-1071：经理引用分析师 claim 文本（含其自标 unknown 的已定价标注）不等于自行断言；
    # 提前构建 claim 文本语料（空白归一），供 priced-in 与 beat/miss 两个分支共用。
    claim_index = _claim_quote_index(claims)

    # 1. Check if priced_in claimed as supported fact without traceable evidence (Chinese & English)
    if fund_pi != PRICED_IN_SUPPORTED and news_pi != PRICED_IN_SUPPORTED:
        has_pi_asserted = False
        pi_patterns_zh = (
            "已充分定价", "已完全定价", "市场已定价", "已基本定价",
            "股价已完全反映", "股价已充分反应", "市场已完全反映",
            "完全定价", "充分定价", "已被市场消化", "已被充分消化", "已被完全消化",
        )
        pi_occurrences: list[tuple[int, int]] = []
        for pat in pi_patterns_zh:
            idx = full_text.find(pat)
            while idx >= 0:
                pi_occurrences.append((idx, idx + len(pat)))
                idx = full_text.find(pat, idx + 1)
        for m in re.finditer(r"已定价|已在股价中反映|已反映在股价中", full_text):
            pi_occurrences.append((m.start(), m.end()))
        for m in re.finditer(
            r"(?:already\s+|fully\s+|largely\s+|mostly\s+|market\s+has\s+)?priced\s*[- ]?in\b",
            full_text,
            re.IGNORECASE,
        ):
            pi_occurrences.append((m.start(), m.end()))
        for m in re.finditer(r"\b(?:fully|largely)\s+discounted\b", full_text, re.IGNORECASE):
            pi_occurrences.append((m.start(), m.end()))
        for m in re.finditer(
            r"\b(?:fully|largely)\s+reflected\s+in\s+(?:the\s+)?(?:stock\s+)?price\b",
            full_text,
            re.IGNORECASE,
        ):
            pi_occurrences.append((m.start(), m.end()))
        for start, end in pi_occurrences:
            # DAV-1071 缺陷0 + DAV-1073：命中处于分析师 claim 文本/改写/ID引用内属引用，非自行断言；
            # 「已定价状态为 unknown」显式标注引用同样放行
            if _is_claim_quotation(full_text, start, end, claim_index, _PI_QUOTE_KW):
                continue
            # DAV-1074 🔴-1 / DAV-1076 🔴-3：标注豁免仅当标注 match 覆盖本命中区间。
            # 「同子句」路径已撤销——「已定价状态为unknown但/即市场实际已定价」类无标点转折
            # 绕过中，第二个命中不在标注 match 内，属独立断言必须拦。
            _pi_sl = start
            while _pi_sl > 0 and full_text[_pi_sl - 1] not in _PRICED_IN_SENTENCE_BREAKS:
                _pi_sl -= 1
            _pi_sr = end
            while _pi_sr < len(full_text) and full_text[_pi_sr] not in _PRICED_IN_SENTENCE_BREAKS:
                _pi_sr += 1
            _pi_sent = full_text[_pi_sl:_pi_sr]
            if any(
                m.start() <= start - _pi_sl < m.end()
                for m in _PI_ANNOTATION.finditer(_pi_sent)
            ):
                continue
            if not _is_priced_in_assertion(full_text, start, end):
                continue
            # DAV-1071 缺陷2：附带可回溯披露依据且用于降权的 priced-in 引用放行
            sentence = _sentence_span(full_text, start, end)
            if _has_traceable_pricing_basis(sentence) and _is_downweighting_pricing(sentence):
                continue
            has_pi_asserted = True
            break
        if has_pi_asserted:
            violations.append("E-04 守卫拦截：缺乏可回溯证据，经理不得将“已定价/priced in”当作已确证事实引用")

    # 2. Check if beat/miss claimed without comparable baseline (Chinese & English & Synonyms)
    if fund_base_type == "none" or fund_base_val is None:
        has_beat_miss = False
        beat_miss_kws_zh = (
            "超预期", "超出预期", "超越预期", "好于预期", "优于预期", "高于预期",
            "不及预期", "低于预期", "未达预期", "差于预期", "弱于预期", "逊于预期", "落后于预期",
        )
        # DAV-1071 缺陷1：beat/miss 改 per-occurrence 判别（否定/驳回/条件句/名词短语/引用豁免），
        # 与 priced-in 同一套语义判定；同位重叠关键词去重（保留最长）。
        bm_occurrences: list[tuple[int, int, str]] = []
        for kw in beat_miss_kws_zh:
            idx = full_text.find(kw)
            while idx >= 0:
                bm_occurrences.append((idx, idx + len(kw), kw))
                idx = full_text.find(kw, idx + 1)
        bm_occurrences.sort(key=lambda o: (o[0], -(o[1] - o[0])))
        bm_dedup: list[tuple[int, int, str]] = []
        for occ in bm_occurrences:
            if bm_dedup and occ[0] < bm_dedup[-1][1]:
                continue
            bm_dedup.append(occ)
        for start, end, kw in bm_dedup:
            if _is_claim_quotation(full_text, start, end, claim_index, _BM_QUOTE_KW):
                continue
            if not _is_beat_miss_assertion(full_text, start, end):
                continue
            has_beat_miss = True
            violations.append(f"E-04 守卫拦截：基本面无有效旧基线，经理不得在正文或裁决理由中断言业绩“{kw}”")
            break
        if not has_beat_miss:
            en_beat_iter = list(re.finditer(
                r"\b(?:beat|beats|beating|exceed|exceeded|exceeds|exceeding|surpass|surpassed|surpasses|surpassing|above|better than|higher than|ahead of)\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected)\b|\b(?:earnings|profit|revenue)\s+beat\b",
                full_text,
                re.IGNORECASE,
            ))
            for m_en_beat in en_beat_iter:
                if _is_claim_quotation(full_text, m_en_beat.start(), m_en_beat.end(), claim_index, _BM_QUOTE_KW):
                    continue
                if not _is_beat_miss_assertion(full_text, m_en_beat.start(), m_en_beat.end()):
                    continue
                violations.append(f"E-04 守卫拦截：基本面无有效旧基线，经理不得断言业绩超预期（命中 {m_en_beat.group(0)!r}）")
                has_beat_miss = True
                break
            if not has_beat_miss:
                en_miss_iter = list(re.finditer(
                    r"\b(?:fell short|falls short|fall short)(?:\s+of\b(?:\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected))?)?\b|"
                    r"\b(?:missed?|misses|missing|below|worse than|lower than|lagged|behind)\s+(?:(?:all\s+)?(?:market|analyst|street|wall\s+street|consensus|earnings)\s+)?(?:expectations?|consensus|estimates?|forecasts?|expected)\b|"
                    r"\b(?:earnings|profit|revenue)\s+miss\b",
                    full_text,
                    re.IGNORECASE,
                ))
                for m_en_miss in en_miss_iter:
                    if _is_claim_quotation(full_text, m_en_miss.start(), m_en_miss.end(), claim_index, _BM_QUOTE_KW):
                        continue
                    if not _is_beat_miss_assertion(full_text, m_en_miss.start(), m_en_miss.end()):
                        continue
                    violations.append(f"E-04 守卫拦截：基本面无有效旧基线，经理不得断言业绩不及预期（命中 {m_en_miss.group(0)!r}）")
                    has_beat_miss = True
                    break

    # 3. Check if financial numbers hallucinated when analyst reported gap / missing structured actual
    fund_act_val = (fund_er.get("actual") or {}).get("value")
    fund_act_status = (fund_er.get("actual") or {}).get("status")

    # Build corpus of all verified / existing evidence from input reports and debate claims
    evidence_tokens: list[str] = []
    if claims:
        for c in claims:
            if isinstance(c, Mapping):
                ev = c.get("evidence")
                if isinstance(ev, list):
                    evidence_tokens.extend(str(x) for x in ev)
                elif ev:
                    evidence_tokens.append(str(ev))
                cl = c.get("claim") or c.get("claim_text") or c.get("text")
                if cl:
                    evidence_tokens.append(str(cl))
    if seven_reports and isinstance(seven_reports, Mapping):
        for rep_k, rep_v in seven_reports.items():
            if rep_v and isinstance(rep_v, str):
                evidence_tokens.append(rep_v)
    ev_verif = manager_verdict.get("evidence_verification")
    if isinstance(ev_verif, list):
        for ev_item in ev_verif:
            if isinstance(ev_item, Mapping):
                evidence_tokens.append(str(ev_item.get("matched_text") or ""))
                evidence_tokens.append(str(ev_item.get("evidence_snippet") or ""))
    evidence_corpus = " \n ".join(evidence_tokens)

    # Check financial metrics in full text
    metric_matches = list(re.finditer(
        r"(?:营业收入|营业总收入|主营业务收入|营收|总收入|净利润|归属于母公司所有者的净利润|归属于上市公司股东的净利润|归母净利润|扣非净利润|毛利率|营业利润)[^\d\n]{0,20}([0-9]+(?:\.[0-9]+)?\s*(?:亿元|万元|元|万|亿|%|万亿元))",
        full_text,
    ))
    en_metric_matches = list(re.finditer(
        r"\b(?:revenue|total revenue|net profit|net income|gross margin|gross profit|operating profit)[^\d\n]{0,25}(\$?[0-9]+(?:\.[0-9]+)?\s*(?:billion|million|bn|m|b|%|yuan|rmb)?)",
        full_text,
        re.IGNORECASE,
    ))

    all_metric_matches = metric_matches + en_metric_matches
    if all_metric_matches:
        for m in all_metric_matches:
            matched_full = m.group(0).strip()
            num_part = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else ""
            clean_num = re.sub(r"[^\d.]", "", num_part)

            # Supported if matches structured actual
            if fund_act_val is not None and fund_act_status == STATUS_AVAILABLE:
                try:
                    if abs(float(clean_num) - float(fund_act_val)) < 1e-4:
                        continue
                except (ValueError, TypeError):
                    pass

            # Supported if present in existing evidence corpus (claims, seven reports, etc.)
            if evidence_corpus:
                if num_part and num_part in evidence_corpus:
                    continue
                if clean_num and len(clean_num) >= 2 and clean_num in evidence_corpus:
                    continue
                if matched_full in evidence_corpus:
                    continue

            # Otherwise, unevidenced hallucination!
            violations.append(f"E-04 守卫拦截：分析师未提供结构化实际财务数值且无既有证据支持，经理不得擅自断言财务指标数值（{matched_full}）")

    # 4. Check if double_count_guard is violated by claiming double voting / extra support
    fund_dc = (fund_er.get("double_count_guard") or {})
    news_dc = (news_er.get("double_count_guard") or {})
    fund_dc_prevent = bool(fund_dc.get("prevent_double_voting", True)) or fund_dc.get("status") in ("accounted_for", "unknown")
    news_dc_prevent = bool(news_dc.get("prevent_double_voting", True)) or news_dc.get("status") in ("accounted_for", "unknown")

    if fund_dc_prevent or news_dc_prevent:
        has_dc_violation = False
        dc_pats_zh = ("双重支持", "额外支持", "双重加票", "额外加票", "两项独立票", "重复计入", "双重印证加票")
        for dc_pat in dc_pats_zh:
            if dc_pat in full_text:
                has_dc_violation = True
                violations.append(f"E-04 守卫拦截：double_count_guard 生效，已计入或未确证事件不得作为额外支持再次加票/计入（命中“{dc_pat}”）")
                break
        if not has_dc_violation:
            m_en_dc = re.search(
                r"\b(?:double|dual|extra|additional)\s+(?:support|voting|votes?)\b|\b(?:counted twice|double counted)\b",
                full_text,
                re.IGNORECASE,
            )
            if m_en_dc:
                violations.append(f"E-04 守卫拦截：double_count_guard 生效，已计入或未确证事件不得作为额外支持再次加票/计入（命中 {m_en_dc.group(0)!r}）")

    return len(violations) == 0, violations


def apply_manager_double_count_guard(
    claim_cluster_metrics: dict[str, Any],
    expectation_revisions: Any,
    claims: Sequence[Mapping[str, Any]] | None = None,
    manager_verdict: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """Enforce double_count_guard in research manager consumption gate (E-04 Defect 4).

    When double_count_guard status is accounted_for or unknown (prevent_double_voting=True):
    1. Deduplicates multiple claims covering the SAME event based on authentic event identity.
    2. Does NOT reduce independent clusters when events are distinct and independent.
    3. Is fully idempotent: repeat invocations do not subtract again.
    4. Strips duplicate event claims from manager_verdict['adopted_claim_ids'] into excluded_evidence.
    5. Records structured audit metadata in metrics.
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

    fund_status = fund_er.get("status")
    news_status = news_er.get("status")
    if fund_status == STATUS_NOT_APPLICABLE and news_status == STATUS_NOT_APPLICABLE:
        metrics = dict(claim_cluster_metrics or {})
        metrics["double_count_guard_active"] = False
        return metrics, manager_verdict, []

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

    if metrics.get("double_count_guard_applied"):
        if manager_verdict and isinstance(manager_verdict, dict):
            excluded = list(manager_verdict.get("excluded_evidence") or [])
            audit = metrics.get("double_count_guard_audit") or {}
            excluded_ids = set(audit.get("excluded_claim_ids") or [])
            if excluded_ids:
                adopted = list(manager_verdict.get("adopted_claim_ids") or [])
                manager_verdict["adopted_claim_ids"] = [cid for cid in adopted if cid not in excluded_ids]
                for cid in excluded_ids:
                    # excluded_evidence 历史元素可能是字符串/空值等非 mapping 形态，跳过其 claim_id 比较但保留元素
                    if not any(isinstance(e, Mapping) and e.get("claim_id") == cid for e in excluded):
                        excluded.append({
                            "claim_id": cid,
                            "reason": "double_count_guard: 同一事件已被既有预测/事件栏位计入，阻止重复加票",
                        })
                manager_verdict["excluded_evidence"] = excluded
        return metrics, manager_verdict, []

    metrics["double_count_guard_active"] = True
    metrics["double_count_guard_applied"] = True

    claims_list = list(claims or [])
    duplicate_claims: list[Mapping[str, Any]] = []
    seen_event_keys: set[str] = set()

    for c in claims_list:
        if not isinstance(c, Mapping):
            continue
        ev_type = str(c.get("event_type") or "").lower()
        txt = str(c.get("claim_text") or c.get("claim") or c.get("text") or "")
        event_id = c.get("event_id")
        cluster_id = c.get("cluster_id")
        evidence = c.get("evidence") or []

        is_event_claim = (
            ev_type in ("event", "fundamental")
            or bool(event_id)
            or bool(cluster_id)
            or any(kw in txt for kw in ("预告", "预测", "快报", "业绩", "财报", "公告", "earnings", "forecast"))
        )
        if not is_event_claim:
            continue

        if event_id:
            event_key = f"event_id:{event_id}"
        elif cluster_id:
            event_key = f"cluster_id:{cluster_id}"
        elif evidence:
            ev_key_str = "|".join(sorted(str(e) for e in evidence))
            event_key = f"evidence:{ev_key_str}"
        else:
            core_subjects = (
                "业绩预告", "业绩预测", "业绩快报", "业绩", "财报", "定期报告", "年报", "半年报", "一季报", "三季报",
                "重大合同", "中标", "采购合同", "采购", "投资", "发明专利", "专利", "增持", "减持", "回购",
            )
            matched_subj = None
            for s in core_subjects:
                if s in txt:
                    matched_subj = s
                    break
            core_preds = (
                "大幅增长", "大幅预增", "预增", "增长", "超预期", "大幅下滑", "预减", "下滑", "扭亏", "减亏", "亏损",
            )
            matched_pred = None
            for p in core_preds:
                if p in txt:
                    matched_pred = p
                    break
            if matched_subj:
                event_key = f"subject:{matched_subj}:{matched_pred or ''}"
            else:
                topic = re.sub(r"^(?:公司|新闻报道|市场传闻|根据公告|基本面|公告|新闻)+", "", txt).strip()
                topic = re.sub(r"[^\w]", "", topic)
                event_key = f"topic:{topic}"

        if event_key in seen_event_keys:
            duplicate_claims.append(c)
        else:
            seen_event_keys.add(event_key)

    blocked_count = len(duplicate_claims)

    if blocked_count > 0:
        orig_indep = metrics.get("independent_cluster_count", 0)
        metrics["independent_cluster_count"] = max(1, orig_indep - blocked_count)
        for dup in duplicate_claims:
            stance = str(dup.get("stance") or "").lower()
            if "bull" in stance or stance == "positive":
                if metrics.get("bull_cluster_count", 0) > 1:
                    metrics["bull_cluster_count"] -= 1
            elif "bear" in stance or stance == "negative":
                if metrics.get("bear_cluster_count", 0) > 1:
                    metrics["bear_cluster_count"] -= 1
            else:
                if metrics.get("bull_cluster_count", 0) > 1:
                    metrics["bull_cluster_count"] -= 1

        metrics["duplicate_voting_prevented"] = True
        metrics["double_count_guard_audit"] = {
            "status": "blocked",
            "reason": "accounted_for 或 unknown 时不得把同一事件作为额外支持/票再次计入",
            "prevent_double_voting": True,
            "blocked_duplicate_votes": blocked_count,
            "excluded_claim_ids": [c.get("claim_id") for c in duplicate_claims if c.get("claim_id")],
        }
    else:
        metrics["duplicate_voting_prevented"] = False
        metrics["double_count_guard_audit"] = {
            "status": "passed",
            "reason": "未检测到针对同一事件的重复计票",
            "prevent_double_voting": True,
            "blocked_duplicate_votes": 0,
            "excluded_claim_ids": [],
        }

    if manager_verdict and isinstance(manager_verdict, dict) and blocked_count > 0:
        dup_ids = {c.get("claim_id") for c in duplicate_claims if c.get("claim_id")}
        adopted = list(manager_verdict.get("adopted_claim_ids") or [])
        manager_verdict["adopted_claim_ids"] = [cid for cid in adopted if cid not in dup_ids]

        excluded = list(manager_verdict.get("excluded_evidence") or [])
        for dup in duplicate_claims:
            cid = dup.get("claim_id")
            # excluded_evidence 历史元素可能是字符串/空值等非 mapping 形态，跳过其 claim_id 比较但保留元素
            if cid and not any(isinstance(e, Mapping) and e.get("claim_id") == cid for e in excluded):
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
            claims=claims,
            seven_reports=seven_reports,
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
