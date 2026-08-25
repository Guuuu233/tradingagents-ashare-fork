"""Standardized Mock Debate & Evaluation Dataset Generator for P3-H2.

Provides realistic synthetic debate reports, evaluation matrices, and weekly datasets
covering:
1. Bull Win scenario (verified claims, challenge adoption, BUY verdict, T+5 hit).
2. Bear Win scenario (verified claims, challenge adoption, SELL verdict, T+5 hit).
3. Balanced Debate / Neutral Hold scenario (legitimate omission for HOLD target_price).
4. Structural data_gaps scenario (northbound stoppage, snapshot historical refusal).
5. Operational data_gaps scenario (news timeout, provider network failure).
6. Consistency Hard Gate / Degenerate scenario (consistency_check_passed=False).
7. Full 60-sample weekly benchmark dataset (20+ symbols, 5+ industries, balanced sides).

Zero external network or LLM dependencies.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Dict, List

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.evaluation_schemas import (
    EvaluationMetricMatrix,
    WeeklyMetricsJSON,
    build_evaluation_metric_matrix,
    build_weekly_metrics,
)

# 20 diverse stock tickers across 6 industries
SAMPLE_SECURITIES = [
    {"symbol": "600519.SH", "name": "贵州茅台", "industry": "白酒", "base_price": 1650.0},
    {"symbol": "000858.SZ", "name": "五粮液", "industry": "白酒", "base_price": 140.0},
    {"symbol": "000568.SZ", "name": "泸州老窖", "industry": "白酒", "base_price": 130.0},
    {"symbol": "000333.SZ", "name": "美的集团", "industry": "白色家电", "base_price": 84.5},
    {"symbol": "600690.SH", "name": "海尔智家", "industry": "白色家电", "base_price": 28.0},
    {"symbol": "000651.SZ", "name": "格力电器", "industry": "白色家电", "base_price": 42.0},
    {"symbol": "601318.SH", "name": "中国平安", "industry": "非银金融", "base_price": 45.0},
    {"symbol": "600030.SH", "name": "中信证券", "industry": "非银金融", "base_price": 22.0},
    {"symbol": "601166.SH", "name": "兴业银行", "industry": "银行", "base_price": 18.5},
    {"symbol": "600036.SH", "name": "招商银行", "industry": "银行", "base_price": 36.0},
    {"symbol": "600276.SH", "name": "恒瑞医药", "industry": "医药生物", "base_price": 46.0},
    {"symbol": "300760.SZ", "name": "迈瑞医疗", "industry": "医药生物", "base_price": 260.0},
    {"symbol": "300015.SZ", "name": "爱尔眼科", "industry": "医药生物", "base_price": 14.5},
    {"symbol": "300750.SZ", "name": "宁德时代", "industry": "电力设备", "base_price": 220.0},
    {"symbol": "002594.SZ", "name": "比亚迪", "industry": "汽车", "base_price": 250.0},
    {"symbol": "600900.SH", "name": "长江电力", "industry": "公用事业", "base_price": 29.0},
    {"symbol": "002415.SZ", "name": "海康威视", "industry": "计算机", "base_price": 31.0},
    {"symbol": "601899.SH", "name": "紫金矿业", "industry": "有色金属", "base_price": 16.0},
    {"symbol": "600028.SH", "name": "中国石化", "industry": "石油石化", "base_price": 6.2},
    {"symbol": "000002.SZ", "name": "万科A", "industry": "房地产", "base_price": 8.5},
]


def create_mock_bull_win_report() -> Dict[str, Any]:
    """Synthetic report: Bull Win with strong verified evidence & Buy verdict."""
    return {
        "id": "mock_rep_bull_001",
        "symbol": "600519.SH",
        "company_of_interest": "贵州茅台",
        "trade_date": "2026-08-20",
        "industry": "白酒",
        "market_regime": "bull_trend",
        "decision": "BUY",
        "direction": "BUY",
        "confidence": 85,
        "probability": 0.85,
        "entry_price": 1650.0,
        "target_price": 1820.0,
        "stop_loss_price": 1580.0,
        "latency_ms": 1250.0,
        "macro_report": "宏观流动性充裕，央行降准 0.25% ，释放长期资金 5000 亿 。消费行业景气度改善，核心CPI同比 +0.8% 。",
        "market_report": "上证指数站稳 3100 点 ，北向资金净流入 45.2 亿 ，大盘成交额达 9500 亿 。",
        "sentiment_report": "社交媒体正面情绪指数达 78 分 ，机构调研覆盖度环比提升 15% 。",
        "news_report": "批价企稳回升至 2450 元 ，中秋国庆旺季订货进度超预期达 80% 。",
        "fundamentals_report": "中报营收同比增长 15.8% 达 819.3 亿 ，净利润同比增长 16.1% 达 416.9 亿 ，毛利率 91.8% 。",
        "smart_money_report": "主力资金净流入 8.5 亿 ，超大单净买入 5.2 亿 ，机构持股占比稳定在 68.5% 。",
        "volume_price_report": "放量突破 20 日均线 1640 元 ，MACD出现金叉，日成交额 68 亿 。",
        "data_gaps": [],
        "source_provenance": {
            "stock_data": {"status": "ok", "gap_class": "operational"},
            "fundamentals": {"status": "ok", "gap_class": "operational"},
        },
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "manager",
            "tiebreak_skipped": False,
            "debate_degenerate": False,
            "claims": [
                {
                    "claim_id": "CLM-1",
                    "speaker_key": "bull",
                    "stance": "bullish",
                    "model_name": "deepseek-r1",
                    "claim": "中报营收增长 15.8% ，净利润达 416.9 亿 ，基本面极其稳健。",
                    "evidence": ["819.3亿", "416.9亿", "15.8%"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 1,
                },
                {
                    "claim_id": "CLM-2",
                    "speaker_key": "bull",
                    "stance": "bullish",
                    "model_name": "deepseek-r1",
                    "claim": "主力资金净流入 8.5 亿 ，超大单买入 5.2 亿 ，筹码集中。",
                    "evidence": ["8.5亿", "5.2亿"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 2,
                },
                {
                    "claim_id": "CLM-3",
                    "speaker_key": "bear",
                    "stance": "bearish",
                    "model_name": "qwen-max",
                    "claim": "批价短期波动或承压，库存周转天数上升至 45 天 。",
                    "evidence": ["45天"],
                    "status": "unsupported",
                    "is_verified": False,
                    "debate_round": 1,
                },
            ],
            "challenges": [
                {
                    "challenge_id": "CH-1",
                    "speaker_key": "bull",
                    "stance": "bullish",
                    "target_claim_id": "CLM-3",
                    "weakest_point": "批价最新已企稳回升至 2450 元 ，旺季订货进度达 80% ，库存压力已消化。",
                    "status": "verified",
                    "adopted": True,
                    "debate_round": 2,
                }
            ],
            "round_messages": [
                {"message_index": 1, "debate_round": 1, "speaker_key": "bull", "cleaned_prose": "中报营收 819.3 亿 ，净利润 416.9 亿 ，估值合理。", "model_name": "deepseek-r1"},
                {"message_index": 2, "debate_round": 1, "speaker_key": "bear", "cleaned_prose": "高端消费承压，批价波动，库存 45 天 需警惕。", "model_name": "qwen-max"},
                {"message_index": 3, "debate_round": 2, "speaker_key": "bull", "cleaned_prose": "批价已恢复至 2450 元 ，主力净买入 8.5 亿 突破 1640 元 ，重申净利润 416.9 亿 。", "model_name": "deepseek-r1"},
            ],
            "manager_verdict": {
                "winner": "bull",
                "direction": "BUY",
                "reason": "多方基本面数据（ 819.3 亿 、 416.9 亿 ）与主力资金流向（ 8.5 亿 ）均获确证，空方顾虑被有效击穿。",
                "entry": "1650.0元",
                "target": "1820.0元",
                "stop_loss": "1580.0元",
                "adopted_claim_ids": ["CLM-1", "CLM-2"],
                "adopted_challenge_ids": ["CH-1"],
                "consistency_check_passed": True,
                "failed_checks": [],
                "claim_evidence_summary": {
                    "CLM-1": {"speaker_key": "bull", "stance": "bullish", "decision": "adopt", "counts": {"verified": 3, "total": 3, "contradicted": 0, "source_unavailable": 0}},
                    "CLM-2": {"speaker_key": "bull", "stance": "bullish", "decision": "adopt", "counts": {"verified": 2, "total": 2, "contradicted": 0, "source_unavailable": 0}},
                    "CLM-3": {"speaker_key": "bear", "stance": "bearish", "decision": "reject", "counts": {"verified": 0, "total": 1, "contradicted": 1, "source_unavailable": 0}},
                },
            },
        },
    }


def create_mock_bear_win_report() -> Dict[str, Any]:
    """Synthetic report: Bear Win with verified downside risks & Sell verdict."""
    return {
        "id": "mock_rep_bear_002",
        "symbol": "000002.SZ",
        "company_of_interest": "万科A",
        "trade_date": "2026-08-20",
        "industry": "房地产",
        "market_regime": "bear_trend",
        "decision": "SELL",
        "direction": "SELL",
        "confidence": 80,
        "probability": 0.80,
        "entry_price": 8.50,
        "target_price": 7.20,
        "stop_loss_price": 9.10,
        "latency_ms": 1180.0,
        "macro_report": "房地产销售面积同比下降 18.5% ，新开工面积下降 23.0% ，行业处于深度出清期。",
        "market_report": "地产板块持续领跌，主力资金净流出 12.4 亿 ，估值中枢下移。",
        "sentiment_report": "舆情情绪指数录得 28 分 极度谨慎，债券价格波动加剧。",
        "news_report": "公司多笔公开债面临到期偿付，偿债现金流压力持续显现。",
        "fundamentals_report": "上半年计提资产减值准备 45.8 亿 ，归母净利润亏损 98.5 亿 ，净负债率升至 62.5% 。",
        "smart_money_report": "主力资金连续 5 日净流出，超大单减仓 3.8 亿 ，北向持股比例下降 1.2% 。",
        "volume_price_report": "跌破半年线 8.80 元 支撑，MACD死叉向下发散，均线空头排列。",
        "data_gaps": [],
        "source_provenance": {"realtime": {"status": "ok", "gap_class": "operational"}},
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "manager",
            "tiebreak_skipped": False,
            "debate_degenerate": False,
            "claims": [
                {
                    "claim_id": "CLM-B1",
                    "speaker_key": "bear",
                    "stance": "bearish",
                    "model_name": "qwen-max",
                    "claim": "资产减值 45.8 亿 ，净利润亏损 98.5 亿 ，负债率达 62.5% 。",
                    "evidence": ["45.8亿", "98.5亿", "62.5%"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 1,
                },
                {
                    "claim_id": "CLM-B2",
                    "speaker_key": "bear",
                    "stance": "bearish",
                    "model_name": "qwen-max",
                    "claim": "销售面积下滑 18.5% ，主力连续净流出 12.4 亿 。",
                    "evidence": ["18.5%", "12.4亿"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 2,
                },
                {
                    "claim_id": "CLM-L1",
                    "speaker_key": "bull",
                    "stance": "bullish",
                    "model_name": "deepseek-r1",
                    "claim": "核心城市二手房成交微幅回暖 3.2% ，存在估值修复空间。",
                    "evidence": ["3.2%"],
                    "status": "unsupported",
                    "is_verified": False,
                    "debate_round": 1,
                },
            ],
            "challenges": [
                {
                    "challenge_id": "CH-B1",
                    "speaker_key": "bear",
                    "stance": "bearish",
                    "target_claim_id": "CLM-L1",
                    "weakest_point": "3.2% 回暖仅限局部一二线核心区，整体新开工下降 23.0% ，无法对冲减值压力。",
                    "status": "verified",
                    "adopted": True,
                    "debate_round": 2,
                }
            ],
            "round_messages": [
                {"message_index": 1, "debate_round": 1, "speaker_key": "bull", "cleaned_prose": "PB处于历史低位，核心城市成交回暖 3.2% 。", "model_name": "deepseek-r1"},
                {"message_index": 2, "debate_round": 1, "speaker_key": "bear", "cleaned_prose": "亏损 98.5 亿 ，减值 45.8 亿 ，流出 12.4 亿 。", "model_name": "qwen-max"},
                {"message_index": 3, "debate_round": 2, "speaker_key": "bear", "cleaned_prose": "新开工仍下滑 23.0% ，均线空头破位 8.80 元 ，再次强调亏损 98.5 亿 。", "model_name": "qwen-max"},
            ],
            "manager_verdict": {
                "winner": "bear",
                "direction": "SELL",
                "reason": "空方提出的财报巨亏 98.5 亿 与减值 45.8 亿 事实确凿，多方弱复苏论据被彻底反驳。",
                "entry": "8.50元",
                "target": "7.20元",
                "stop_loss": "9.10元",
                "adopted_claim_ids": ["CLM-B1", "CLM-B2"],
                "adopted_challenge_ids": ["CH-B1"],
                "consistency_check_passed": True,
                "failed_checks": [],
                "claim_evidence_summary": {
                    "CLM-B1": {"speaker_key": "bear", "stance": "bearish", "decision": "adopt", "counts": {"verified": 3, "total": 3, "contradicted": 0, "source_unavailable": 0}},
                    "CLM-B2": {"speaker_key": "bear", "stance": "bearish", "decision": "adopt", "counts": {"verified": 2, "total": 2, "contradicted": 0, "source_unavailable": 0}},
                    "CLM-L1": {"speaker_key": "bull", "stance": "bullish", "decision": "reject", "counts": {"verified": 0, "total": 1, "contradicted": 1, "source_unavailable": 0}},
                },
            },
        },
    }


def create_mock_balanced_tie_report() -> Dict[str, Any]:
    """Synthetic report: Balanced debate resulting in Neutral HOLD verdict."""
    return {
        "id": "mock_rep_hold_003",
        "symbol": "600900.SH",
        "company_of_interest": "长江电力",
        "trade_date": "2026-08-20",
        "industry": "公用事业",
        "market_regime": "consolidation",
        "decision": "HOLD",
        "direction": "HOLD",
        "confidence": 60,
        "probability": 0.50,
        "entry_price": 29.0,
        "target_price": None,
        "stop_loss_price": 27.5,
        "note": "观望不设目标价 概率未提供/未提取",
        "extraction_note": "观望不设目标价",
        "latency_ms": 1050.0,
        "macro_report": "无风险利率处于 2.15% 低位，红利资产配置价值稳定，公用事业指数波动率收窄至 12% 。",
        "market_report": "股价在 28.5 元至 29.5 元区间窄幅震荡，全天成交额 15.2 亿 。",
        "sentiment_report": "防御性配置情绪稳定，机构持仓变动低于 2% 。",
        "news_report": "乌东德、白鹤滩水库来水正常，年发电量预测在 2900 亿千瓦时左右。",
        "fundamentals_report": "上半年实现净利润 113.6 亿 ，股息率约 3.8% ，现金流充沛达 240 亿 。",
        "smart_money_report": "主力资金净流入 0.35 亿 ，买卖双方力量处于高度均衡状态。",
        "volume_price_report": "均线黏合在 29.0 元附近，RSI指标报 51 处于中性区间。",
        "data_gaps": [],
        "source_provenance": {},
        "investment_debate_state": {
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "protocol_stage": "manager",
            "tiebreak_skipped": False,
            "debate_degenerate": False,
            "claims": [
                {
                    "claim_id": "CLM-H1",
                    "speaker_key": "bull",
                    "stance": "bullish",
                    "model_name": "deepseek-r1",
                    "claim": "股息率 3.8% ，现金流 240 亿 ，防御属性极强。",
                    "evidence": ["3.8%", "240亿"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 1,
                },
                {
                    "claim_id": "CLM-H2",
                    "speaker_key": "bear",
                    "stance": "bearish",
                    "model_name": "qwen-max",
                    "claim": "当前PE处历史中位数偏高位置，短期缺乏催化剂，震荡区间 28.5-29.5 元 。",
                    "evidence": ["28.5-29.5元"],
                    "status": "verified",
                    "is_verified": True,
                    "debate_round": 1,
                },
            ],
            "challenges": [],
            "round_messages": [
                {"message_index": 1, "debate_round": 1, "speaker_key": "bull", "cleaned_prose": "股息率 3.8% ，现金流 240 亿 防御价值突出。", "model_name": "deepseek-r1"},
                {"message_index": 2, "debate_round": 1, "speaker_key": "bear", "cleaned_prose": "估值合理充分， 28.5-29.5 元 窄幅震荡。", "model_name": "qwen-max"},
                {"message_index": 3, "debate_round": 2, "speaker_key": "bull", "cleaned_prose": "持续看好股息率 3.8% 和现金流 240 亿 。", "model_name": "deepseek-r1"},
            ],
            "manager_verdict": {
                "winner": "tie",
                "direction": "HOLD",
                "reason": "多空双方证据均属实（股息率 3.8% vs 估值合理缺乏催化），建议观望持有。",
                "entry": "29.0元",
                "target": None,
                "stop_loss": "27.5元",
                "adopted_claim_ids": ["CLM-H1", "CLM-H2"],
                "adopted_challenge_ids": [],
                "consistency_check_passed": True,
                "failed_checks": [],
                "claim_evidence_summary": {
                    "CLM-H1": {"speaker_key": "bull", "stance": "bullish", "decision": "adopt", "counts": {"verified": 2, "total": 2, "contradicted": 0, "source_unavailable": 0}},
                    "CLM-H2": {"speaker_key": "bear", "stance": "bearish", "decision": "adopt", "counts": {"verified": 1, "total": 1, "contradicted": 0, "source_unavailable": 0}},
                },
            },
        },
    }


def create_mock_structural_gaps_report() -> Dict[str, Any]:
    """Synthetic report: Contains institutional structural data_gaps (e.g. Northbound stoppage)."""
    base = create_mock_bull_win_report()
    base["id"] = "mock_rep_struct_gap_004"
    base["symbol"] = "000333.SZ"
    base["company_of_interest"] = "美的集团"
    base["industry"] = "白色家电"
    base["data_gaps"] = [
        {
            "source": "northbound_flow",
            "gap_class": "structural",
            "status": "unavailable",
            "reason": "沪深港通个股每日持股明细自2024年8月起停止披露，本项制度性停更不可用。",
            "gap": "northbound_flow 制度性停更 (停止披露)",
        },
        {
            "source": "share_pledge",
            "gap_class": "structural",
            "status": "refused",
            "reason": "股权质押仅提供当前快照，拒绝历史日期数据查询。",
            "gap": "share_pledge 历史快照拒绝",
        },
    ]
    base["source_provenance"] = {
        "northbound_flow": {"gap_class": "structural", "status": "unavailable", "gap": "停止披露"},
        "share_pledge": {"gap_class": "structural", "status": "refused", "gap": "快照拒绝"},
    }
    return base


def create_mock_operational_gaps_report() -> Dict[str, Any]:
    """Synthetic report: Contains operational data_gaps (e.g. Timeout, Network Error)."""
    base = create_mock_bear_win_report()
    base["id"] = "mock_rep_oper_gap_005"
    base["symbol"] = "600036.SH"
    base["company_of_interest"] = "招商银行"
    base["industry"] = "银行"
    base["data_gaps"] = [
        {
            "source": "news",
            "gap_class": "operational",
            "status": "timeout",
            "reason": "news 数据拉取超时（>300s），本次分析跳过该数据源",
            "gap": "news provider timeout",
        },
        {
            "source": "global_indices",
            "gap_class": "operational",
            "status": "failed",
            "reason": "Token 认证失败/网络连接异常 (来源: cn_akshare)",
            "gap": "global_indices network failure",
        },
    ]
    base["source_provenance"] = {
        "news": {"gap_class": "operational", "status": "timeout", "gap": "timeout"},
        "global_indices": {"gap_class": "operational", "status": "failed", "gap": "network failure"},
    }
    return base


def create_mock_degenerate_and_gate_report() -> Dict[str, Any]:
    """Synthetic report: Consistency hard gate triggered & degenerate belief trajectory."""
    base = create_mock_bull_win_report()
    base["id"] = "mock_rep_degen_006"
    base["symbol"] = "300750.SZ"
    base["company_of_interest"] = "宁德时代"
    base["industry"] = "电力设备"
    inv = base["investment_debate_state"]
    inv["debate_degenerate"] = True
    mv = inv["manager_verdict"]
    mv["consistency_check_passed"] = False
    mv["failed_checks"] = ["Direction BUY conflicts with high-confidence negative fundamental claims"]
    return base


def generate_60_sample_weekly_dataset(
    start_date: str = "2026-07-06",
    end_date: str = "2026-08-20",
    week_identifier: str = "week_202634",
) -> WeeklyMetricsJSON:
    """Generate a comprehensive 60-sample synthetic weekly dataset.

    Covers:
    - Exactly 60 debates
    - 20 unique securities across 6 industries
    - Balanced bull / bear / hold distribution (26 bull, 26 bear, 8 hold)
    - 46 calendar days and 34 trading days
    - T+5 outcomes for calibration
    - Both structural and operational data_gaps
    - 100% compliant with H1b 7-dimension activation gate criteria
    """
    matrix_list: List[EvaluationMetricMatrix] = []
    base_start = date.fromisoformat(start_date)

    # 60 sample plan: loop through 20 securities 3 times with varying trade dates
    for idx in range(60):
        sec = SAMPLE_SECURITIES[idx % len(SAMPLE_SECURITIES)]
        day_offset = int((idx / 59.0) * 45)  # 0 to 45 calendar days
        curr_date = base_start + timedelta(days=day_offset)
        # Skip weekends for trading days
        while curr_date.weekday() >= 5:
            curr_date += timedelta(days=1)
        t_date_str = curr_date.isoformat()
        t5_date_str = (curr_date + timedelta(days=7)).isoformat()

        # Decision balance: 26 Bull, 26 Bear, 8 Hold
        if idx < 26:
            # Bull win
            rep = create_mock_bull_win_report()
            winner = "bull"
            direction = "BUY"
            entry_p = sec["base_price"]
            target_p = round(entry_p * 1.10, 2)
            stop_p = round(entry_p * 0.95, 2)
            t5_p = round(entry_p * 1.035, 2)  # Hit +3.5%
        elif idx < 52:
            # Bear win
            rep = create_mock_bear_win_report()
            winner = "bear"
            direction = "SELL"
            entry_p = sec["base_price"]
            target_p = round(entry_p * 0.90, 2)
            stop_p = round(entry_p * 1.05, 2)
            t5_p = round(entry_p * 0.965, 2)  # Hit -3.5%
        else:
            # Balanced HOLD
            rep = create_mock_balanced_tie_report()
            winner = "tie"
            direction = "HOLD"
            entry_p = sec["base_price"]
            target_p = None
            stop_p = round(entry_p * 0.95, 2)
            t5_p = round(entry_p * 1.002, 2)  # Neutral +0.2%

        rep["id"] = f"mock_weekly_{idx+1:03d}_{sec['symbol']}"
        rep["symbol"] = sec["symbol"]
        rep["company_of_interest"] = sec["name"]
        rep["trade_date"] = t_date_str
        rep["industry"] = sec["industry"]
        rep["entry_price"] = entry_p
        rep["target_price"] = target_p
        rep["stop_loss_price"] = stop_p

        # Add data gaps to selected samples
        if idx % 10 == 0:
            rep["data_gaps"] = [
                {
                    "source": "northbound_flow",
                    "gap_class": "structural",
                    "status": "unavailable",
                    "reason": "沪深港通个股每日持股明细停止披露",
                }
            ]
        elif idx % 15 == 0:
            rep["data_gaps"] = [
                {
                    "source": "news",
                    "gap_class": "operational",
                    "status": "timeout",
                    "reason": "news provider timeout",
                }
            ]

        matrix = build_evaluation_metric_matrix(
            rep,
            report_id=rep["id"],
            symbol=sec["symbol"],
            security_name=sec["name"],
            trade_date=t_date_str,
            industry=sec["industry"],
            t_plus_5_price=t5_p,
            t_plus_5_date=t5_date_str,
        )
        matrix_list.append(matrix)

    return build_weekly_metrics(
        matrix_list,
        week_identifier=week_identifier,
        start_date=start_date,
        end_date=end_date,
    )


def save_mock_artifacts(base_dir: Path) -> Dict[str, Path]:
    """Save all mock scenarios and 60-sample dataset into JSON files."""
    base_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: Dict[str, Path] = {}

    scenarios = {
        "mock_bull_win_evaluation.json": build_evaluation_metric_matrix(
            create_mock_bull_win_report(), t_plus_5_price=1708.0
        ),
        "mock_bear_win_evaluation.json": build_evaluation_metric_matrix(
            create_mock_bear_win_report(), t_plus_5_price=8.20
        ),
        "mock_balanced_tie_evaluation.json": build_evaluation_metric_matrix(
            create_mock_balanced_tie_report(), t_plus_5_price=29.05
        ),
        "mock_structural_gaps_evaluation.json": build_evaluation_metric_matrix(
            create_mock_structural_gaps_report(), t_plus_5_price=86.5
        ),
        "mock_operational_gaps_evaluation.json": build_evaluation_metric_matrix(
            create_mock_operational_gaps_report(), t_plus_5_price=35.2
        ),
        "mock_degenerate_evaluation.json": build_evaluation_metric_matrix(
            create_mock_degenerate_and_gate_report(), t_plus_5_price=225.0
        ),
        "mock_weekly_dataset_60_samples.json": generate_60_sample_weekly_dataset(),
    }

    for fname, data_obj in scenarios.items():
        out_path = base_dir / fname
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data_obj, f, ensure_ascii=False, indent=2)
        saved_paths[fname] = out_path

    return saved_paths
