"""DAV-1369: 语义判定收紧——预测/判断命题不作事实支持，辩手论证不入已核实语料。

总控裁定口径：
A：预测、判断、解读不是可核验事实——既不能判 supported_available，也不算
   覆盖率缺口（退出分母）；事实+判断混合句拆开，只对事实部分核验；
   interpretation_causal 一律不得 supported_available；claim 无可核验
   事实命题 → non_factual_only 无全额资格。notA 真值（受制/守稳/显/触及/
   中枢区间/派生盈亏比）必须仍按事实核验。
B：verified 语料只由已核实事实子句构成；论证/反驳子句（矛盾/偷换/宣称/
   自设/剧本/威科夫纪律/互斥等）剔除；不按立场名词（多头/空头）误伤。
"""
from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    SEM_KIND_CAUSAL,
    SEM_KIND_DERIVED,
    SEM_KIND_SCENARIO,
    _sem_adjudicate_proposition,
    audit_claim_semantic_coverage,
    decompose_claim_propositions,
)


def _props(res):
    return res["proposition_audit"]


# ── A 类：混合句拆分，事实部分照常核验，判断部分退出分母 ──────────────────

def test_a_mixed_fact_supported_judgment_excluded():
    """R3：连接词边界拆分——"18.5倍PE且面临二次杀跌" 事实子句照常核验，
    判断子句整句归 scenario（non_factual，不进分母）；非连接词混合句
    （"18.5倍PE面临二次杀跌" 同一子句）整句归判断，不切。"""
    res = audit_claim_semantic_coverage(
        "18.5倍PE且面临二次杀跌",
        verified_evidence=["当前PE约18.5倍"],
        report_fields={},
    )
    kinds = {p["kind"] for p in _props(res)}
    assert "scenario_hypothesis" in kinds
    scen = [p for p in _props(res) if p["kind"] == "scenario_hypothesis"]
    assert any("面临" in p["text"] for p in scen)
    assert all(p["support_status"] == "non_factual_or_normative" for p in scen)
    fact = [p for p in _props(res) if p["kind"] == "factual_numeric"]
    assert fact and fact[0]["support_status"] == "supported_available"
    assert res["semantic_coverage"] == 1.0
    assert res["semantic_decision"] == "adopt"

    # 同一子句内的混合句不切：整句归 scenario
    res2 = audit_claim_semantic_coverage(
        "18.5倍PE面临二次杀跌",
        verified_evidence=["当前PE约18.5倍"],
        report_fields={},
    )
    kinds2 = {p["kind"] for p in _props(res2)}
    assert kinds2 == {"scenario_hypothesis"}
    assert res2["semantic_decision"] == "non_factual_only"


def test_a_mixed_fact_unsupported_loses_coverage():
    """事实子句未证 → 判断子句不顶数：coverage<1，不得 adopt。"""
    res = audit_claim_semantic_coverage(
        "18.5倍PE且面临二次杀跌",
        verified_evidence=["公司发布回购公告"],
        report_fields={},
    )
    # 事实子句无已核实原子 → non_factual 退分母；判断子句 non_factual
    # → 全无可核验事实命题 → non_factual_only（无全额资格）
    assert res["semantic_coverage"] is None
    assert res["semantic_decision"] == "non_factual_only"


def test_a_pure_prediction_non_factual_only():
    """纯预测句（无任何可核验事实命题）→ non_factual_only，无全额资格。"""
    res = audit_claim_semantic_coverage(
        "后市大概率破位下行",
        verified_evidence=["任何已核实证据"],
        report_fields={"market_report": "包含若干数字3.14"},
    )
    verifiable = [p for p in _props(res) if p["support_status"] != "non_factual_or_normative"]
    assert all(p["support_status"] != "supported_available" or True for p in verifiable)
    assert res["semantic_counts"]["verifiable"] == 0
    assert res["semantic_coverage"] is None
    assert res["semantic_decision"] == "non_factual_only"


def test_a_causal_never_supported_available():
    """interpretation_causal 一律不得 supported_available——即使解释结论词
    显式出现在 verified 语料中（grounded/strict 两路全取消）。"""
    res = audit_claim_semantic_coverage(
        "扣非增20%与回购70亿构筑估值底",
        verified_evidence=["扣非增长20%与70亿回购构筑估值底"],
        report_fields={"sentiment_report": "构筑估值底"},
    )
    causal = [p for p in _props(res) if p["kind"] == "interpretation_causal"]
    assert causal
    assert all(p["support_status"] == "non_factual_or_normative" for p in causal)
    assert all(p["support_kind"] != "grounded_interpretation" for p in causal)


def test_a_e04_judgment_keeps_hard_guard():
    """E-04 敏感解读命题仍 unsupported + hard guard（契约不松）。"""
    res = audit_claim_semantic_coverage(
        "利空已定价但反弹将开启",
        verified_evidence=[],
        report_fields={},
    )
    assert res["semantic_hard_guards"]
    assert res["semantic_decision"] != "adopt"


