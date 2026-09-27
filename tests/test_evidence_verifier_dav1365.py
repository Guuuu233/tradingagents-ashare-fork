"""DAV-1365 旧口径证据核验双向修复单测（合成夹具，不含 vendor 原文）。

漏认向（真实同值被门拦）：指标绑定不对称字面桥接、期间局部错绑、
单位未归一（个百分点/个基点）、日期误提取、符号/区间约定、原子级补检。
误认向（不应放行）：bound=min/max 下界吞没、裸数字逐字子串、数字巧合。
每个根因独立 RED→GREEN。
"""
import pytest

from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator,
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
)


@pytest.fixture
def evaluator():
    return EvidenceFactualTruthEvaluator()


def _status(ev, raw, reports):
    res = ev.evaluate_single_evidence(
        raw_evidence=raw, seven_reports=reports, claim_id="T-1"
    )
    return res["status"]


def _decompose(ev, raw, reports):
    return ev._verify_evidence_or_decompose(
        raw, reports, {}, None, "T-1", None
    )


# ── 误认向：bound=min/max 下界吞没 ─────────────────────────────────────


def test_bound_min_swallow_rejected(evaluator):
    """报告「投资预计超5万亿元」是下界 5万，不得佐证更大的待证值 361万。"""
    assert _status(
        evaluator, "361万",
        {"macro_report": "发改委明确新型电网战略地位，“十五五”期间投资预计超5万亿元"},
    ) == STATUS_UNSUPPORTED


def test_bound_min_same_metric_still_matches(evaluator):
    """方向界不一概禁用：同指标同主体的「至少100亿净利润」仍可由 110亿佐证。"""
    assert _status(
        evaluator, "净利润至少100亿元",
        {"fundamentals_report": "2026年净利润录得110亿元，同比持平。"},
    ) == STATUS_VERIFIED


def test_bare_number_correct_source_still_matches(evaluator):
    """361万 的正确来源是同单位未绑定同值行（-0.0361亿），不受下界修复影响。"""
    assert _status(
        evaluator, "361万",
        {"smart_money_report": "- 总资金净流入（netamount）：-0.0361 亿元（小额净流出）"},
    ) == STATUS_VERIFIED


# ── 误认向：裸数字无语义绑定 ────────────────────────────────────────────


def test_bare_percent_not_verified_by_substring(evaluator):
    """裸『25%』不得因报告出现 25% 子串而放行。"""
    assert _status(
        evaluator, "25%",
        {"macro_report": "悲观情景概率约25%，乐观情景概率约35%。"},
    ) == STATUS_UNSUPPORTED


def test_bare_int_not_verified_by_substring(evaluator):
    """裸『15』不得因报告出现 15（如 15bp）而放行。"""
    assert _status(
        evaluator, "15",
        {"macro_report": "美国10Y单日回落超15bp。"},
    ) == STATUS_UNSUPPORTED


def test_number_with_metric_still_verified(evaluator):
    """带指标锚点的数字仍走正常核验（不得因裸数字门误伤）。"""
    assert _status(
        evaluator, "净利润同比下降1.03个百分点",
        {"fundamentals_report": "销售净利率同比下降约1.03个百分点。"},
    ) in (STATUS_VERIFIED, STATUS_UNSUPPORTED)  # 语义可比性由门决定，不断言放行


# ── 漏认向：指标绑定不对称（字面桥接） ──────────────────────────────────


def test_unbound_line_metric_bridged_by_synonym(evaluator):
    """行侧数字未绑指标，但行内含证据指标同义词（货币资金↔现金）→ 放行。"""
    assert _status(
        evaluator, "货币资金同比下降约41.66%",
        {"fundamentals_report":
         "- **货币资金：**2026年3月末940.921亿元，较2025年末852.472亿元增加约10.38%；"
         "较2025年3月末1,612.78亿元减少约41.66%。"},
    ) == STATUS_VERIFIED


def test_flow_direction_bridge(evaluator):
    """证据缺指标词但含资金流方向词，报告同值绑资金流族严格指标 → 放行。"""
    assert _status(
        evaluator, "净流出收窄约57.1%",
        {"fundamentals_report":
         "2026年一季度经营活动现金流净额为-37.71亿元，2025年同期为-87.93亿元，"
         "现金流净流出同比收窄约57.1%，但仍为负。"},
    ) == STATUS_VERIFIED


def test_strict_metric_single_side_bridged(evaluator):
    """行侧百分比未绑指标、证据侧绑严格指标，行内含指标同义词 → 放行。"""
    assert _status(
        evaluator, "基本面报告2026H1营业成本同比暴增37.55%达1221亿元",
        {"fundamentals_report":
         "2026H1营业成本为1221.53亿元（同比2025H1的888.06亿元增长37.55%）"},
    ) == STATUS_VERIFIED


def test_both_bound_diff_metric_not_bridged(evaluator):
    """双侧均绑定但不同名是真实指标分歧——不得桥接（防「营收增3.55%」串值
    被「毛利率」证据借同句词放行）；现金 vs 现金流的 canonical 分歧属残余漏认。"""
    assert _status(
        evaluator,
        "现金流量表期末现金余额字段90.81亿元与资产负债表货币资金218.56亿元存在口径差异",
        {"fundamentals_report":
         "期末现金余额字段在现金流表列示为90.81亿元，但资产负债表货币资金为218.56亿元。"},
    ) == STATUS_UNSUPPORTED


