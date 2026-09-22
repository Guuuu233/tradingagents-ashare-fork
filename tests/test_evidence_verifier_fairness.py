"""Unit and golden regression tests for evidence verifier fairness and multi-line matching (DAV-338 / Lane C).

Tests:
1. Golden test set execution against tests/golden/evidence_sentences_20260823.json:
   - Positive cases (true facts in reports across single/multiple lines) evaluate to verified.
   - Negative cases (non-existent numbers or coincidental numbers without keyword support) evaluate to unsupported.
2. Granular tests for single-line vs multi-line aggregation.
3. Strict keyword guard to prevent coincidental numeric matches in unrelated contexts.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    STATUS_CONTRADICTED,
)

GOLDEN_PATH = Path(__file__).parent / "golden" / "evidence_sentences_20260823.json"


def load_golden_cases():
    with open(GOLDEN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def evaluator():
    return EvidenceFactualTruthEvaluator()


def test_golden_dataset_exists_and_valid():
    cases = load_golden_cases()
    assert len(cases) >= 15
    pos_cases = [c for c in cases if c.get("is_positive")]
    neg_cases = [c for c in cases if not c.get("is_positive")]
    assert len(pos_cases) >= 12
    assert len(neg_cases) >= 1


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda c: f"{c['case_id']}_{c['symbol']}_{c['claim_id']}")
def test_golden_evidence_sentence_verdicts(case, evaluator):
    """Verify that each golden evidence sentence produces its expected verdict."""
    raw = case["raw_evidence"]
    seven_reports = case["seven_reports"]
    market_data_context = case.get("market_data_context")
    analysis_baseline_date = case.get("analysis_baseline_date")
    claim_id = case.get("claim_id")
    expected = case["expected_status"]

    res = evaluator.evaluate_single_evidence(
        raw_evidence=raw,
        seven_reports=seven_reports,
        market_data_context=market_data_context,
        analysis_baseline_date=analysis_baseline_date,
        claim_id=claim_id,
    )

    actual_status = res.get("status")
    assert actual_status == expected, (
        f"Case {case['case_id']} ({case['symbol']} {claim_id}) expected {expected}, got {actual_status}. "
        f"Details: {res.get('details')}. Raw evidence: {raw}"
    )
    if expected == STATUS_VERIFIED and case.get("is_positive"):
        # Details should explain single-line or multi_line_match
        assert "验证" in res.get("details", "") or "匹配" in res.get("details", "")


def test_negative_case_coincidental_number_without_keyword_rejected(evaluator):
    """Verify that a number appearing in an unrelated context without shared keywords remains unsupported."""
    seven_reports = {
        "volume_price_report": "- **成交量与量比**：8月21日成交 1.274 亿股，量比达 1.40；收盘位置 0.15。",
        "macro_report": "宏观报告内容，无相关财务数据。",
    }
    # Fabricated claim: quotes numbers 1.4 and 0.15 from volume_price_report but with revenue/margin keywords
    fake_evidence = "公司营收同比增长1.4倍且综合毛利率达到0.15"

    res = evaluator.evaluate_single_evidence(
        raw_evidence=fake_evidence,
        seven_reports=seven_reports,
        claim_id="FAKE-1",
    )
    assert res["status"] == STATUS_UNSUPPORTED
    assert "未在七份分析师报告" in res["details"]


def test_negative_case_nonexistent_derived_number_rejected(evaluator):
    """Verify that an evidence sentence containing a non-existent number is rejected."""
    seven_reports = {
        "news_report": "美的集团完成69.73亿元回购，回购均价79.79元/股。",
    }
    # 5.6% is not in the text
    ev_text = "69.73亿回购均价79.79元低于现价5.6%已充分定价"
    res = evaluator.evaluate_single_evidence(
        raw_evidence=ev_text,
        seven_reports=seven_reports,
        claim_id="INV-4",
    )
    assert res["status"] in (STATUS_UNSUPPORTED, STATUS_CONTRADICTED)


def test_debate_state_prompt_templates_symmetry():
    """Verify that both Bull and Bear DEBATE_STATE prompt examples are symmetric in resolved_claim_ids."""
    import re
    from tradingagents.prompts.zh import PROMPTS

    bull_p = PROMPTS["bull_prompt"]
    bear_p = PROMPTS["bear_prompt"]

    # Extract DEBATE_STATE block content
    pattern = re.compile(r"<!--\s*DEBATE_STATE:\s*(\{\{.*?\}\})\s*-->")
    bull_match = pattern.search(bull_p)
    bear_match = pattern.search(bear_p)

    assert bull_match is not None, "DEBATE_STATE block not found in bull_prompt"
    assert bear_match is not None, "DEBATE_STATE block not found in bear_prompt"

    # Both blocks should have `"resolved_claim_ids": []`
    assert '"resolved_claim_ids": []' in bull_match.group(1), f"Bull prompt resolved_claim_ids is not [] in: {bull_match.group(1)}"
    assert '"resolved_claim_ids": []' in bear_match.group(1), f"Bear prompt resolved_claim_ids is not [] in: {bear_match.group(1)}"


# ── DAV-595: Metric-Number Binding & Pseudo Contradiction Tests ──────────────


def test_reproduce_inv6_pseudo_contradiction_falsified_as_unsupported(evaluator):
    """INV-6 pseudo contradiction: '应收账款增长17.10%''营收增长3.55%' must NOT contradict '概率：25%'."""
    seven_reports = {
        "macro_report": (
            "| **乐观情景 (Bull Case)**<br>*(概率：25%)* | - 宏观逆周期货币与财政双发力；"
            "- 海外OBM出海营收加速上行；- 预期收益弹性空间：+8% 至 +15%。 |"
        )
    }
    raw_evidence = "2026Q2应收账款619.55亿元同比大增17.10%，严重背离营收3.55%的微弱增速"
    res = evaluator.evaluate_single_evidence(
        raw_evidence=raw_evidence,
        seven_reports=seven_reports,
        claim_id="INV-6",
    )
    assert res["status"] == STATUS_UNSUPPORTED
    assert res["status"] != STATUS_CONTRADICTED


def test_same_line_multi_metric_no_cross_binding_verified(evaluator):
    """Same-line multiple metrics must bind numbers to their specific metrics, preventing cross-value verification."""
    seven_reports = {
        "fundamentals_report": (
            "- **财务表现**：2026年Q2营业收入2600.42亿元，同比增长3.55%；综合毛利率达25.57%，净利率为9.81%。"
        )
    }
    # 正确匹配自身指标
    res_rev = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2营业收入同比增长3.55%",
        seven_reports=seven_reports,
    )
    assert res_rev["status"] == STATUS_VERIFIED

    res_margin = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2综合毛利率达25.57%",
        seven_reports=seven_reports,
    )
    assert res_margin["status"] == STATUS_VERIFIED


def test_same_line_multi_metric_cross_value_negative_not_verified(evaluator):
    """Negative test: Claiming gross margin is 3.55% or revenue growth is 25.57% must NOT be verified against the same line."""
    seven_reports = {
        "fundamentals_report": (
            "- **财务表现**：2026年Q2营业收入2600.42亿元，同比增长3.55%；综合毛利率达25.57%，净利率为9.81%。"
        )
    }
    # 串值负例 1: 将毛利率说成 3.55% (3.55% 属于营收，不属于毛利率)
    res_fake_margin = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2综合毛利率为3.55%",
        seven_reports=seven_reports,
    )
    assert res_fake_margin["status"] == STATUS_CONTRADICTED

    # 串值负例 2: 将营收增速说成 25.57% (25.57% 属于毛利率，不属于营收)
    res_fake_rev = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2营业收入同比增长25.57%",
        seven_reports=seven_reports,
    )
    assert res_fake_rev["status"] == STATUS_CONTRADICTED


def test_compound_sentence_atomic_evidence_splitting(evaluator):
    """Compound sentence split into atomic statements: each atomic fact must verify against its corresponding report line."""
    seven_reports = {
        "fundamentals_report": (
            "- **营业收入**：2026年Q2单季实现营业收入2600.42亿元，同比增长3.55%。\n"
            "- **营运指标**：2026Q2末应收账款为619.55亿元，同比增长17.10%。"
        )
    }
    compound_ev = "2026Q2应收账款619.55亿元同比大增17.10%，严重背离营收3.55%的微弱增速"
    res = evaluator.evaluate_single_evidence(
        raw_evidence=compound_ev,
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_percentage_conflict_requires_same_metric_unit_period(evaluator):
    """Conflict detection must require matching metric name, unit, and period. Mismatched metric or period cannot trigger contradiction."""
    seven_reports = {
        "macro_report": "| **乐观情景** (概率：25%) | 预计2026Q3海外营收恢复增长 |",
    }
    # 概率 25% 不得与营收 3.55% 冲突
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q2营业收入同比增长3.55%",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_UNSUPPORTED
    assert res["status"] != STATUS_CONTRADICTED


def test_true_conflict_positive_case(evaluator):
    """True conflict positive case: when metric name, unit, and period match but numbers disagree, trigger CONTRADICTED."""
    seven_reports = {
        "fundamentals_report": "2026年Q2综合毛利率为25.57%，营业收入同比增长3.55%。",
    }
    res_conflict = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2综合毛利率为15.00%",
        seven_reports=seven_reports,
    )
    assert res_conflict["status"] == STATUS_CONTRADICTED
    assert "毛利率" in res_conflict.get("details", "")


# =====================================================================
# E-03c Deterministic Verification Scenarios
# =====================================================================

def test_e03c_adopt_partial_reject_exact_coverage_boundaries(evaluator):
    """E-03c: Exact boundaries for adopt (100%), partial (>=67% mixed), and reject (<67% or conflict)."""
    from tradingagents.agents.utils.evidence_verifier import (
        aggregate_claim_evidence,
        DECISION_ADOPT,
        DECISION_PARTIAL,
        DECISION_REJECT,
    )

    # 1. 3/3 verified -> adopt
    s1 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-1", "claim": "论点1", "evidence": ["E1", "E2", "E3"]}],
        claims_verification=[
            {"claim_id": "C-1", "raw": "E1", "status": "verified"},
            {"claim_id": "C-1", "raw": "E2", "status": "verified"},
            {"claim_id": "C-1", "raw": "E3", "status": "verified"},
        ],
    )["C-1"]
    assert s1["decision"] == DECISION_ADOPT
    assert s1["coverage"] == 1.0

    # 2. 2/3 verified (66.7%) -> partial
    s2 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-2", "claim": "论点2", "evidence": ["E1", "E2", "E3"]}],
        claims_verification=[
            {"claim_id": "C-2", "raw": "E1", "status": "verified"},
            {"claim_id": "C-2", "raw": "E2", "status": "verified"},
            {"claim_id": "C-2", "raw": "E3", "status": "unsupported"},
        ],
    )["C-2"]
    assert s2["decision"] == DECISION_PARTIAL
    assert len(s2["verified_evidence"]) == 2
    assert s2["unsupported_evidence"] == ["E3"]
    assert s2["excluded_evidence"] == ["E3"]

    # 3. 1/2 verified (50%) -> reject (coverage < 67%)
    s3 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-3", "claim": "论点3", "evidence": ["E1", "E2"]}],
        claims_verification=[
            {"claim_id": "C-3", "raw": "E1", "status": "verified"},
            {"claim_id": "C-3", "raw": "E2", "status": "unsupported"},
        ],
    )["C-3"]
    assert s3["decision"] == DECISION_REJECT
    assert "覆盖率不足" in s3["reason"]

    # 4. 0/2 verified -> reject (unsupported)
    s4 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-4", "claim": "论点4", "evidence": ["E1", "E2"]}],
        claims_verification=[
            {"claim_id": "C-4", "raw": "E1", "status": "unsupported"},
            {"claim_id": "C-4", "raw": "E2", "status": "unsupported"},
        ],
    )["C-4"]
    assert s4["decision"] == DECISION_REJECT
    assert "未提供有效证据" in s4["reason"]

    # 5. Contradicted evidence -> reject even with verified items
    s5 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-5", "claim": "论点5", "evidence": ["E1", "E2", "E3"]}],
        claims_verification=[
            {"claim_id": "C-5", "raw": "E1", "status": "verified"},
            {"claim_id": "C-5", "raw": "E2", "status": "verified"},
            {"claim_id": "C-5", "raw": "E3", "status": "contradicted"},
        ],
    )["C-5"]
    assert s5["decision"] == DECISION_REJECT
    assert "事实冲突" in s5["reason"]

    # 6. Source unavailable -> reject
    s6 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-6", "claim": "论点6", "evidence": ["E1", "E2", "E3"]}],
        claims_verification=[
            {"claim_id": "C-6", "raw": "E1", "status": "verified"},
            {"claim_id": "C-6", "raw": "E2", "status": "verified"},
            {"claim_id": "C-6", "raw": "E3", "status": "source_unavailable", "is_fatal": True},
        ],
    )["C-6"]
    assert s6["decision"] == DECISION_REJECT
    assert "不可用数据源" in s6["reason"]


def test_e03c_pit_anti_lookahead_date_failure(evaluator):
    """E-03c: Anti-lookahead date check fails closed when evidence date exceeds baseline date."""
    seven_reports = {
        "market_report": "2026-08-20收盘价为1500元，放量突破均线。",
    }
    # 1. Past date matches -> verified
    res_past = evaluator.evaluate_single_evidence(
        raw_evidence="2026-08-20收盘价为1500元",
        seven_reports=seven_reports,
        analysis_baseline_date="2026-08-25",
    )
    assert res_past["status"] == STATUS_VERIFIED

    # 2. Future date without forward keywords -> contradicted (lookahead bias)
    res_future = evaluator.evaluate_single_evidence(
        raw_evidence="2026-09-02收盘价创出新高突破1600元",
        seven_reports=seven_reports,
        analysis_baseline_date="2026-08-25",
    )
    assert res_future["status"] == STATUS_CONTRADICTED
    assert "前视偏差" in res_future["details"]

    # 3. Future date with forward target/prediction keyword -> allowed to not be contradicted
    res_pred = evaluator.evaluate_single_evidence(
        raw_evidence="目标2026-09-02前收盘价有望冲击1600元",
        seven_reports=seven_reports,
        analysis_baseline_date="2026-08-25",
    )
    assert res_pred["status"] != STATUS_CONTRADICTED


def test_e03c_source_unavailable_provider_failure(evaluator):
    """E-03c: Citation of failed data source must be marked source_unavailable, not missing or absent in fact."""
    from tradingagents.agents.utils.evidence_verifier import STATUS_SOURCE_UNAVAILABLE

    market_data_context = {
        "data_failure_ledger": [
            {
                "source": "smart_money",
                "name": "主力资金数据流",
                "status": "failed",
                "provenance_status": "unverified",
            }
        ],
        "source_provenance": {
            "smart_money": {"status": "failed"},
        },
    }
    seven_reports = {
        "smart_money_report": "【数据获取失败】smart_money",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="smart_money主力资金净流入2.5亿元",
        seven_reports=seven_reports,
        market_data_context=market_data_context,
    )
    assert res["status"] == STATUS_SOURCE_UNAVAILABLE
    assert res["is_fatal"] is True
    assert "严重幻觉" in res["details"] or "不可用" in res["details"]


def test_e03c_valid_zero_and_decimal_zero_matching(evaluator):
    """E-03c: Zero values (0, 0.0, Decimal(0)) are valid financial metrics and must match reports."""
    seven_reports = {
        "fundamentals_report": "2026年Q2归母净利润同比增长0.0%，营业收入增长0.0%，资产负债率为0%。",
        "smart_money_report": "主力净流入0亿元，超大单净流出0万元。",
    }
    # 1. 0.0% match
    res_profit = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2归母净利润同比增长0%",
        seven_reports=seven_reports,
    )
    assert res_profit["status"] == STATUS_VERIFIED

    # 2. 0亿元 match
    res_flow = evaluator.evaluate_single_evidence(
        raw_evidence="主力净流入0亿元",
        seven_reports=seven_reports,
    )
    assert res_flow["status"] == STATUS_VERIFIED

    # 3. Disagreeing with 0% triggers contradiction
    res_diff = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2归母净利润同比增长10.0%",
        seven_reports=seven_reports,
    )
    assert res_diff["status"] == STATUS_CONTRADICTED


def test_e03c_observation_and_hypothesis_never_upgraded_to_adopt():
    """E-03c: Observation and hypothesis claims stay in audited partial state and never upgrade to adopt."""
    from tradingagents.agents.utils.evidence_verifier import (
        aggregate_claim_evidence,
        is_observation_or_hypothesis_claim,
        DECISION_PARTIAL,
        DECISION_ADOPT,
    )

    claims = [
        {"claim_id": "CLM-1", "claim": "主力大单净买入", "evidence": ["净流入1亿"], "is_observation": True},
        {"claim_id": "CLM-2", "claim": "【假设】若下周放量突破3000点将加速上涨", "evidence": ["突破60日线"]},
        {"claim_id": "CLM-3", "claim": "题材情绪向好", "claim_type": "hypothesis", "evidence": ["政策支持"]},
        {"claim_id": "CLM-4", "claim": "确凿基本面事实", "claim_type": "fact", "evidence": ["营收2000亿"]},
    ]
    assert is_observation_or_hypothesis_claim(claims[0]) is True
    assert is_observation_or_hypothesis_claim(claims[1]) is True
    assert is_observation_or_hypothesis_claim(claims[2]) is True
    assert is_observation_or_hypothesis_claim(claims[3]) is False

    ver_items = [
        {"claim_id": "CLM-1", "raw": "净流入1亿", "status": "verified"},
        {"claim_id": "CLM-2", "raw": "突破60日线", "status": "verified"},
        {"claim_id": "CLM-3", "raw": "政策支持", "status": "verified"},
        {"claim_id": "CLM-4", "raw": "营收2000亿", "status": "verified"},
    ]
    summary = aggregate_claim_evidence(claims=claims, claims_verification=ver_items)

    assert summary["CLM-1"]["decision"] == DECISION_PARTIAL
    assert summary["CLM-2"]["decision"] == DECISION_PARTIAL
    assert summary["CLM-3"]["decision"] == DECISION_PARTIAL
    assert summary["CLM-4"]["decision"] == DECISION_ADOPT


def test_e03c_preserves_raw_evidence_source_and_state_change_reasons():
    """E-03c: Verifier retains raw evidence, sources, and state change reasons for auditability."""
    from tradingagents.agents.utils.evidence_verifier import aggregate_claim_evidence

    claim = {
        "claim_id": "INV-AUDIT",
        "speaker": "Bull Analyst",
        "speaker_key": "Bull",
        "stance": "bullish",
        "claim": "量价综合立论",
        "evidence": ["放量突破", "虚假指标", "冲突数据"],
    }
    ver_items = [
        {"claim_id": "INV-AUDIT", "raw": "放量突破", "status": "verified", "matched_role": "market_report"},
        {"claim_id": "INV-AUDIT", "raw": "虚假指标", "status": "unsupported"},
        {"claim_id": "INV-AUDIT", "raw": "冲突数据", "status": "contradicted"},
    ]
    summary = aggregate_claim_evidence(claims=[claim], claims_verification=ver_items)
    s = summary["INV-AUDIT"]

    assert s["verified_evidence"] == ["放量突破"]
    assert s["unsupported_evidence"] == ["虚假指标"]
    assert s["contradicted_evidence"] == ["冲突数据"]
    assert "冲突数据" in s["excluded_evidence"]
    assert "虚假指标" in s["excluded_evidence"]
    assert "存在 1 条与报告事实冲突/前视偏差证据" in s["reason"]


# ── DAV-1088: Semantic-Type De-folding + Binding/Match Whitelist ─────────────
# All fixtures below are verbatim production samples from 600036.SH report
# 9e2dd38b79a04819ae619f0078cefbf5 (2026-09-18).

_PROD_FUNDAMENTALS_REPORT = (
    "- 招商银行当前股价 40.59 元，对应 2026H1 每股净资产 45.40 元，**市净率（PB）仅约 0.89 倍**，处于显著“破净”状态。\n"
    "- **净资产规模**：截至 2026-06-30，归属于母公司的股东权益合计为 13,533.01 亿元，"
    "较 2025 年底（12,808.99 亿元）扩张 5.65%；每股净资产达到 **45.40 元**（2025 年底为 43.43 元）。\n"
    "- 2026H1 归母净利润为 764.45 亿元，同比 2025H1（749.30 亿元）增长 **+2.02%**；\n"
    "- **经营活动现金流（OCF）**：\n"
    "  - 2026H1 经营活动产生的现金流量净额达到 **3,046.11 亿元**，相较于 2025H1（1,344.61 亿元）同比暴增 **+126.54%**；"
)

_PROD_MACRO_REPORT = (
    "- 中线基本面扎实：2026年中报实现营业总收入1781.81亿元（同比+4.83%），"
    "净利润764.45亿元（同比+2.02%），行业龙头地位稳固，万亿市值重新收复；"
)


# ── Commit 1 (缺陷 A): 语义类型去折叠 ──

def test_dav1088_a1_proportion_vs_growth_rate_not_contradicted(evaluator):
    """A1 生产 INV-10 原句: 「折损利息约15.3亿元占净利1%」是占比，「净利润同比+2.02%」是同比增速，不可比。"""
    seven_reports = {"macro_report": _PROD_MACRO_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="新闻报告显示个贷新规仅折损利息约15.3亿元占净利1%且已充分定价并加速非标出清",
        seven_reports=seven_reports,
        claim_id="INV-10",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1088_a2_partial_impact_vs_total_not_contradicted(evaluator):
    """A2 生产 INV-12 原句: 「年化利息收入折损15.3亿元」是分项影响额，「营业总收入1781.81亿元」是总量，不可比。"""
    seven_reports = {"macro_report": _PROD_MACRO_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="新闻报告显示个贷新规导致年化利息收入折损15.3亿元且居民去杠杆压制总需求",
        seven_reports=seven_reports,
        claim_id="INV-12",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1088_a3_true_conflict_same_metric_unit_stype_period(evaluator):
    """A3: 同指标、同单位、同语义类型、同期间下的数值矛盾仍须判 contradicted。"""
    seven_reports = {"macro_report": _PROD_MACRO_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026年中报营业总收入1500.50亿元",
        seven_reports=seven_reports,
        claim_id="A3",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1088_a_unit_fold_removed(evaluator):
    """规范化不再把「净利 + %」折叠为净利率、把「净利率 + 元」反向折叠为净利润。"""
    from tradingagents.agents.utils.evidence_verifier import _canonicalize_metric

    assert _canonicalize_metric("净利", "%") != "净利率"
    assert _canonicalize_metric("净利率", "元") != "净利润"


# ── Commit 2 (缺陷 B'): 绑定与匹配白名单化 ──

def test_dav1088_b1_production_inv10_verified(evaluator):
    """B1 生产 INV-10 原句: 两个事实均在 fundamentals_report 中真实存在，应 verified。"""
    seven_reports = {"fundamentals_report": _PROD_FUNDAMENTALS_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="基本面报告显示2026H1每股净资产45.40元且现金流达3046亿元提供极强反脆弱缓冲",
        seven_reports=seven_reports,
        claim_id="INV-10",
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1088_b2_production_inv6_verified_and_not_bound_to_pb(evaluator):
    """B2 生产 INV-6 原句: verified，且 45.40元 的绑定指标不得为 pb。"""
    from tradingagents.agents.utils.evidence_verifier import (
        extract_bound_numbers,
        split_compound_evidence,
    )

    raw = "基本面报告显示2026H1每股净资产45.40元对应PB仅0.89倍且经营现金流达3046亿元"
    bns = []
    for clause in split_compound_evidence(raw):
        bns.extend(extract_bound_numbers(clause))
    bn_4540 = next(b for b in bns if abs(b.val - 45.40) < 1e-6)
    assert bn_4540.metric is not None and bn_4540.metric != "pb"

    seven_reports = {"fundamentals_report": _PROD_FUNDAMENTALS_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence=raw,
        seven_reports=seven_reports,
        claim_id="INV-6",
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1088_b3_pb_clause_optional_consistent_verdict(evaluator):
    """B3: 同一句仅增删句尾「对应PB仅0.89倍」，两种句式结论一致，不得翻转。"""
    seven_reports = {"fundamentals_report": _PROD_FUNDAMENTALS_REPORT}
    res_with = evaluator.evaluate_single_evidence(
        raw_evidence="基本面报告显示2026H1每股净资产45.40元对应PB仅0.89倍且经营现金流达3046亿元",
        seven_reports=seven_reports,
        claim_id="INV-6",
    )
    res_without = evaluator.evaluate_single_evidence(
        raw_evidence="基本面报告显示2026H1每股净资产45.40元且经营现金流达3046亿元",
        seven_reports=seven_reports,
        claim_id="INV-6",
    )
    assert res_with["status"] == res_without["status"] == STATUS_VERIFIED


def test_dav1088_b4_number_absent_still_unsupported(evaluator):
    """B4: 证据数值确实不在任何报告中，仍判 unsupported，不得蒙混通过。"""
    seven_reports = {"fundamentals_report": _PROD_FUNDAMENTALS_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="基本面报告显示2026H1每股净资产45.40元且股息率达9.99%",
        seven_reports=seven_reports,
        claim_id="B4",
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1088_b5_unbound_number_coincidence_not_verified(evaluator):
    """B5: 未绑定指标 + 数值巧合相等（避开 3.1 快速路径），不得 verified。"""
    # 证据中 2.02 为无单位评分(raw)，与报告中净利润同比 +2.02% 单位兼容但指标未绑定；
    # 文本按「，」切分无任何 ≥4 字段落入报告，规避 3.1 两个放行分支。
    seven_reports = {"macro_report": _PROD_MACRO_REPORT}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="量化策略评分录得2.02且置信区间稳定",
        seven_reports=seven_reports,
        claim_id="B5",
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1088_b_binding_unbound_marker_no_cross_sentence_guess(evaluator):
    """绑定侧: 数字无法确定指标时产出显式未绑定标记，不得绑到句中碰巧出现的其他指标。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    # 「45.40元」与「PB」之间隔着关系动词「对应」，不得把 45.40 误绑给 pb；
    # 句中无「每股净资产」词表命中时须显式未绑定(metric=None)。
    bns = extract_bound_numbers("资产价格45.40元对应PB仅0.89倍")
    bn_4540 = next(b for b in bns if abs(b.val - 45.40) < 1e-6)
    assert bn_4540.metric != "pb"