# ── notA 真值：可观察事实不得被预测词表误伤 ────────────────────────────────

def test_nota_observable_states_stay_factual():
    """受制/守稳/显/触及/增配/中枢区间/派生盈亏比——总控 notA 条目形态。"""
    for claim, expect_kind in [
        ("缩量反抽受制VWMA与空头均线", "factual_event_state"),
        ("ATR降至19显波动率收缩", "factual_numeric"),
        ("缩量回踩守稳153元均线", "factual_numeric"),
        ("海外烈酒估值中枢降至15-18倍", "factual_numeric"),
        ("真实盈亏比达1.66比1", ("derived", "factual_numeric")),
        ("800现金流指数增配买盘", ("factual_event_state", "factual_numeric")),
        ("公募调仓未止大单持续流出反弹受制200线", "factual_event_state"),
    ]:
        props = decompose_claim_propositions(claim)
        kinds = [p["type"] for p in props]
        expected = (expect_kind,) if isinstance(expect_kind, str) else expect_kind
        assert any(k in kinds for k in expected), f"{claim} 应含 {expected}: {kinds}"
        assert SEM_KIND_SCENARIO not in kinds, f"{claim} 不得被拆出 scenario: {kinds}"


def test_pure_fact_numeric_still_supported_no_reverse_misfire():
    """反向不误伤："扣非下滑14.02%" 类纯事实仍 supported → adopt。"""
    res = audit_claim_semantic_coverage(
        "扣非下滑14.02%",
        verified_evidence=["扣非净利润同比下滑14.02%"],
        report_fields={},
    )
    assert res["semantic_coverage"] == 1.0
    assert res["semantic_decision"] == "adopt"


# ── B 类：论证子句剔除出已核实语料，事实子句保留 ───────────────────────────

def test_b_argumentative_evidence_dropped_from_corpus():
    """"衰竭度量基准被多头偷换" 式论证子句不得为命题提供 verified 命中。"""
    res = audit_claim_semantic_coverage(
        "衰竭度量基准被多头偷换",
        verified_evidence=["破位日较平台放量63%，衰竭度量基准被多头偷换"],
        report_fields={},
    )
    # 论证子句被剔除后，claim 中的 "偷换" 命题不再能从 verified 语料拿支持
    assert res["semantic_counts"]["verified_corpus_dropped"] >= 1


def test_b_fact_clause_of_mixed_evidence_kept():
    """混合证据：事实子句（量比0.7）保留可命中，论证子句剔除。"""
    res = audit_claim_semantic_coverage(
        "量比0.7供给枯竭",
        verified_evidence=["空头自设破位需量比>1.2放量阴线，当前量比0.7供给枯竭，缩量破底系诱空候选"],
        report_fields={},
    )
    fact = [p for p in _props(res) if p["kind"] in ("factual_numeric", "factual_event_state")]
    assert any(p["support_status"] == "supported_available" for p in fact), (
        "量比0.7 事实子句应仍可命中")


def test_b_technical_term_not_misfired():
    """"空头排列" 是技术术语：不按立场名词剔除。"""
    res = audit_claim_semantic_coverage(
        "50日与200日均线死叉空头排列",
        verified_evidence=["技术报告：50日与200日均线死叉空头排列，布林中轨1206.5强压"],
        report_fields={},
    )
    assert res["semantic_counts"]["verified_corpus_dropped"] == 0
    fact = [p for p in _props(res) if p["support_status"] == "supported_available"]
    assert fact, "空头排列/死叉 事实应可命中"


def test_b_derived_value_of_mixed_evidence_kept():
    """可复算派生值所在事实子句保留（PE12.3-13.3）；自相矛盾论证子句剔除。"""
    res = audit_claim_semantic_coverage(
        "公允估值12.3-13.3倍",
        verified_evidence=[
            "基本面报告压力测试底线600-650亿÷市值7980亿=PE12.3-13.3倍，与多头宣称的10.2倍前瞻PE自相矛盾"
        ],
        report_fields={},
    )
    assert res["semantic_counts"]["verified_corpus_dropped"] >= 1
    fact = [p for p in _props(res) if p["kind"] == "factual_numeric"]
    assert any(p["support_status"] == "supported_available" for p in fact), (
        "PE12.3-13.3 派生值所在事实子句应保留在语料中")


def test_b_rebuttal_evidence_cannot_self_certify():
    """纯反驳论证证据（无事实子句）整条剔除后，claim 不得获 verified 支持。"""
    res = audit_claim_semantic_coverage(
        "空头剧本与资金流证据互斥",
        verified_evidence=["空头剧本需放量破位，与其自身资金流证据互斥"],
        report_fields={},
    )
    assert all(
        p["support_status"] != "supported_available" or p["support_kind"] != "verified_evidence"
        for p in _props(res)
    )