def test_serial_value_metric_not_stolen(evaluator):
    """串值负例：行内「营收增3.55%，毛利率25.57%」中 3.55% 已绑营收，
    证据「毛利率3.55%」不得借行内「毛利率」词放行。"""
    line = "- **财务表现**：2026年Q2营业收入2600.42亿元，同比增长3.55%；综合毛利率达25.57%，净利率为9.81%。"
    res = evaluator.evaluate_single_evidence(
        raw_evidence="2026年Q2综合毛利率为3.55%",
        seven_reports={"fundamentals_report": line},
        claim_id="T-1",
    )
    assert res["status"] != STATUS_VERIFIED


# ── 漏认向：期间局部错绑 ────────────────────────────────────────────────


def test_comparison_paren_period_not_rebound(evaluator):
    """「1221.53亿元（同比2025H1的888.06亿元）」括号基期不得回绑当期值。"""
    assert _status(
        evaluator, "2026H1营业成本为1221亿元",
        {"fundamentals_report":
         "2026H1营业成本为1221.53亿元（同比2025H1的888.06亿元增长37.55%）"},
    ) == STATUS_VERIFIED


def test_cn_half_period_and_compare_base(evaluator):
    """「2026年上半年」归一 2026H1；「同比2025H1的52.33%」基期只锚其后首个数字。"""
    assert _status(
        evaluator, "2026H1毛利率降至50.95%下滑1.38pct",
        {"fundamentals_report":
         "2026年上半年毛利率（根据摘要毛利率口径）为 50.95%，"
         "同比2025H1的 52.33% 下滑 1.38 个百分点"},
    ) == STATUS_VERIFIED


# ── 漏认向：单位未归一 ──────────────────────────────────────────────────


def test_ge_baidianfen_normalized(evaluator):
    """「上行10个基点」与证据「10BP」同值。"""
    items = _decompose(
        evaluator, "综合负债成本下行10BP增厚利润5.74%",
        {"fundamentals_report":
         "若全行综合计息负债成本率上行10个基点（BP），对应年化利息支出增加115亿元"},
    )
    by_raw = {i["raw"]: i["status"] for i in items}
    assert by_raw.get("10BP") == STATUS_VERIFIED


def test_ge_baidianfen_point(evaluator):
    """「下滑1.38个百分点」与证据「1.38pct」同值（随期间绑定一并覆盖）。"""
    assert _status(
        evaluator, "2026H1毛利率下滑1.38pct",
        {"fundamentals_report": "2026年上半年毛利率同比下滑 1.38 个百分点。"},
    ) == STATUS_VERIFIED


# ── 漏认向：日期片段误提取 ──────────────────────────────────────────────


def test_slash_date_not_extracted_as_number(evaluator):
    """「9/24」是日期，其片段不得成为待证数字。"""
    assert _status(
        evaluator, "9/24超大单-2.63亿加大单-2.64亿双源流出",
        {"smart_money_report":
         "| 超大单分组 | -2.63 亿（仅东财分量） |\n"
         "| 大单分组 | -2.64 亿（仅东财分量） | 双源流出 |"},
    ) == STATUS_VERIFIED


# ── 漏认向：符号/区间约定 ────────────────────────────────────────────────


def test_negative_range_vs_positive_magnitude(evaluator):
    """证据负值区间「净流出1.96亿至2.93亿」可由报告正幅度/负值行佐证。"""
    assert _status(
        evaluator, "主力资金报告显示大单持续净流出1.96亿至2.93亿元",
        {"smart_money_report":
         "* 主力净流入额（r0_net）：-1.9635 亿元（净流出约 1.96 亿元）。\n"
         "* 大单净流入额（lg_net）：-2.9256 亿元"},
    ) == STATUS_VERIFIED


# ── 漏认向：stype 推导（水平值非变动量） ────────────────────────────────


def test_level_suffix_not_growth(evaluator):
    """「收窄至19.16」是水平值声明，与报告「ATR为19.16」绝对额可比。"""
    assert _status(
        evaluator, "ATR收窄至19.16呈卖压衰竭",
        {"market_report": "- **波动通道**：ATR为19.16，波动适中"},
    ) == STATUS_VERIFIED


# ── 漏认向：原子级补检（上下文一致性） ──────────────────────────────────


def test_atomic_fact_recheck_restores_line_hit(evaluator):
    """子句级关键词门拦下的原子事实，按原子自身语境补检命中。"""
    items = _decompose(
        evaluator, "东财-1.82亿仅占流通市值0.022%为噪声级",
        {"smart_money_report":
         "- 东财 r0_net = -1.82 亿，方向 outflow\n- 该值占流通市值比例0.022%"},
    )
    by_raw = {i["raw"]: i["status"] for i in items}
    assert by_raw.get("-1.82亿") == STATUS_VERIFIED


# ── 对照：数字巧合/语义不同仍判 unsupported ─────────────────────────────


def test_coincidence_metric_mismatch_still_rejected(evaluator):
    """不同指标同值不得放行——桥接要求指标同义词字面出现。"""
    assert _status(
        evaluator, "净利润同比下滑25%",
        {"fundamentals_report": "现金流占比25%、综合毛利率30%。"},
    ) == STATUS_UNSUPPORTED


def test_coincidence_unit_mismatch_still_rejected(evaluator):
    """同值异纲（元 vs 股）不得放行。"""
    assert _status(
        evaluator, "成交量放大至5.28亿股",
        {"volume_price_report": "中单净买入5.28亿元。"},
    ) == STATUS_UNSUPPORTED