# ── DAV-1144: Metric Binding 基础层 ───────────────────────────────────────


def test_dav1144_canonical_metric_completion_price_and_holder_count():
    """DAV-1144: 股价/股东户数 入 canonical 词表；户数单位=户，不归一为元。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("股价在22.76元")[0]
    assert bn.metric == "股价"

    bn = extract_bound_numbers("净利润2.46亿元")[0]
    assert bn.metric == "净利润"

    bn = extract_bound_numbers("股东户数增至18.81万户")[0]
    assert bn.metric == "股东户数"
    assert bn.unit == "户"
    assert abs(bn.val - 188100.0) < 1e-6

    bn = extract_bound_numbers("融资净偿还76.69万")[0]
    assert bn.metric == "两融"


def test_dav1144_no_cross_comma_metric_inheritance():
    """DAV-1144: 跨逗号无关数字不得继承前一指标（股东户数不得绑两融）。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("融资净偿还76.69万，股东户数18.81万户")
    bn_margin = next(b for b in bns if abs(b.val - 766900.0) < 1e-3)
    bn_holders = next(b for b in bns if b.unit == "户")
    assert bn_margin.metric == "两融"
    assert bn_holders.metric == "股东户数"


def test_dav1144_paren_yoy_binds_nearest_amount_metric():
    """DAV-1144: 金额+括号同比配对绑最近主体指标：-14.73% 绑净利不得绑营收。"""
    from tradingagents.agents.utils.evidence_verifier import (
        STYPE_GROWTH,
        extract_bound_numbers,
    )

    bns = extract_bound_numbers("营收70.02亿(-17.02%)，归母净利2.46亿(-14.73%)")
    bn_rev_yoy = next(b for b in bns if abs(b.val + 17.02) < 1e-6)
    bn_np_yoy = next(b for b in bns if abs(b.val + 14.73) < 1e-6)
    assert bn_rev_yoy.metric == "营收"
    assert bn_np_yoy.metric == "净利润"
    assert bn_rev_yoy.stype == STYPE_GROWTH
    assert bn_np_yoy.stype == STYPE_GROWTH