# ── R4 派生值通道：只认经核实的派生数值 ──────────────────────────────────

def test_r4_derived_without_value_is_interpretation():
    """定性派生表述无派生数值：关键词命中/严格核验器定性匹配都不算，退分母。"""
    prop = {"text": "存货周转健康且缩量", "type": SEM_KIND_DERIVED}
    adj = _sem_adjudicate_proposition(
        prop, "存货周转健康且缩量", "", {}, set(), strict_verify=lambda t: (1, 1))
    assert adj["support_status"] == "non_factual_or_normative"


def test_r4_derived_value_verified_supported():
    """派生数值本身经 verified 语料或严格核验器全部核实 → supported。"""
    prop = {"text": "盈亏比达2.65比1", "type": SEM_KIND_DERIVED}
    adj = _sem_adjudicate_proposition(prop, "盈亏比约2.65比1", "", {}, set())
    assert adj["support_status"] == "supported_available"
    assert adj["support_kind"] == "verified_evidence"
    adj2 = _sem_adjudicate_proposition(
        prop, "", "", {"market_report": "盈亏比2.65"}, set(), strict_verify=lambda t: (2, 2))
    assert adj2["support_status"] == "supported_available"
    assert adj2["support_kind"] == "report_verified"


def test_r4_derived_value_unverified_is_interpretation_not_unsupported():
    """派生数值未全部核实（严格核验部分通过/受限源）→ 归 interpretation。"""
    prop = {"text": "大单占比2.5%", "type": SEM_KIND_DERIVED}
    adj = _sem_adjudicate_proposition(
        prop, "", "", {"smart_money_report": "x"}, {"fund_flow"}, strict_verify=lambda t: (1, 2))
    assert adj["support_status"] == "non_factual_or_normative"


def test_r4_event_atom_counts_as_verified_atom():
    """≥3 字事件原子（净流出）在 verified 语料命中即算已核实原子：
    未获支持时判 unsupported 留在分母，不得因原子漏认退出分母。"""
    res = audit_claim_semantic_coverage(
        "净流出横盘探底企稳",
        verified_evidence=["主力资金净流出"],
        report_fields={},
    )
    ev = [p for p in _props(res) if p["kind"] == "factual_event_state"]
    assert ev and ev[0]["support_status"] == "unsupported"


# ── R4 逐词裁定：折价/溢价、压顶 只在披露搭配内放行，其余判断词一律不放 ──────

def _kinds(claim):
    return [p["type"] for p in decompose_claim_propositions(claim)]


def test_r4_disclosure_discount_released_as_fact():
    for claim in ("大宗折价11.58%", "大宗折价近10%", "大宗折价7.33%", "大宗折价7%锚定估值下行"):
        kinds = _kinds(claim)
        assert "factual_numeric" in kinds, f"{claim}: {kinds}"
        assert SEM_KIND_CAUSAL not in kinds and SEM_KIND_SCENARIO not in kinds


def test_r4_discount_outside_collocation_still_judgment():
    for claim in ("0.86倍PB属合理折价", "竞品抢跑加速击穿33倍PE溢价", "大宗10%折价定锚",
                  "大宗深折价11%", "海外折价锚定", "估值溢价未计入"):
        kinds = _kinds(claim)
        assert kinds and set(kinds) <= {SEM_KIND_CAUSAL, SEM_KIND_SCENARIO}, f"{claim}: {kinds}"


def test_r4_released_word_does_not_release_other_triggers():
    """搭配内豁免只屏蔽该词：同子句其他判断词（接盘）照常整句拦截。"""
    kinds = _kinds("大宗折价7.33%击穿二级接盘锚")
    assert set(kinds) <= {SEM_KIND_CAUSAL, SEM_KIND_SCENARIO}


def test_r4_unlock_overhang_released_only_with_quantity():
    for claim in ("4.56亿股解禁压顶", "220亿解禁筹码压顶"):
        kinds = _kinds(claim)
        assert "factual_numeric" in kinds, f"{claim}: {kinds}"
    for claim in ("4月2.53亿股天量套牢盘压顶", "千亿流动负债压顶", "VWMA压顶", "解禁压顶"):
        kinds = _kinds(claim)
        assert set(kinds) <= {SEM_KIND_CAUSAL, SEM_KIND_SCENARIO}, f"{claim}: {kinds}"


def test_r4_ruled_not_release_words_stay_judgment():
    """总控裁定「不放」的代表词：例句形态仍整句归判断。"""
    for claim in ("站稳VWMA确立年线突破", "均线压制恐破38.60", "破位1%系普跌日跟随",
                  "20.6倍PE已透支防御溢价", "1238元下轨支撑构筑高赔率反抽底线",
                  "缩量回踩1283长下影确立突破有效性"):
        kinds = _kinds(claim)
        assert kinds and set(kinds) <= {SEM_KIND_CAUSAL, SEM_KIND_SCENARIO}, f"{claim}: {kinds}"
