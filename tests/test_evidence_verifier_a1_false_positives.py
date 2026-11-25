import pytest

from tradingagents.agents.utils.evidence_verifier import (
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    EvidenceFactualTruthEvaluator,
)


@pytest.fixture
def evaluator():
    return EvidenceFactualTruthEvaluator()


def test_unsupported_policy_clause_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="国家医保支持创新药实行DRG除外支付免计定额",
        seven_reports={"macro_report": "医保支持创新药发展，支付端政策保持稳定。"},
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_hypothetical_clause_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="假设生息资产收益率下调10bp，税后利润减少93.75亿元",
        seven_reports={
            "fundamentals_report": "生息资产综合收益率下调10bps，税后利润影响约-93.75亿元。"
        },
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_bare_numeric_token_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="90%",
        seven_reports={"macro_report": "评估基准日：2026-11-23，流动性覆盖率达到90%。"},
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_numeric_token_with_metric_is_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="流动性覆盖率达到90%",
        seven_reports={"macro_report": "评估基准日：2026-11-23，流动性覆盖率达到90%。"},
    )

    assert result["status"] == STATUS_VERIFIED


def test_judgment_word_is_not_verified(evaluator):
    result = evaluator.evaluate_single_evidence(
        raw_evidence="11-20与11-23天量对倒2.78亿股形成高位筹码套牢峰",
        seven_reports={
            "volume_price_report": "11-20与11-23成交量分别为1.52亿股与1.26亿股，高位换手剧烈放大。"
        },
    )

    assert result["status"] == STATUS_UNSUPPORTED


def test_bullish_stance_cannot_use_net_outflow_as_verified_evidence(evaluator):
    result = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-6",
                "stance": "bullish",
                "evidence": ["5日主力累计净流出4.67亿占流通市值比仅0.03%"],
            }
        ],
        seven_reports={"smart_money_report": "5日累计主力净额为-4.67亿元。"},
    )[0]

    assert result["status"] == STATUS_UNSUPPORTED
    assert result["details"] == "资金流方向与论点 stance 相反，不作为已核实事实"


def test_bullish_stance_cannot_use_matching_net_outflow_as_verified_evidence(evaluator):
    result = evaluator.evaluate_claims(
        claims=[
            {
                "claim_id": "INV-6",
                "stance": "bullish",
                "evidence": ["5日主力累计净流出4.67亿占流通市值比仅0.03%"],
            }
        ],
        seven_reports={
            "smart_money_report": (
                "5日累计主力净额为-4.67亿元。从5日累计净流出4.67亿元相对于公司数千亿市值的比例"
                "（单日流出仅占流通市值0.03%）来看，抛售偏向流动性再配置。"
            )
        },
    )[0]

    assert result["status"] == STATUS_UNSUPPORTED
    assert result["details"] == "资金流方向与论点 stance 相反，不作为已核实事实"