# ── DAV-1159: Metric Binding 残漏收尾（贴息/负债率/裂口/拨备）─────────────


def test_dav1159_percent_possessive_binds_following_subject():
    """DAV-1159: 「X%的<科目>」所有格——% 量化紧随其后的科目名词，不得绑前向指标。

    「压降负债率69.4%的利息支出」中 69.4% 是利息支出的降幅，绑到资产负债率
    后会与 60% 阈值互判伪冲突。
    """
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("贴息扩围与低利率环境显著压降负债率69.4%的利息支出")[0]
    assert bn.metric == "利息支出"

    bn = extract_bound_numbers("资产负债率60%")[0]
    assert bn.metric == "资产负债率"


def test_dav1159_gap_and_provision_canonical_landing():
    """DAV-1159: 裂口/拨备 入 canonical 词表——不再回退错绑净利润/每股净资产。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("净利润5.2亿元且资金裂口68.5亿元")
    bn_gap = next(b for b in bns if abs(b.val - 6850000000.0) < 1e-3)
    assert bn_gap.metric == "裂口"

    bns = extract_bound_numbers("每股净资产8.5元拨备30-40亿元")
    bn_prov = next(b for b in bns if abs(b.val - 4000000000.0) < 1e-3)
    assert bn_prov.metric == "拨备"

    bn = extract_bound_numbers("计提30亿的拨备")[0]
    assert bn.metric == "拨备"


# ── DAV-1145: Entity / Comparison Scope ────────────────────────────────────


def test_dav1145_entity_binding_extraction():
    """DAV-1145: 主体抽取——公司名/代码/行业基准入 BoundNumber.entity；无主体保持 None。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("美的毛利率为26.39%")[0]
    assert bn.entity == "co:美的"

    bn = extract_bound_numbers("奥克斯毛利率18.8%")[0]
    assert bn.entity == "co:奥克斯"

    bn = extract_bound_numbers("大金单月跌15.58%")[0]
    assert bn.entity == "co:大金"

    bn = extract_bound_numbers("恒瑞医药2026年Q2毛利率高达86.33%")[0]
    assert bn.entity == "co:恒瑞医药"

    bn = extract_bound_numbers("行业均值毛利率为18.8%")[0]
    assert bn.entity == "bench:行业均值"

    # 无主体提及 → None（默认报告目标股）
    bn = extract_bound_numbers("2026年Q2综合毛利率为25.57%")[0]
    assert bn.entity is None

    # 同子句双主体 → 归属歧义标记
    bn = extract_bound_numbers("美的集团与奥克斯集团毛利率为26.39%")[0]
    assert bn.entity == "ambig"


def test_dav1145_cross_entity_same_metric_not_contradicted(evaluator):
    """美的毛利率26.39% vs 奥克斯18.8%：同指标跨主体不得互判冲突，须记 entity gap。"""
    seven_reports = {
        "fundamentals_report": "- **同业对比**：奥克斯同期毛利率为18.8%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="美的毛利率为26.39%",
        seven_reports=seven_reports,
        claim_id="ENT-1",
    )
    assert res["status"] == STATUS_UNSUPPORTED
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("entity_scope_gaps")


def test_dav1145_named_vs_unspecified_entity_not_contradicted(evaluator):
    """单侧指明主体、另一侧未指明（默认目标股）→ 主体不可比，按最保守不判冲突 + 记 gap。"""
    seven_reports = {
        "fundamentals_report": "- **财务表现**：2026年Q2综合毛利率为25.57%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="奥克斯2026年Q2毛利率为15.00%",
        seven_reports=seven_reports,
        claim_id="ENT-2",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("entity_scope_gaps")


def test_dav1145_same_entity_true_conflict_still_contradicted(evaluator):
    """同一公司同指标同期间真实矛盾仍判冲突（防过宽）。"""
    seven_reports = {
        "fundamentals_report": "- **财务表现**：美的2026年Q2毛利率为25.57%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="美的2026年Q2毛利率为15.00%",
        seven_reports=seven_reports,
        claim_id="ENT-3",
    )
    assert res["status"] == STATUS_CONTRADICTED
    assert "毛利率" in res.get("details", "")


def test_dav1145_both_unspecified_true_conflict_still_contradicted(evaluator):
    """双侧均未指明主体（默认同一报告目标股）→ 可比，真矛盾仍拦。"""
    seven_reports = {
        "fundamentals_report": "2026年Q2综合毛利率为25.57%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2综合毛利率为15.00%",
        seven_reports=seven_reports,
        claim_id="ENT-4",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1145_benchmark_vs_stock_value_not_contradicted(evaluator):
    """行业均值基准 vs 个股值：主体类型不同不可比，不判冲突。"""
    seven_reports = {
        "fundamentals_report": "- **行业对比**：行业均值毛利率为18.8%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="美的毛利率为26.39%",
        seven_reports=seven_reports,
        claim_id="ENT-5",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1145_ambiguous_entity_conservative_no_contradiction(evaluator):
    """同子句多主体归属歧义 → 主体抽取失败按最保守：不判冲突 + 记 gap，不误杀。"""
    seven_reports = {
        "fundamentals_report": "- **财务表现**：美的集团毛利率为18.80%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="美的集团与奥克斯集团毛利率为26.39%",
        seven_reports=seven_reports,
        claim_id="ENT-6",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("entity_scope_gaps")


# ── DAV-1146: Semantic Role / Scenario / Period 进 binding key ─────────────


def test_dav1146_role_and_basis_extraction():
    """DAV-1146: 语义角色/期间基准入 BoundNumber.role/basis；无修饰默认 actual/None。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("实际换手率为1.11%")[0]
    assert bn.role == "actual" and bn.basis is None

    bn = extract_bound_numbers("单日换手率达20%即登龙虎榜披露阈值")[0]
    assert bn.role == "threshold"

    bn = extract_bound_numbers("压力测试情景下净利润或降至335亿")[0]
    assert bn.role == "scenario"

    bn = extract_bound_numbers("增量营收贡献17亿元")[0]
    assert bn.role == "incremental"

    bn = extract_bound_numbers("单季ROE为3.595%")[0]
    assert bn.role == "actual" and bn.basis == "single_quarter"

    bn = extract_bound_numbers("年化ROE为14.38%")[0]
    assert bn.basis == "annualized"


def test_dav1146_actual_vs_threshold_not_contradicted(evaluator):
    """1.11%实际换手 vs 20%龙虎榜披露阈值：actual vs threshold 不判冲突 + 记 gap。"""
    seven_reports = {
        "market_report": "- **异动规则**：单日换手率达20%即登龙虎榜披露阈值。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="实际换手率为1.11%",
        seven_reports=seven_reports,
        claim_id="ROLE-1",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1146_scenario_vs_actual_not_contradicted(evaluator):
    """压力测试净利335亿 vs 实际净利445亿：scenario vs actual 不判冲突。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：2026年实际净利润为445亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="压力测试情景下2026年净利润或降至335亿",
        seven_reports=seven_reports,
        claim_id="ROLE-2",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1146_incremental_vs_total_not_contradicted(evaluator):
    """增量营收17亿 vs 总营收4565亿：incremental vs actual(total) 不判冲突。"""
    seven_reports = {
        "fundamentals_report": "- **营收**：全年总营收4565亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="新业务增量营收贡献17亿元",
        seven_reports=seven_reports,
        claim_id="ROLE-3",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1146_single_quarter_vs_annualized_not_contradicted(evaluator):
    """单季ROE 3.595% vs 年化ROE 14.38%：期间基准不同不判冲突 + 记 gap。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：年化ROE为14.38%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="单季ROE为3.595%",
        seven_reports=seven_reports,
        claim_id="ROLE-4",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1146_same_role_same_basis_true_conflict_still_contradicted(evaluator):
    """同角色同语义同期间真矛盾仍拦（防过宽）：actual/无基准标注 双侧一致。"""
    seven_reports = {
        "fundamentals_report": "- **财务表现**：2026年Q2净利润为25.57亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2净利润为15.00亿元",
        seven_reports=seven_reports,
        claim_id="ROLE-5",
    )
    assert res["status"] == STATUS_CONTRADICTED
    assert "净利润" in res.get("details", "")


def test_dav1146_same_scenario_true_conflict_still_contradicted(evaluator):
    """同 scenario 角色下真矛盾仍拦：压力测试净利335亿 vs 压力测试净利445亿。"""
    seven_reports = {
        "fundamentals_report": "- **压力测试**：极端情景下净利润或降至445亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="压力测试情景下净利润或降至335亿",
        seven_reports=seven_reports,
        claim_id="ROLE-6",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1146_disclosed_actual_value_not_threshold(evaluator):
    """返修回归：「中报披露净利445亿」中「披露」修饰实际披露值，不得误判为 threshold；
    与报告净利15亿的真矛盾仍判 contradicted（防假阴性）。"""
    seven_reports = {
        "fundamentals_report": "- **财务表现**：2026年净利润为15.00亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="中报披露归母净利润为445亿元",
        seven_reports=seven_reports,
        claim_id="ROLE-7",
    )
    assert res["status"] == STATUS_CONTRADICTED
    assert "净利润" in res.get("details", "")


def test_dav1146_anaphoric_prev_clause_not_polluting():
    """返修回归：「阈值如上，实际换手1.11%」前子句为回指性陈述，不得并入语境
    污染实际值——提取 role 必须为 actual。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("阈值如上，实际换手1.11%")[0]
    assert bn.role == "actual"


def test_dav1146_mixed_marker_priority_threshold_over_scenario():
    """角色优先级固化 threshold > scenario：「极端压力测试…净利底线」同时含
    情景词与界线词，归 threshold。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("极端压力测试显示极悲观年化净利底线60亿元")[0]
    assert bn.role == "threshold"
    assert bn.basis == "annualized"


# ── DAV-1157: 残余修复——合计vs分项/降幅vs水平/期间角色不互判冲突 ──────────


def test_dav1157_role_and_basis_extraction():
    """DAV-1157: aggregate/component/delta 角色与 yoy/mom 基准提取。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("超大单加大单净流入0.9122亿元")[0]
    assert bn.role == "aggregate"

    bn = extract_bound_numbers("合计超大单净流入0.9122亿元")[0]
    assert bn.role == "aggregate"

    bns = extract_bound_numbers("合计净流入5亿元，其中超大单3亿元")
    assert bns[0].role == "aggregate" and bns[1].role == "component"

    bns = extract_bound_numbers("LPR环比大跌10.45%至3.00%")
    assert bns[0].role == "delta" and bns[0].basis == "mom"
    # 「至3.00%」是变动后的水平值，归 actual 而非 delta
    assert bns[1].role == "actual"

    bn = extract_bound_numbers("净利润同比增长10%")[0]
    assert bn.role == "delta" and bn.basis == "yoy"

    # 水平值后的「（同比+X%）」括号注释不得把水平值标成 yoy（DAV-1088 A3 防退）
    bn = extract_bound_numbers("2026年中报实现营业总收入1781.81亿元（同比+4.83%）")[0]
    assert bn.role == "actual" and bn.basis is None


def test_dav1157_per_number_period_binding():
    """同一行两期共存：后置括号期间逐数字补绑，不再被整句期间压平。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("毛利率由37.91%（2024）降至25.48%（2026H1）")
    assert bns[0].period == "2024" and bns[1].period == "2026H1"

    bns = extract_bound_numbers("2024年营收100亿元")
    assert bns[0].period == "2024"

    # 返修回归：前置千分位逗号数字不得使期间绑定坐标系错位（cleaned 去逗号
    # 不保位）——37.91% 必须仍绑 2024 而非被整句 2026H1 压平
    bns = extract_bound_numbers("营收1,234.56亿元，毛利率由37.91%（2024）降至25.48%（2026H1）")
    pct_bns = [b for b in bns if b.unit == "%"]
    assert pct_bns[0].period == "2024" and pct_bns[1].period == "2026H1"


def test_dav1157_thousands_comma_period_conflict_still_contradicted(evaluator):
    """返修回归：千分位前置 + 两期共存，37.91%（2024）vs 报告 2024 毛利率
    40.00% 的同期间真矛盾仍判 contradicted（防静默放行）。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：2024年毛利率为40.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="营收1,234.56亿元，毛利率由37.91%（2024）降至25.48%（2026H1）",
        seven_reports=seven_reports,
        claim_id="AGG-6",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1157_consolidated_statement_not_aggregate():
    """返修回归：「合并报表净利润50亿」是实际值表述，裸「合并」不得误标
    aggregate——role 必须为 actual。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("合并报表净利润50亿元")[0]
    assert bn.role == "actual"


def test_dav1157_aggregate_vs_component_not_contradicted(evaluator):
    """合计超大单净流入0.9122亿 vs 分项超大单净流入0.5亿：aggregate vs
    actual(component) 不判冲突 + 记 semantic_role_gaps。"""
    seven_reports = {
        "market_report": "- **资金流**：超大单净流入0.5亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="合计超大单净流入0.9122亿元",
        seven_reports=seven_reports,
        claim_id="AGG-1",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1157_delta_vs_level_not_contradicted(evaluator):
    """净利润下调10.45%（delta）vs 净利润为12.00%（actual/未标基准）：
    降幅与水平值不互判冲突 + 记 gap。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：净利润为12.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="净利润下调10.45%",
        seven_reports=seven_reports,
        claim_id="AGG-2",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1157_yoy_vs_mom_not_contradicted(evaluator):
    """净利润同比增长30% vs 净利润环比增长12%：同为 delta 但同比/环比基准
    不同，不互判冲突 + 记 gap。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：净利润环比增长12.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="净利润同比增长30.00%",
        seven_reports=seven_reports,
        claim_id="AGG-3",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("semantic_role_gaps")


def test_dav1157_same_period_same_role_true_conflict_still_contradicted(evaluator):
    """防过宽：2026H1 毛利率 25.48% vs 2026H1 毛利率 30.00%，
    同期间同语义同主体不同值仍判 contradicted。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：2026H1毛利率为30.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026H1毛利率为25.48%",
        seven_reports=seven_reports,
        claim_id="AGG-4",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1157_cross_period_same_metric_not_contradicted(evaluator):
    """同行两期共存不再被压平互判：2024 毛利率 37.91% vs 2024 毛利率 40.00%
    仍判冲突（真矛盾）；而 2026H1 值不得与 2024 记录互判。"""
    seven_reports = {
        "fundamentals_report": "- **盈利**：2024年毛利率为40.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="毛利率由37.91%（2024）降至25.48%（2026H1）",
        seven_reports=seven_reports,
        claim_id="AGG-5",
    )
    # 37.91%（2024）vs 40.00%（2024）为同期间真矛盾，仍拦
    assert res["status"] == STATUS_CONTRADICTED


# ── DAV-1091: is_fatal 独立严重度位在证据核验器中的消费契约 ─────────────────


def test_dav1091_aggregate_claim_evidence_is_fatal_contract():
    """DAV-1091: aggregate_claim_evidence 在 claims 汇总中保持 is_fatal 独立位。"""
    from tradingagents.agents.utils.evidence_verifier import aggregate_claim_evidence

    # 1. contradicted + is_fatal=False -> summary["is_fatal"] 为 False，decision 为 reject
    sum_1 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-1", "claim": "命题1"}],
        claims_verification=[{"claim_id": "C-1", "status": "contradicted", "is_fatal": False, "raw": "冲突"}],
    )
    assert sum_1["C-1"]["decision"] == "reject"
    assert sum_1["C-1"]["is_fatal"] is False

    # 2. contradicted + is_fatal=True -> summary["is_fatal"] 为 True
    sum_2 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-2", "claim": "命题2"}],
        claims_verification=[{"claim_id": "C-2", "status": "contradicted", "is_fatal": True, "raw": "致命冲突"}],
    )
    assert sum_2["C-2"]["decision"] == "reject"
    assert sum_2["C-2"]["is_fatal"] is True

    # 3. source_unavailable + is_fatal=False -> summary["is_fatal"] 为 False (按显式 is_fatal 判定不升级)
    sum_3 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-3", "claim": "命题3"}],
        claims_verification=[{"claim_id": "C-3", "status": "source_unavailable", "is_fatal": False, "raw": "非致命不可用"}],
    )
    assert sum_3["C-3"]["decision"] == "reject"
    assert sum_3["C-3"]["is_fatal"] is False

    # 4. source_unavailable + is_fatal=True -> summary["is_fatal"] 为 True
    sum_4 = aggregate_claim_evidence(
        claims=[{"claim_id": "C-4", "claim": "命题4"}],
        claims_verification=[{"claim_id": "C-4", "status": "source_unavailable", "is_fatal": True, "raw": "真致命幻觉"}],
    )
    assert sum_4["C-4"]["decision"] == "reject"
    assert sum_4["C-4"]["is_fatal"] is True


def test_dav1091_evaluate_challenges_is_fatal_contract(evaluator):
    """DAV-1091: evaluate_challenges 中 source_unavailable + is_fatal=False 不升级为 fatal unavailable。"""
    # 构造单个不可用源挑战，is_fatal=False 情况下不得升级
    challenges = [
        {
            "challenge_id": "CH-1",
            "target_claim_id": "C-1",
            "speaker": "bear",
            "severity": "fatal",
            "evidence": ["非致命不可用源指标引用"],
        }
    ]
    # 使用打桩验证项直接注入
    res = evaluator.evaluate_challenges(
        challenges=challenges,
        seven_reports={},
    )
    assert len(res) == 1
    # 正常无报告且无 failure_ledger 时判定为 unsupported
    assert res[0]["evidence_status"] in ("unsupported", "contradicted")


# ── DAV-1147: Cross-report Evidence Aggregation（跨报告事实拼合恢复 coverage）──


def test_dav1147_verbatim_atomic_clauses_across_reports(evaluator):
    """牧原型假 unsupported：复合证据的原子事实逐字分处不同报告 → 聚合 verified。

    「头均减亏40%」在 fundamentals、「主力净流出2.89亿元」在 smart_money——
    原文逐字存在，不得判 unsupported。（用「；」分隔避开 3.1 逗号切片预命中，
    确保走 verbatim_atomic_aggregation 路径。）
    """
    seven_reports = {
        "fundamentals_report": "成本改善显著，头均减亏40%。\n",
        "smart_money_report": "当日主力净流出2.89亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="头均减亏40%；主力净流出2.89亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED
    assert "verbatim_atomic_aggregation" in res["details"]
    assert "fundamentals" in res["matched_source"]
    assert "smart_money" in res["matched_source"]


def test_dav1147_verbatim_aggregation_requires_all_clauses(evaluator):
    """逐字聚合是合取：任一 ≥4 字子句未命中即不放行（不得借部分命中强拼）。"""
    seven_reports = {
        "fundamentals_report": "成本改善显著，头均减亏40%。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="头均减亏40%；主力净流出9.99亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1147_verbatim_aggregation_tolerates_report_line_wrap(evaluator):
    """报告文本换行/空白差异不得破坏逐字命中。"""
    seven_reports = {
        "fundamentals_report": "头均减亏\n40%。\n",
        "smart_money_report": "主力净流出 2.89亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="头均减亏40%；主力净流出2.89亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1147_numeric_facts_joined_across_reports(evaluator):
    """中兴 INV-10 型：现金/流动比率在 fundamentals、回购区间在 news → 跨报告数值拼合 verified。

    依赖：货币资金↔现金 canonical 归一、流动比率词表补全、10–12亿 全角 dash 单位继承。
    """
    seven_reports = {
        "fundamentals_report": (
            "资产负债与流动性：\n"
            "报告期末货币资金余额337.51亿元，流动性充裕。\n"
            "流动比率为1.76，短期偿债能力稳健。\n"
        ),
        "news_report": (
            "公司公告：拟以10亿元至12亿元回购公司股份。\n"
        ),
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="货币资金337.51亿元，流动比率1.76，拟10–12亿元回购",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED
    assert "fundamentals" in res["matched_source"]
    assert "news" in res["matched_source"]


def test_dav1147_cross_report_join_rejects_different_period(evaluator):
    """不同期间的同值不得跨报告强拼：2026H1 净利润不得拿 2025H1 的同值佐证。"""
    seven_reports = {
        "fundamentals_report": "2025H1公司实现净利润15亿元。\n",
        "news_report": "公司拟以10亿元回购股份。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026H1净利润15亿元，拟10亿元回购",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1147_cross_report_join_rejects_different_entity(evaluator):
    """不同主体的同值不得跨报告强拼：奥克斯的毛利率不得佐证美的的同名指标。"""
    seven_reports = {
        "fundamentals_report": "奥克斯毛利率为18.8%，低于同业。\n",
        "news_report": "公司拟以10亿元回购股份。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="美的毛利率18.8%，拟10亿元回购",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1147_en_dash_range_unit_inheritance():
    """全角 en-dash「10–12亿元」前半截须继承单位亿元（归一为元量纲）。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("拟10–12亿元回购")
    vals_units = {(b.val, b.unit) for b in bns}
    assert (10 * 100_000_000.0, "元") in vals_units
    assert (12 * 100_000_000.0, "元") in vals_units


def test_dav1147_claim_level_coverage_restored(evaluator):
    """端到端 claim 覆盖：分处 fundamentals/news 的三条事实全部 verified → coverage 100%。

    中兴 INV-10 型 claim：修复前货币资金/流动比率/回购子句各自拼不上 → 50% coverage；
    修复后逐字+数值两条聚合路径均放行。
    """
    from tradingagents.agents.utils.evidence_verifier import aggregate_claim_evidence

    seven_reports = {
        "fundamentals_report": (
            "报告期末货币资金余额337.51亿元。\n"
            "流动比率为1.76。\n"
        ),
        "news_report": "公司公告拟以10亿元至12亿元回购股份。\n",
    }
    claims = [
        {
            "claim_id": "INV-10",
            "speaker_key": "Bull",
            "claim": "现金充裕且回购托底",
            "evidence": [
                "货币资金337.51亿元",
                "流动比率1.76",
                "拟10–12亿元回购",
            ],
        }
    ]
    vers = evaluator.evaluate_claims(claims=claims, seven_reports=seven_reports)
    assert all(v["status"] == STATUS_VERIFIED for v in vers)
    summary = aggregate_claim_evidence(claims=claims, claims_verification=vers)
    assert summary["INV-10"]["coverage"] == 1.0
    assert summary["INV-10"]["decision"] in ("adopt", "partial")


# ── DAV-1163: 复合句覆盖坍缩修复（区间/约数/方向界/canonical补全/逐事实计分）──


def test_dav1163_min_bound_superior_value_covered(evaluator):
    """「超X」为下界声明：报告值 ≥ X 即覆盖（「在手现金超330亿」被337.51亿覆盖）。"""
    seven_reports = {
        "fundamentals_report": "报告期末在手现金337.51亿元，流动性充裕。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="在手现金超330亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_min_bound_lower_value_not_covered(evaluator):
    """「超X」下界不成立时不得放行：报告值 310 < 330 仍须 unsupported。"""
    seven_reports = {
        "fundamentals_report": "报告期末在手现金310.00亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="在手现金超330亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_UNSUPPORTED


def test_dav1163_bound_not_value_conflict(evaluator):
    """方向界不是点值：「超100亿」与「130亿」是覆盖而非冲突，不得判 contradicted。"""
    seven_reports = {
        "news_report": "公司公告拟回购130亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="公司拟回购超100亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_evidence_range_covered_by_point_inside(evaluator):
    """证据区间「67-77元」被区间内报告值覆盖（市值底线族）。"""
    seven_reports = {
        "fundamentals_report": "极端压力测试下公司市值底线为77元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="极端压力测试对应市值底线67-77元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_point_not_covered_by_report_range(evaluator):
    """报告侧区间不得反向覆盖证据点值（区间内取值是衍生值，golden CASE-001 红线）。"""
    seven_reports = {
        "news_report": "美的集团完成69.73亿元回购，回购均价79.79元/股。\n",
        "macro_report": "标的股价可能出现5%-8%的阶段性回调。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="69.73亿回购均价79.79元低于现价5.6%已充分定价",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1163_approx_tolerance_widened(evaluator):
    """「约X」约数容差放宽：「约100亿」被「105亿」覆盖。"""
    seven_reports = {
        "news_report": "公司公告拟回购105亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="公司拟回购约100亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_rounding_equivalence_same_value(evaluator):
    """舍入/精度差同值不判失配：「3.70亿」与「+3.7045亿」是同一数值。"""
    seven_reports = {
        "smart_money_report": "小单分组单日逆势净买入 +3.7045 亿元。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="小单净流入3.70亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_canonical_small_order_binding(evaluator):
    """「小单/中单」canonical 补全：证据「小单净流入」绑小单而非回退主力。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("主力资金小单逆势净流入3.70亿元")
    assert bns and bns[0].metric == "小单"


def test_dav1163_ma_family_canonical(evaluator):
    """均线族（EMA/SMA/日均线）归一：「10EMA(91.14)」与「10日均线91.14」同族匹配。"""
    seven_reports = {
        "market_report": "现价高于10日均线91.14元与50日均线89.28元，多头排列。\n",
        "volume_price_report": "收盘价92.32元，短中期均线多头排列。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="收盘价92.32稳居10EMA(91.14)与50SMA(89.28)之上",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1163_composite_partial_fact_scoring(evaluator):
    """逐事实独立计分：复合句单点失配不拖垮整条，已命中事实计 verified。"""
    from tradingagents.agents.utils.evidence_verifier import aggregate_claim_evidence

    seven_reports = {
        "fundamentals_report": "2026Q1归母净利润同比下滑 -46.58%。\n",
        "news_report": "公司公告拟回购超100亿元。\n",
    }
    claims = [
        {
            "claim_id": "INV-1",
            "speaker_key": "Bull",
            "claim": "利润下滑但回购托底",
            "evidence": [
                "2026Q1归母净利润同比下滑46.58%，且公司拟回购超100亿元，同期营收999.99亿",
            ],
        }
    ]
    vers = evaluator.evaluate_claims(claims=claims, seven_reports=seven_reports)
    stats = [v["status"] for v in vers]
    assert STATUS_VERIFIED in stats, "已命中事实须计 verified"
    assert STATUS_UNSUPPORTED in stats, "虚构的999.99亿仍须 unsupported"
    summary = aggregate_claim_evidence(claims=claims, claims_verification=vers)
    # 2/3 事实获验 → coverage 66.7% → partial 而非 reject
    assert summary["INV-1"]["coverage"] > 0.6
    assert summary["INV-1"]["decision"] == "partial"


def test_dav1163_leadin_attribution_not_atomic_clause(evaluator):
    """出处引导语（根据X报告/报告显示）不作 unsupported 原子子句阻断逐事实计分。"""
    seven_reports = {
        "market_report": "股价在52.60-52.80元区间多次探底企稳，RSI达41已钝化。\n",
    }
    items = evaluator._verify_evidence_or_decompose(
        "根据市场分析师报告，股价在52.60-52.80元区间多次探底企稳且RSI达41已钝化",
        seven_reports, None, None, "C1", None,
    )
    assert any(i["status"] == STATUS_VERIFIED for i in items)


def test_dav1163_true_missing_fact_still_unsupported(evaluator):
    """红线：实际缺失仍须 unsupported——不放宽 verified 标准换分。"""
    seven_reports = {
        "fundamentals_report": "2026Q1归母净利润同比下滑 -46.58%。\n",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1存货减值损失99.99亿元",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_UNSUPPORTED


def test_dav1163_price_metric_demoted_for_paren_quantile(evaluator):
    """「收盘处于日内绝对低位（0.03）」中 0.03 是分位值，不得绑严格股价。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("K线收盘处于日内绝对低位（0.03）")
    bn = [b for b in bns if b.raw == "0.03"][0]
    assert bn.metric is None


def test_dav1163_price_metric_demoted_for_percent(evaluator):
    """「低于现价5.6%」中 5.6% 是差值比率，不得绑严格股价引发伪冲突。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("回购均价79.79元低于现价5.6%已充分定价")
    bn = [b for b in bns if b.raw == "5.6%"][0]
    assert bn.metric is None


def test_dav1163_yoy_anchor_shifts_period_back_one_year():
    """「去年同期+311.37亿」锚定前一年度期间；其后的当期数字不前移。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("2026H1经营现金流由去年同期+311.37亿元骤降至-21.54亿元")
    by_raw = {b.raw: b for b in bns}
    assert by_raw["+311.37亿元"].period == "2025H1"
    assert by_raw["-21.54亿元"].period == "2026H1"


def test_dav1164_negative_range_signed_span_and_coverage():
    """负区间带符号归一：「-5%至-3%」range_span=(-5,-3)，被 -4% 覆盖、+4% 不覆盖。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("波动区间-5%至-3%")
    spans = {b.range_span for b in bns if b.range_span}
    assert spans == {(-5.0, -3.0)}


def test_dav1164_negative_range_covered_by_inside_point(evaluator):
    seven_reports = {"news_report": "波动区间为-4%。\n"}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="波动区间-5%至-3%",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1164_negative_range_not_covered_by_opposite_sign(evaluator):
    """+4% 不在真实区间 (-5,-3) 内，不得被判覆盖（防假阳性放宽 verified）。"""
    seven_reports = {"news_report": "波动区间为+4%。\n"}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="波动区间-5%至-3%",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1164_cross_zero_range_signed_span(evaluator):
    """跨零区间「-3%~2%」range_span=(-3,2)，被 -1% 覆盖、+4% 不覆盖；
    端点 2% 的 bound 不得被 `~` 误标为 approx。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bns = extract_bound_numbers("变动-3%~2%")
    spans = {b.range_span for b in bns if b.range_span}
    assert spans == {(-3.0, 2.0)}
    tail = [b for b in bns if b.raw == "2%"][0]
    assert tail.bound is None
    seven_reports = {"news_report": "变动为-1%。\n"}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="变动-3%~2%",
        seven_reports=seven_reports,
    )
    assert res["status"] == STATUS_VERIFIED
    seven_reports = {"news_report": "变动为+4%。\n"}
    res = evaluator.evaluate_single_evidence(
        raw_evidence="变动-3%~2%",
        seven_reports=seven_reports,
    )
    assert res["status"] != STATUS_VERIFIED


def test_dav1164_clause_trace_fields_complete(evaluator):
    """溯源三件套齐备：拆分与未拆分原子项均带 parent_evidence/atomic_index/clause。"""
    seven_reports = {
        "fundamentals_report": "2026Q1归母净利润同比下滑 -46.58%。\n",
        "news_report": "公司公告拟回购超100亿元。\n",
    }
    items = evaluator._verify_evidence_or_decompose(
        "2026Q1归母净利润同比下滑46.58%，且公司拟回购超100亿元，同期营收999.99亿",
        seven_reports, None, None, "INV-1", None,
    )
    assert any(i["status"] == STATUS_VERIFIED for i in items)
    for i in items:
        assert "clause" in i
        assert "atomic_index" in i
    assert any("parent_evidence" in i for i in items)
# ── DAV-1158: provider/source 口径命名空间 ─────────────────────────────────


def test_dav1158_provider_extraction():
    """DAV-1158: 数据源/字段代码口径入 BoundNumber.provider；非作用域指标/无标注为 None。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers

    bn = extract_bound_numbers("东财 r0_net 主力净流出8.1亿")[0]
    assert bn.provider == ("fld:r0_net", "src:eastmoney")
    bn = extract_bound_numbers("同花顺 netamount 显示主力净流出5.2亿")[0]
    assert bn.provider == ("fld:netamount", "src:ths")
    bn = extract_bound_numbers("换手率3.2%（同花顺口径）")[0]
    assert bn.provider == ("src:ths",)
    # 财务科目不在 provider 作用域——「据东财财报」不给 provider
    bn = extract_bound_numbers("据东方财富财报净利润为45亿元")[0]
    assert bn.provider is None
    # 无标注 → None
    bn = extract_bound_numbers("主力净流出8.1亿")[0]
    assert bn.provider is None
    # 数据源名不得再被裸 token 立为 co: 主体（归 provider 层承载）
    bn = extract_bound_numbers("东财口径主力净流出5.2亿")[0]
    assert bn.entity is None
    bn = extract_bound_numbers("据东方财富财报净利润为45亿元")[0]
    assert bn.entity is None


def test_dav1158_cross_provider_same_metric_not_contradicted(evaluator):
    """东财口径 vs 同花顺口径同指标数值发散：不同数据源不得互作冲突真值。"""
    seven_reports = {
        "smart_money_report": "同花顺口径主力净流出8.1亿。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="东财口径主力净流出5.2亿",
        seven_reports=seven_reports,
        claim_id="PROV-1",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("provider_scope_gaps")


def test_dav1158_cross_field_code_not_contradicted(evaluator):
    """东财 r0_net vs 同花顺 netamount：同语义不同字段口径互判 → 跳过 + 记 gap。"""
    seven_reports = {
        "smart_money_report": "东财 r0_net 主力净流出8.1亿。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="同花顺 netamount 显示主力净流出5.2亿",
        seven_reports=seven_reports,
        claim_id="PROV-2",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("provider_scope_gaps")


def test_dav1158_single_side_provider_not_contradicted(evaluator):
    """单侧标注 provider、另一侧未标注 → 口径未知按最保守不判 + 记 gap。"""
    seven_reports = {
        "smart_money_report": "主力净流出8.1亿。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="东财口径主力净流出5.2亿",
        seven_reports=seven_reports,
        claim_id="PROV-3",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("provider_scope_gaps")


def test_dav1158_same_provider_same_field_true_conflict_still_contradicted(evaluator):
    """同 provider 同字段口径的真矛盾仍拦（防过宽）。"""
    seven_reports = {
        "smart_money_report": "东财 r0_net 主力净流出8.1亿。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="东财 r0_net 主力净流出5.2亿",
        seven_reports=seven_reports,
        claim_id="PROV-4",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1158_both_untagged_true_conflict_still_contradicted(evaluator):
    """双侧均无 provider 标注（默认同口径）→ 可比，真矛盾仍拦。"""
    seven_reports = {
        "smart_money_report": "主力净流出8.1亿。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="主力净流出5.2亿",
        seven_reports=seven_reports,
        claim_id="PROV-5",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1158_provider_tagged_quote_metric_not_contradicted(evaluator):
    """行情类（换手率）跨源不互判——东财口径换手7.84% vs 同花顺3.2% 跳过。"""
    seven_reports = {
        "market_report": "同花顺数据显示换手率为3.2%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="东财口径换手率为7.84%",
        seven_reports=seven_reports,
        claim_id="PROV-6",
    )
    assert res["status"] != STATUS_CONTRADICTED
    assert res.get("provider_scope_gaps")


def test_dav1158_financial_metric_provider_ignored(evaluator):
    """财务科目不在 provider 作用域——「据东财财报净利润45亿」与报告 60亿 仍判冲突。"""
    seven_reports = {
        "fundamentals_report": "净利润为60亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="据东方财富财报净利润为45亿元",
        seven_reports=seven_reports,
        claim_id="PROV-7",
    )
    assert res["status"] == STATUS_CONTRADICTED


# ── DAV-1168: Semantic Role 残余收敛——delta/aggregate/scenario 分桶后互判 ──


def test_dav1168_delta_vs_level_lpr_quarterly_drop(evaluator):
    """#1: LPR季降22%（变动率）vs 报告 LPR 3.00%（水平值）不互判冲突。"""
    seven_reports = {
        "macro_report": "央行引导LPR_1Y降至3.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="LPR季降22%使生息资产收益持续塌陷",
        seven_reports=seven_reports,
        claim_id="SR-1",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_delta_vs_level_lpr_mom_drop(evaluator):
    """#16: LPR月环比大降10.45%（变动率）vs 报告 3.00%（水平值）不互判冲突。"""
    seven_reports = {
        "macro_report": "LPR_1Y为3.00%。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="LPR_1Y降至3.00%月环比大降10.45%",
        seven_reports=seven_reports,
        claim_id="SR-16",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_delta_amount_vs_level_cashflow(evaluator):
    """#10: 同比骤降183亿（变动额）vs 现金流-25.35亿（水平值）不互判冲突。"""
    seven_reports = {
        "fundamentals_report": "2026Q1经营活动现金流量净额为-25.35亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1经营现金流-25.35亿同比骤降183亿",
        seven_reports=seven_reports,
        claim_id="SR-10",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_delta_amount_after_level_suffix(evaluator):
    """#28: 「环比激增9.83%达36.57亿」中 36.57亿 是变动额（量纲异于9.83%），
    不得与存货408.69亿水平值互判冲突。"""
    seven_reports = {
        "fundamentals_report": "2026Q1存货为408.69亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="基本面报告显示2026Q1存货达408.69亿元环比激增9.83%达36.57亿元挤压现金流",
        seven_reports=seven_reports,
        claim_id="SR-28",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_aggregate_vs_component_fund_flow(evaluator):
    """#2/#3: 大单与中单合计净流出2.91亿（合计值）vs 中单-1.7948亿（分项）不互判。"""
    seven_reports = {
        "smart_money_report": "| **大单与中单博弈** | 大单 -1.1134 亿 / 中单 -1.7948 亿 | 离场 |",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="08-21大单与中单合计净流出2.91亿元主力净流入仅380万",
        seven_reports=seven_reports,
        claim_id="SR-2",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_concatenated_aggregate_vs_component(evaluator):
    """#5: 「大单超大单流出15.06亿」（连写合计）vs 大单-12.96亿（分项）不互判。"""
    seven_reports = {
        "smart_money_report": "大单净额（LG）：-12.96 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="主力资金报告：2026-07-06主力净流出15.06亿元(大单超大单流出15.06亿)",
        seven_reports=seven_reports,
        claim_id="SR-5",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_zhongxiaodan_aggregate_vs_component(evaluator):
    """#11: 「中小单流出1.25亿」（中+小连写合计）vs 小单-0.7239亿（分项）不互判。"""
    seven_reports = {
        "smart_money_report": "小单净流出 -0.7239 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="中小单流出1.25亿元确认主力在71元关口吸纳散户割肉筹码",
        seven_reports=seven_reports,
        claim_id="SR-11",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_actual_vs_scenario_projection(evaluator):
    """#6: 每股净资产实际21元 vs 报告压力测算「跌向8.00元」情景值不互判。"""
    seven_reports = {
        "fundamentals_report": "- **压力测算底线**：每股净资产跌向 8.00元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="每股净资产已由21元降至9.26元",
        seven_reports=seven_reports,
        claim_id="SR-6",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_header_scoped_scenario(evaluator):
    """#23: 「汇率敏感性：…升值1%」小节标题式情景声明作用于行内数值，
    营收实际增速14.03%不得与其互判冲突。"""
    seven_reports = {
        "fundamentals_report": "- **汇率敏感性**：公司海外营收占比较高，人民币每单向升值 1%，毛利率受拖累。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="营收增14.03%",
        seven_reports=seven_reports,
        claim_id="SR-23",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_scenario_target_vs_actual(evaluator):
    """#27: 悲观推演「杀跌至23.50元」情景目标 vs 报告 VWMA 28.42 实际值不互判。"""
    seven_reports = {
        "news_report": "大盘VWMA加权均线位于28.42元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="大盘均线空头排列且板块加速补跌击穿25.40将诱发踩踏杀跌至23.50元(-12.1%)",
        seven_reports=seven_reports,
        claim_id="SR-27",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1168_true_conflict_still_contradicted(evaluator):
    """正例对照：同角色同指标真矛盾仍拦——actual vs actual 不放宽。"""
    seven_reports = {
        "smart_money_report": "超大单净流出 -1.6056 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="超大单净流出2.2亿元",
        seven_reports=seven_reports,
        claim_id="SR-POS",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1168_level_after_drop_still_actual(evaluator):
    """守卫回归：「大降10.45%至3.00%」中 3.00% 是变动后水平值（同量纲），仍归 actual。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers
    bns = extract_bound_numbers("LPR月环比大降10.45%至3.00%")
    by_raw = {b.raw: b for b in bns}
    assert by_raw["10.45%"].role == "delta"
    assert by_raw["3.00%"].role == "actual"


# ── DAV-1169: Period Identity 残余收敛——跨期间/跨时点/价格基准时点 ──


def test_dav1169_cross_period_q1_vs_fullyear(evaluator):
    """#24-26: 「2026Q1 经营现金流 -90.84亿」不得与「2025 全年 241.86亿」
    互判冲突（跨期间：Q1 单季 vs 全年累计，报告原文 -90.84 逐字在）。"""
    seven_reports = {
        "fundamentals_report": (
            "- 2026Q1 经营活动现金净额为 -90.84 亿元（2025Q1 为 -50.34 亿元至 -50.69 亿元）。"
            "经营现金流呈现典型的高度季节性：上半年通常为大额垫资备料与施工期，"
            "回款集中在四季度统一结算（如 2025 全年经营现金流净流入高达 241.86 亿元，"
            "2024 年达 271.28 亿元）。"
        ),
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1经营现金流大幅净流出90.84亿元",
        seven_reports=seven_reports,
        claim_id="PI-24",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1169_operating_vs_investing_cashflow(evaluator):
    """#24-26 二阶守卫：同期间（2026Q1）但不同现金流子科目——经营现金流
    不得与投资活动现金流净额互判冲突。"""
    seven_reports = {
        "fundamentals_report": "- 2026Q1 投资活动现金流净额为 +80.39 亿元，有效对冲了季节性经营垫付。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1经营现金流大幅净流出90.84亿元",
        seven_reports=seven_reports,
        claim_id="PI-24B",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1169_stock_balance_vs_daily_flow(evaluator):
    """#21-22: 「两融余额/杠杆出清至 24.88亿」（存量时点值）不得与
    「单日融资净偿还 1526.45万」（单日流量）互判冲突。"""
    seven_reports = {
        "macro_report": (
            "- **融资杠杆资金持续流出（已发生）**：7月30日融资净偿还 1526.45 万元，"
            "7月29日融资净偿还 1688.69 万元，近一周融资资金呈持续净偿还态势。"
        ),
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="AH精明指数为单一因子指数且两融杠杆已出清至24.88亿",
        seven_reports=seven_reports,
        claim_id="PI-21",
    )
    assert res["status"] != STATUS_CONTRADICTED
    res2 = evaluator.evaluate_single_evidence(
        raw_evidence="宏观与新闻报告证实调出仅为单一因子指数，两融余额降至24.88亿浮筹出清",
        seven_reports=seven_reports,
        claim_id="PI-22",
    )
    assert res2["status"] != STATUS_CONTRADICTED


def test_dav1169_hist_high_vs_current_price(evaluator):
    """#8: 「股价自 42.48 元累跌 21%」（历史高点时点值）不得与
    现价 33.42 元互判冲突。"""
    seven_reports = {
        "market_report": "截至 2026-08-25 收盘价为 33.42 元，中期下行趋势压制显著。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="中报利空公布已超3日股价自42.48元累跌21%充分定价",
        seven_reports=seven_reports,
        claim_id="PI-8",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1169_cost_basis_vs_current_price(evaluator):
    """#9: 「大股东增持均价 82.71 元」（历史成本均价）不得与
    现价 71.52 元互判冲突。"""
    seven_reports = {
        "market_report": "当前股价（71.52 元）处于中期下降通道的下轨支撑震荡区域。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="新闻报告:大股东增持均价82.71元现价跌破13.5%利好充分定价已失效",
        seven_reports=seven_reports,
        claim_id="PI-9",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1169_same_timepoint_true_conflict_still_contradicted(evaluator):
    """正例守卫 #12：超大单流出 2.2 亿原文无支撑 vs 报告 -1.6056 亿——
    资金流分单不在时点作用域，同时点同指标真矛盾仍判 contradicted。"""
    seven_reports = {
        "smart_money_report": "超大单净流出 -1.6056 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="超大单流出2.2亿与中小单净流入2.2亿形成被动接盘",
        seven_reports=seven_reports,
        claim_id="PI-POS12",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1169_same_balance_true_conflict_still_contradicted(evaluator):
    """正例守卫：同存量基准（两融余额 vs 两融余额）数值发散仍判冲突。"""
    seven_reports = {
        "macro_report": "截至 7 月末两融余额 18.20 亿元，杠杆水平平稳。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="两融余额已升至24.88亿",
        seven_reports=seven_reports,
        claim_id="PI-POS1",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1169_same_period_cashflow_true_conflict(evaluator):
    """正例守卫：同期间同子科目（2026Q1 经营现金流）数值发散仍判冲突。"""
    seven_reports = {
        "fundamentals_report": "- 2026Q1 经营现金流为 +45.20 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q1经营现金流大幅净流出90.84亿元",
        seven_reports=seven_reports,
        claim_id="PI-POS2",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1169_timepoint_binding():
    """机制单测：时点/价格基准绑定值正确。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers
    by_raw = {b.raw: b for b in extract_bound_numbers("两融余额降至24.88亿")}
    assert by_raw["24.88亿"].timepoint == "tp:stock"
    by_raw = {b.raw: b for b in extract_bound_numbers("7月30日融资净偿还 1526.45 万元")}
    assert by_raw["1526.45 万元"].timepoint == "tp:flow"
    by_raw = {b.raw: b for b in extract_bound_numbers("股价自42.48元累跌21%")}
    assert by_raw["42.48元"].timepoint == "tp:hist"
    by_raw = {b.raw: b for b in extract_bound_numbers("大股东增持均价82.71元")}
    assert by_raw["82.71元"].timepoint == "tp:cost"
    # 未标注现价不打标签（保持可与报告未标注记录判真冲突）
    by_raw = {b.raw: b for b in extract_bound_numbers("现价33.42元")}
    assert by_raw["33.42元"].timepoint is None


def test_dav1169_spaced_year_period():
    """机制单测：「2025 全年」「2024 年」空格年度形态归一为年度期间。"""
    from tradingagents.agents.utils.evidence_verifier import (
        extract_bound_numbers,
        normalize_period,
    )
    assert normalize_period("如 2025 全年经营现金流净流入高达 ") == "2025"
    assert normalize_period("2024 年达 ") == "2024"
    bns = extract_bound_numbers("如 2025 全年经营现金流净流入高达 241.86 亿元")
    by_raw = {b.raw: b for b in bns}
    assert by_raw["241.86 亿元"].period == "2025"


def test_dav1169_fcf_vs_operating_cashflow(evaluator):
    """DAV-1173 🔴-1 返修回归：「2026Q2 经营现金流 602.17亿」与「单季 FCF
    351.44亿」是同期间不同子科目，不得互判冲突（冻结语料 cda1abff 第 16 条）。"""
    seven_reports = {
        "fundamentals_report": "- 经营活动现金流单季度达 602.17 亿元，同比大幅改善。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q2经营现金流602.17亿且单季FCF达351.44亿",
        seven_reports=seven_reports,
        claim_id="PI-FCF",
    )
    assert res["status"] != STATUS_CONTRADICTED


def test_dav1169_same_fcf_true_conflict(evaluator):
    """FCF 拆分守卫：同子科目（自由现金流 vs 自由现金流）数值发散仍判冲突。"""
    seven_reports = {
        "fundamentals_report": "- 2026Q2 单季自由现金流为 345.44 亿元。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026Q2单季自由现金流高达119.04亿元抗风险",
        seven_reports=seven_reports,
        claim_id="PI-POS-FCF",
    )
    assert res["status"] == STATUS_CONTRADICTED


def test_dav1169_match_direction_unlabeled_timepoint(evaluator):
    """DAV-1173 🟡-2 返修：佐证方向单侧标注不阻塞——证据标 tp:cost 的
    「增持均价 82.71元」可被报告未标注同值记录佐证。"""
    seven_reports = {
        "news_report": "大股东增持均价 82.71 元已完成披露。",
    }
    res = evaluator.evaluate_single_evidence(
        raw_evidence="大股东增持均价82.71元",
        seven_reports=seven_reports,
        claim_id="PI-JOIN",
    )
    assert res["status"] == STATUS_VERIFIED


def test_dav1169_junxian_not_cost_basis():
    """均价负例：「N日均价线」是均线类表述而非成本基准，不打 tp:cost 标签。"""
    from tradingagents.agents.utils.evidence_verifier import extract_bound_numbers
    bns = extract_bound_numbers("10日均价线33.42元上穿")
    tagged = [b for b in bns if b.timepoint is not None]
    assert tagged == []
