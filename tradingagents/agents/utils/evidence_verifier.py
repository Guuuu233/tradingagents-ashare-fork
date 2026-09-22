"""Deterministic factual truth evaluator for debate claims and evidence.

Performs unit normalization, anti-lookahead date checks, deterministic factual
matching against the seven analyst reports and market_data_context, and flags
fatal hallucinations when citations reference failed or unavailable data sources.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from tradingagents.agents.utils.claim_specs import (
    ClaimApplicability,
    ClaimInvalidationCondition,
    ClaimReviewContract,
    ERR_SPEC_LOOKAHEAD_PIT,
    validate_applicability,
    validate_claim_review_contract,
    validate_invalidation_condition,
)

logger = logging.getLogger(__name__)

# Status constants
STATUS_VERIFIED = "verified"
STATUS_UNSUPPORTED = "unsupported"
STATUS_CONTRADICTED = "contradicted"
STATUS_SOURCE_UNAVAILABLE = "source_unavailable"

# Decision constants
DECISION_ADOPT = "adopt"
DECISION_PARTIAL = "partial"
DECISION_REJECT = "reject"

# Coverage thresholds
MIN_COVERAGE_THRESHOLD = 0.67

# Common failed status strings
UNAVAILABLE_STATUSES = frozenset(
    {
        "failed",
        "unavailable",
        "empty",
        "error",
        "missing",
        "partial_failure",
        "not_found",
        "rejected",
        "available_unverified_as_of",
        "unverified",
        "refused",
        "future",
    }
)

# Standard report keys
SEVEN_REPORT_KEYS = (
    "macro_report",
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "smart_money_report",
    "volume_price_report",
)

# Mapping from report key to underlying provenance sources
REPORT_TO_PROVENANCE_SOURCES: dict[str, tuple[str, ...]] = {
    "fundamentals_report": ("fundamentals", "balance_sheet", "income_statement", "cashflow"),
    "market_report": ("stock_data",),
    "volume_price_report": ("stock_data",),
    "news_report": ("news",),
    "sentiment_report": ("social_archive", "social_data", "social", "social.xhs", "social.dy"),
}

# Chinese and English quantity/unit patterns
_UNIT_STR = r"(?:万股|亿股|股|亿元|万元|万户|万人|万|亿|%|％|pct|bp|点|元|港元|美元|倍|次|手|户|人)"

_RANGE_BOTH_UNIT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(" + _UNIT_STR + r")\s*[-~至到]\s*(\d+(?:\.\d+)?)\s*(" + _UNIT_STR + r")(?![\d.])"
)
_RANGE_END_UNIT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*[-~至到]\s*(\d+(?:\.\d+)?)\s*(" + _UNIT_STR + r")(?![\d.])"
)
_RANGE_NO_UNIT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*[-~至到]\s*(\d+(?:\.\d+)?)(?![\d.])"
)

_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![\d.])([+-]?\d+(?:\.\d+)?)\s*(" + _UNIT_STR + r")?",
    re.IGNORECASE,
)

_DATE_MASK_PATTERN = re.compile(
    r"(?<![\d.])\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?![\d.])|"
    r"(?<![\d.])\d{4}年\d{1,2}月\d{1,2}日?(?![\d.])|"
    r"(?<![\d.])\d{1,2}月\d{1,2}日?(?![\d.])|"
    r"(?<![\d.])(0[1-9]|1[0-2])[-/.](0[1-9]|[12]\d|3[01])(?![\d.])|"
    r"(?<![\d.])\d{4}年?[hHqQ][1-4](?![\d.])|"
    r"(?<![\d.])[hHqQ][1-4](?![\d.])|"
    r"(?<![\d.])\d{4}年?[hH][1-2](?![\d.])|"
    r"(?<![\d.])\d{4}年度?(?![\d.])"
)

_PERIOD_QUARTER_RE = re.compile(r"(\d{4})年?[-_]?[qQ]([1-4])")
_PERIOD_SINGLE_QUARTER_RE = re.compile(r"(?<!\w)[qQ]([1-4])(?!\w)")
_PERIOD_HALF_RE = re.compile(r"(\d{4})年?[-_]?[hH]([1-2])")
_PERIOD_DATE_RE = re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?")
_PERIOD_MD_RE = re.compile(r"(\d{1,2})月(\d{1,2})日?")
# DAV-1169: 空格断开的年度/全年形态（「2024 年」「2025 全年」「2025全年」）
# 同样归一为年度期间——旧式 \d{4}年 要求紧邻「年」，漏绑后数字被整句
# 期间错绑（「2025 全年经营现金流241.86亿」被绑到句首 2026Q1 判跨期冲突）。
_PERIOD_YEAR_RE = re.compile(r"(\d{4})\s*(?:全\s*年|年(?:度)?)")


def normalize_period(text: str) -> str | None:
    """Extract and normalize period string (e.g. 2026Q2, 2026-08-24, 08-20, 2026)."""
    if not text:
        return None
    m = _PERIOD_QUARTER_RE.search(text)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = _PERIOD_HALF_RE.search(text)
    if m:
        return f"{m.group(1)}H{m.group(2)}"
    m = _PERIOD_DATE_RE.search(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _PERIOD_SINGLE_QUARTER_RE.search(text)
    if m:
        return f"Q{m.group(1)}"
    m = _PERIOD_MD_RE.search(text)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = _PERIOD_YEAR_RE.search(text)
    if m:
        return m.group(1)
    return None

_ISO_DATE_RE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_CN_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?")


def _parse_date(text: str) -> date | None:
    if not text:
        return None
    iso_m = _ISO_DATE_RE.search(text)
    if iso_m:
        try:
            return date(int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3)))
        except (ValueError, TypeError):
            pass
    cn_m = _CN_DATE_RE.search(text)
    if cn_m:
        try:
            return date(int(cn_m.group(1)), int(cn_m.group(2)), int(cn_m.group(3)))
        except (ValueError, TypeError):
            pass
    return None


def normalize_numeric_value(val_str: str, unit_str: str = "") -> tuple[float, str] | None:
    """Normalize a value and its unit to a canonical base (e.g. 元, 股, %, or raw number).

    Returns:
        (canonical_number, canonical_unit) or None
    """
    try:
        num = float(val_str)
    except (ValueError, TypeError):
        return None

    unit = (unit_str or "").strip().lower()
    if unit in {"万股", "亿股", "股"}:
        if unit == "亿股":
            return num * 100_000_000.0, "股"
        elif unit == "万股":
            return num * 10_000.0, "股"
        return num, "股"
    elif unit in {"亿元", "亿"}:
        return num * 100_000_000.0, "元"
    elif unit in {"万元", "万"}:
        return num * 10_000.0, "元"
    elif unit in {"元", "港元", "美元"}:
        return num, "元"
    elif unit in {"%", "％", "pct"}:
        return num, "%"
    elif unit == "bp":
        return num / 100.0, "%"
    elif unit == "万户":
        # 股东户数等计数单位：归一为「户」，不得折叠为元（DAV-1144）
        return num * 10_000.0, "户"
    elif unit == "户":
        return num, "户"
    elif unit == "万人":
        return num * 10_000.0, "人"
    elif unit == "人":
        return num, "人"
    else:
        return num, "raw"


def _extract_numbers_and_units(text: str) -> list[tuple[float, str, str]]:
    """Extract list of (normalized_val, canonical_unit, raw_substr) from text."""
    if not text:
        return []
    # 1. Mask dates to prevent temporal anchors from polluting financial metric matching
    cleaned = _DATE_MASK_PATTERN.sub(" ", text)
    # 2. Expand ranges so the first number inherits trailing unit (e.g. 450~470亿元 -> 450亿元 ~ 470亿元)
    cleaned = _RANGE_BOTH_UNIT_PATTERN.sub(r"\1\2 ~ \3\4", cleaned)
    cleaned = _RANGE_END_UNIT_PATTERN.sub(r"\1\3 ~ \2\3", cleaned)
    cleaned = _RANGE_NO_UNIT_PATTERN.sub(r"\1 ~ \2", cleaned)

    results = []
    for match in _NUMBER_WITH_UNIT_RE.finditer(cleaned):
        val_str = match.group(1)
        unit_str = match.group(2) or ""
        norm = normalize_numeric_value(val_str, unit_str)
        if norm is not None:
            results.append((norm[0], norm[1], match.group(0).strip()))
    return results


def _is_num_match(
    ev_num: float,
    ev_unit: str,
    l_num: float,
    l_unit: str,
    rel_tol: float = 0.02,
    abs_tol: float = 0.05,
) -> bool:
    """Check if evidence numeric value matches report numeric value within tolerance."""
    unit_compatible = (
        (ev_unit == l_unit)
        or (ev_unit == "raw" and l_unit == "%")
        or (ev_unit == "%" and l_unit == "raw")
    )
    if not unit_compatible:
        return False
    return (
        math.isclose(ev_num, l_num, rel_tol=rel_tol, abs_tol=abs_tol)
        or math.isclose(abs(ev_num), abs(l_num), rel_tol=rel_tol, abs_tol=abs_tol)
    )


# Expanded domain metric and context keywords
_METRIC_KEYWORDS = [
    # Valuation & Financial metrics
    "pe", "pb", "ps", "roe", "roa", "eps", "m2", "cpi", "ppi", "gdp", "lpr", "shibor",
    "营收", "收入", "利润", "净利润", "净利", "毛利", "毛利率", "净利率", "扣非", "负债率", "资产负债率",
    "现金流", "自由现金流", "fcf", "资本开支", "capex", "研发", "费用", "费用率", "应收账款", "存货",
    "周转率", "商誉", "减值", "利用率", "产能", "cr3", "价格战", "库存", "去库", "补库", "订单",
    "估值", "分红", "股息", "股息率", "回购", "增持", "减持", "重组", "定增", "质押", "现金", "货币资金", "安全垫", "安全边际",
    # DAV-1163: canonical 词表补全对应的关键词——关键词门（canonical 交集）
    # 只看 _METRIC_KEYWORDS，新 canonical 无关键词落点时命中行会被门拦下
    "流通市值", "市值", "离散度", "利息收入", "利息支出", "财务费用", "融资成本", "持仓占比", "均价",
    "贴息", "裂口", "缺口", "拨备", "投资现金流", "筹资现金流", "总负债",
    "股价", "收盘", "现价", "价格", "日均线", "日线", "铜价", "美债", "shibor",
    "流动比率", "速动比率",
    "底线", "压力测试", "敏感性", "弹性",
    # Capital & Flow metrics
    "主力", "净流入", "净流出", "流出", "流入", "超大单", "大单", "中单", "小单", "全单", "龙虎榜",
    "北向", "北向资金", "机构", "外资", "游资", "散户", "两融", "融资", "融券", "大宗交易", "筹码",
    "吸筹", "出货", "洗盘", "托底", "增仓", "减仓", "持股", "席位",
    # Technical & Volume/Price metrics
    "成交量", "成交额", "换手率", "换手", "量比", "地量", "天量", "放量", "缩量", "量价", "均线",
    "ema", "sma", "vwma", "macd", "rsi", "boll", "布林", "kdj", "atr", "dif", "dea",
    "支撑", "阻力", "突破", "破位", "双底", "筑底", "死叉", "金叉", "超买", "超卖", "多头", "空头",
    "冲高", "回落", "震荡", "趋势", "动量", "k线", "收盘", "开盘", "最高", "最低", "日内", "位置",
    "高点", "低点", "低位", "高位", "实体", "上影", "下影", "涨停", "跌停", "连板", "炸板",
    # Macro & Industry metrics
    "降息", "降准", "加息", "利率", "美债", "汇率", "油价", "原油", "黄金", "铜价", "lme", "大宗商品",
    "关税", "补贴", "以旧换新", "外需", "内需", "财政", "赤字", "信贷", "流动性", "长协", "转嫁",
    "水库", "来水", "发电量", "偏枯", "偏丰", "蓄能", "电量", "纳斯达克", "生物科技", "指数",
    # Pharma & Sector specific
    "管线", "临床", "获批", "授权", "license-out", "医保", "集采", "原料药", "仿制药", "创新药", "adc", "fda",
    # Sentiment & General
    "风险偏好", "进攻", "防守", "避险", "虹吸", "抽水", "情绪", "舆情", "预期差", "公告", "中报", "年报", "季报",
    # Entities frequently referenced
    "大金", "惠而浦", "乌东德", "三峡", "美的", "恒瑞", "礼来",
    # DAV-1088: per-share fundamentals needed for metric binding (缺陷 B' 词表缺项)
    "每股净资产", "bps"
]


def _extract_metric_keywords(text: str) -> list[str]:
    """Extract financial and market metric keywords from a string."""
    found = []
    text_lower = text.lower()
    for kw in _METRIC_KEYWORDS:
        if kw in text_lower:
            found.append(kw)
    return found


# DAV-1088 准入标准（本卡审定）：进入 _STRICT_METRICS 的规范化指标名必须同时满足
#   a) 名称唯一、量纲稳定 —— 同名 + 同单位 + 同语义类型 + 同期间下的数值发散即构成真实冲突；
#   b) 该指标在证据核验中需要承担「绑定即负责」语义 —— 绑定到严格指标的数字只允许与
#      同名严格指标的数字匹配/判冲突，不得作为通配符与未绑定或其他指标的数字互相放行。
# 该集合同时决定缺陷 A 的触发面（谁能被判冲突）与缺陷 B' 的触发面（谁不得当通配符）。
_STRICT_METRICS = {
    "营收", "毛利率", "毛利", "净利率", "净利润", "成本", "应收账款", "存货", "现金流",
    "资产负债率", "roe", "roa", "eps", "pe", "pb", "ps", "股息率", "换手率", "量比",
    "主力", "超大单", "大单", "两融", "概率", "预期收益", "降息", "降准", "关税",
    # DAV-1169: 投资/筹资活动现金流子科目入严格集（绑定即负责，与经营现金流不互判）
    "投资现金流", "筹资现金流",
    # DAV-1169 返修：自由现金流/FCF 独立子科目入严格集（与经营现金流不互判）
    "自由现金流",
    # DAV-1163: 小单/中单/均线族/RSI/ATR 入严格集——绑定即负责，不得当通配符
    "小单", "中单", "均线", "rsi", "atr",
    "lpr", "cpi", "ppi", "m2", "gdp",
    "每股净资产",
    # DAV-1144: 股价与股东户数为独立严格指标（同名+同单位+同语义才可比较）
    "股价", "最高价", "最低价", "股东户数",
}

_METRIC_CANONICAL_MAP: dict[str, str] = {
    # 营收 / 收入
    "营业收入": "营收", "主营业务收入": "营收", "单季营收": "营收", "海外营收": "营收", "营收": "营收", "收入": "营收", "revenue": "营收",
    # 利息收入为独立科目（非营收总量），不进入 _STRICT_METRICS：
    # 「利息收入折损 15.3亿」与「营收 1781.81亿」不可比，防止误绑营收后判伪冲突
    "利息收入": "利息收入",
    # 毛利率 / 毛利
    "综合毛利率": "毛利率", "销售毛利率": "毛利率", "毛利率": "毛利率", "毛利": "毛利",
    # 净利率
    "归母净利率": "净利率", "扣非净利率": "净利率", "净利率": "净利率", "净利润率": "净利率",
    # 净利润
    "归母净利润": "净利润", "归母净利": "净利润", "扣非净利润": "净利润", "扣非净利": "净利润", "净利润": "净利润", "净利": "净利润",
    # 每股净资产
    "每股净资产": "每股净资产", "每股净资产（bps）": "每股净资产", "bps": "每股净资产",
    # 成本
    "营业成本": "成本", "生产成本": "成本", "成本": "成本",
    # 应收账款
    "应收账款": "应收账款", "应收款项": "应收账款", "应收账期": "应收账款",
    # 存货
    "存货": "存货", "库存": "存货",
    # 现金流
    "经营活动产生的现金流量净额": "现金流", "经营性现金流": "现金流", "经营现金流": "现金流", "现金流": "现金流",
    "经营活动现金流": "现金流", "经营活动现金流量净额": "现金流",
    # DAV-1169 返修（DAV-1173 🔴-1）：自由现金流/FCF 为独立子科目——「单季
    # FCF 351.44亿」不得与同期间「经营活动现金流 602.17亿」互判冲突（同属
    # canonical 现金流造成的跨子科目假冲突，与投资/筹资漏拆同类）。
    "自由现金流": "自由现金流", "派生自由现金流": "自由现金流", "fcf": "自由现金流",
    # DAV-1169: 投资/筹资活动现金流为独立子科目——「2026Q1 经营现金流 -90.84亿」
    # 不得与同期间「投资活动现金流 +80.39亿」互判冲突（同名「现金流」跨科目误绑）。
    "投资活动产生的现金流量净额": "投资现金流", "投资活动现金流净额": "投资现金流",
    "投资活动现金流": "投资现金流", "投资现金流": "投资现金流",
    "筹资活动产生的现金流量净额": "筹资现金流", "筹资活动现金流净额": "筹资现金流",
    "筹资活动现金流": "筹资现金流", "筹资现金流": "筹资现金流",
    # 资产负债率
    "资产负债率": "资产负债率", "负债率": "资产负债率",
    # 估值 / 收益率
    "加权净资产收益率": "roe", "净资产收益率": "roe", "roe": "roe",
    "总资产收益率": "roa", "roa": "roa",
    "每股收益": "eps", "eps": "eps",
    "市盈率": "pe", "pe": "pe",
    "市净率": "pb", "pb": "pb",
    "市销率": "ps", "ps": "ps",
    "分红率": "股息率", "股息率": "股息率", "分红": "股息率", "股息": "股息率",
    # 交易 / 资金
    # DAV-1163: 换手率细类归一（实际换手/单日换手率/自由流通换手率 等同指标）
    "换手率": "换手率", "换手": "换手率", "实际换手": "换手率", "单日换手率": "换手率",
    "自由流通换手率": "换手率", "流通换手率": "换手率",
    # DAV-1163: 均线族（EMA/SMA/VWMA/日均线）归一——「10EMA(91.14)」与
    # 「10日均线91.14」是同族均线的不同记法；RSI/ATR 为独立规范化指标。
    "ema": "均线", "sma": "均线", "vwma": "均线", "日均线": "均线", "均线": "均线",
    "日线": "均线", "ma": "均线",
    "rsi": "rsi", "atr": "atr",
    "量比": "量比",
    "成交量": "成交量", "成交额": "成交额", "成交": "成交量",
    "主力净流入": "主力", "主力净流出": "主力", "主力": "主力",
    "超大单净流入": "超大单", "超大单净流出": "超大单", "超大单": "超大单",
    "大单净流入": "大单", "大单净流出": "大单", "大单": "大单",
    # DAV-1163: 小单/中单补全——词表此前仅有「散户小单」，「小单净流入3.70亿」
    # 无 canonical 落点，数字错绑回退到「主力」后被严格指标门误判失配。
    # 散户小单与小单同义（小单即散户分组），归一到同一 canonical。
    "散户小单净买入": "小单", "散户小单": "小单",
    "小单净流入": "小单", "小单净流出": "小单", "小单净买入": "小单", "小单": "小单",
    "中单净流入": "中单", "中单净流出": "中单", "中单净买入": "中单", "中单": "中单",
    "融资净偿还": "两融", "融资净买入": "两融", "融券净卖出": "两融", "两融": "两融", "融资": "两融", "融券": "两融",
    # DAV-1147: 现金口径归一——证据侧「现金337.51亿」与报告侧「货币资金337.51亿」
    # 必须折叠到同一规范化名，否则跨报告聚合的关键词门（canonical 交集）与
    # 指标绑定两侧均对不上，真实存在的事实被误判 unsupported。
    "现金": "现金", "货币资金": "现金", "现金及等价物": "现金", "现金储备": "现金",
    # DAV-1147: 流动性比率独立指标（流动比率 1.76 此前无词表项，跨报告拼合失败）
    "流动比率": "流动比率", "速动比率": "速动比率",
    # DAV-1144: 股价/股东户数 独立指标（此前词表缺失，错绑回退到最近指标）
    # DAV-1163: 「收盘/现价/报收/价格」同义记法归一到股价——报告侧
    # 「收盘 92.32」此前无 canonical 落点，与证据侧「收盘价92.32」互判失配。
    "股价": "股价", "收盘价": "股价", "收盘": "股价", "现价": "股价",
    "报收": "股价", "价格": "股价", "开盘": "开盘价", "开盘价": "开盘价",
    "最高价": "最高价", "最低价": "最低价",
    # DAV-1163: canonical 指标补全（复合句家族失配点）
    "市值": "市值",
    "在手现金": "现金",
    "lme铜价": "铜价", "lme铜": "铜价", "伦铜": "铜价", "铜价": "铜价", "lme": "铜价",
    "10年期美债": "美债", "美债利率": "美债", "美债收益率": "美债",
    # 「美债10年期收益率报4.740%」中期限数字 10 隔断了「美债」与数值的前向
    # 绑定——「年期收益率/国债收益率」词组落在数字之后，使收益率数值可前向
    # 绑到美债。
    "年期收益率": "美债", "国债收益率": "美债", "美债": "美债",
    "shibor": "shibor",
    "综合融资成本": "融资成本", "融资成本": "融资成本",
    # DAV-1159: 财务费用/贴息类、裂口/拨备类 canonical 落点——此前无词表项，
    # 「压降负债率69.4%的利息支出」中 69.4% 错绑资产负债率、「裂口68.5亿」
    # 回退错绑净利润、「拨备30-40亿」回退错绑每股净资产。
    "利息支出": "利息支出", "利息费用": "利息支出", "财务费用": "财务费用",
    "贴息": "贴息",
    "资金裂口": "裂口", "裂口": "裂口", "资金缺口": "裂口",
    "计提拨备": "拨备", "拨备": "拨备",
    # DAV-1169: 总负债独立科目落点——「Q1投资现金流净流出1023亿致总负债
    # 2273亿」中 2273亿 是负债总量而非投资现金流，缺词会前向继承投资现金流
    # 造成同指标伪冲突；非严格指标，不作冲突判定基准。
    "总负债": "总负债", "负债总额": "总负债", "负债合计": "总负债",
    "价格区间": "股价",
    "均价": "均价",
    "资本开支": "capex", "capex": "capex",
    "流通市值": "流通市值",
    "离散度": "离散度",
    "回购": "回购",
    "持仓占比": "持仓占比",
    "股东户数": "股东户数", "股东人数": "股东户数",
    # 布林轨位类：「股价跌破布林下轨49.59」中 49.59 归属下轨而非股价；
    # 报告侧「BOLL 下轨为 49.59」须绑同一规范化名才可匹配
    "布林下轨": "布林", "布林上轨": "布林", "布林中轨": "布林",
    "布林带": "布林", "布林": "布林", "boll": "布林",
    # 宏观 / 情景
    "情景概率": "概率", "概率": "概率", "情景": "概率",
    "预期收益": "预期收益",
    "降息": "降息", "降准": "降准", "关税": "关税",
    "lpr": "lpr", "cpi": "cpi", "ppi": "ppi", "m2": "m2", "gdp": "gdp",
}


def _canonicalize_metric(raw_metric: str | None, unit: str) -> str | None:
    """Map a raw metric name to its canonical form.

    DAV-1088: 规范化不再只依据「词 + 单位」做有损折叠。
    「净利 + %」不得归为净利率（它可能是占比或同比增速），「净利率 + 元」也不得
    反向归为净利润 —— 量的语义由 _classify_semantic_type 独立判定。
    """
    if not raw_metric:
        return None
    return _METRIC_CANONICAL_MAP.get(raw_metric, raw_metric)


# ── DAV-1088: 语义类型（封闭枚举）──
# 「未知」为显式枚举值，表示“已抽取数字但语义类型无法归类”；它与 metric=None
# （未绑定）含义不同，不得用 None 兼作两种含义。语义类型无法确定时一律归「未知」，
# 走不可比路径，不得猜测归属。
STYPE_ABS_AMOUNT = "绝对额"          # 绑定到非总量科目的金额/每股量（如每股净资产、eps）
STYPE_PARTIAL_IMPACT = "分项影响额"  # 对总量的分项冲击金额（折损/影响/贡献等修饰词判定）
STYPE_TOTAL = "总量"                # 营收/净利润/现金流等总量科目金额
STYPE_PROPORTION = "占比"           # 「占 X 的 Y%」
STYPE_GROWTH = "同比增速"           # 同比/环比/涨跌/增减类速率
STYPE_RATIO = "比率"                # 率、倍数、概率类无方向比值
STYPE_UNKNOWN = "未知"

# 「分项影响额 vs 总量」不可由指标名与单位推断（15.3亿元 与 1781.81亿元 单位相同、
# 指标名同为「营收」仍不可比），必须靠句法角色/修饰词另行判定。
_PARTIAL_IMPACT_RE = re.compile(
    # DAV-1163: 「压减/压降」同为分项冲击修饰（「压减利息收入约20-30亿」），
    # 缺词会把分项影响额压成绝对额/未知，与同事实记录互判失配
    r"折损|影响|贡献|拖累|侵蚀|损失|承压|减少|压缩|计提|减值|折让|冲击|侵蚀|压减|压降"
)
_GROWTH_CONTEXT_RE = re.compile(
    r"同比|环比|增长|增速|增幅|下降|下滑|降低|回落|回升|提升|提高|暴增|大增|"
    r"微增|微降|下跌|上涨|涨跌|收窄|走阔|扩张|收缩|跌(?!破)|涨(?!停)|"
    # DAV-1163: 骤降/骤升/跳水/飙升/腰斩/翻倍 等同为变动速率语境——
    # 「经营现金流骤降67.48%」语义即同比增速，缺词会被压成「未知」互判失配
    r"骤降|骤升|跳水|飙升|飙涨|腰斩|翻倍|大跌|大涨|暴跌|暴涨"
)
# DAV-1163: 「占」字比例语境放宽到同子句 16 字窗口——「主力净额占流通
# 市值比（net_to_circ_mv）约为 -0.0069%」中「占」与数字之间隔着括号注
# 释，8 字窗口会把占比语义压成「未知」互判失配。
_PROPORTION_CONTEXT_RE = re.compile(r"占[^，。；、]{0,16}$")

# % 单位下语义为「比率」的规范化指标
_RATE_CANON_METRICS = {
    "毛利率", "净利率", "roe", "roa", "股息率", "资产负债率", "换手率", "概率",
}
# raw/倍/点 单位下语义为「比率」的规范化指标
_RATIO_CANON_METRICS = _RATE_CANON_METRICS | {"pe", "pb", "ps", "eps", "量比"}
# 元/股 单位下语义为「总量」的规范化指标
_TOTAL_CANON_METRICS = {
    "营收", "净利润", "成本", "毛利", "现金流", "应收账款", "存货",
    "成交量", "成交额", "主力", "超大单", "大单", "散户小单", "小单", "中单", "两融",
}


def _classify_semantic_type(
    text: str,
    n_start: int,
    n_end: int,
    unit: str,
    canon_metric: str | None,
) -> str:
    """Classify a bound number's semantic type (closed enum; falls back to 未知).

    依据单位 + 数字紧邻上下文修饰词 + 规范化指标名三维判定；依据不足时返回 STYPE_UNKNOWN。
    """
    # 仅取数字前最近一个子句作语境，防止跨子句/跨括号的「同比增长」污染后续指标判定
    window = re.split(r"[，。；、,;（）()【】]", text[max(0, n_start - 20):n_start])[-1]
    if unit == "%":
        if _PROPORTION_CONTEXT_RE.search(window):
            return STYPE_PROPORTION
        # 「暴跌至10%」「达到28.5%」等至/到/为/达/录得结尾的窗口是**水平值**声明，
        # 语义为比率而非变动速率；但「同比增至」「同比增长」仍是增速，优先判增长语境。
        level_suffix = re.search(r"[至到为达得]\s*$", window)
        has_growth_ctx = _GROWTH_CONTEXT_RE.search(window)
        if has_growth_ctx and not (level_suffix and not re.search(r"同比|环比|增", window)):
            return STYPE_GROWTH
        # 后缀语境：「3.55%的微弱增速」「17.10%的增速」
        if re.search(r"增速|增长|增幅|跌幅|涨幅", text[n_end:n_end + 8]):
            return STYPE_GROWTH
        if canon_metric in _RATE_CANON_METRICS or (
            canon_metric is None and window and window.endswith("率")
        ) or (canon_metric and "率" in canon_metric) or canon_metric == "毛利":
            # % 绑定到「毛利」只能是毛利率语义；绑定到率类指标同理
            return STYPE_RATIO
        return STYPE_UNKNOWN
    if unit in ("元", "股"):
        if _PARTIAL_IMPACT_RE.search(window):
            return STYPE_PARTIAL_IMPACT
        if canon_metric in _TOTAL_CANON_METRICS:
            return STYPE_TOTAL
        if canon_metric is not None:
            return STYPE_ABS_AMOUNT
        return STYPE_UNKNOWN
    # raw / 倍 / 点 / 次 / 手 等无标准量纲单位
    if _GROWTH_CONTEXT_RE.search(window) or re.search(
        r"增速|增长|增幅|跌幅|涨幅", text[n_end:n_end + 8]
    ):
        # 「下降 1.38 个百分点」等变动量，语义同同比增速
        return STYPE_GROWTH
    if canon_metric in _RATIO_CANON_METRICS:
        return STYPE_RATIO
    if canon_metric is not None:
        return STYPE_ABS_AMOUNT
    return STYPE_UNKNOWN


_SORTED_METRIC_MAP_KEYS = sorted(_METRIC_CANONICAL_MAP.keys(), key=len, reverse=True)


# ── DAV-1145: 实体 / 比较范围（同指标跨主体不得互判冲突）──
# BoundNumber.entity 取值：
#   None        未指明主体（默认当前报告目标股，双侧均 None 视为同主体）
#   'co:<名>'   公司/主体名（「美的」「格力电器」「奥克斯」等）
#   'sym:<6位>' 证券代码
#   'bench:<名>' 指数/行业均值/同业等基准主体（与个股主体不可比）
#   ENTITY_AMBIGUOUS  同子句出现多个不同主体，归属歧义 → 最保守不判冲突
ENTITY_AMBIGUOUS = "ambig"

# 6 位证券代码（A 股 0/3/6/9 开头）：后跟量纲单位时排除（300000元 是金额非代码）
_ENTITY_TICKER_RE = re.compile(
    r"(?<![\d.])([0369]\d{5})(?:\.(?:sh|sz|bj))?(?![\d.])"
    r"(?!\s*(?:万股|亿股|股|亿元|万元|万户|万人|万|亿|%|％|元|港元|美元|倍|点|次|手|户|人))",
    re.IGNORECASE,
)
# 指数 / 行业均值 / 同业基准类主体
_ENTITY_BENCH_RE = re.compile(
    r"沪深\s*300|中证\s*\d{2,4}|国证\s*\d{2,4}|上证\s*50|上证指数|深证成指|"
    r"创业板指|科创\s*50|北证\s*50|恒生指数|恒生科技|纳斯达克|标普\s*500|道琼斯|"
    r"行业均值|行业平均|同业均值|同业平均|可比公司|板块均值|行业基准|大盘"
)
# 带组织后缀的公司名（美的集团/格力电器股份/惠而浦（中国）有限公司）
_ENTITY_COMPANY_RE = re.compile(
    r"[一-龥A-Za-z][一-龥A-Za-z0-9（）()]{0,11}?"
    r"(?:股份有限公司|有限责任公司|有限公司|公司|集团|股份|控股)"
)
# 裸主体名：仅识别「子句首 2-6 字 token 且紧贴指标词/数字（可隔连接词）」的
# 高精度形态（「美的毛利率26.39%」「奥克斯18.8%」「奥克斯为18.8%」）。
# 句中修饰语/谓语片段一律不作主体，宁可漏抽也不误抓（漏抽走最保守路径）。
# 子句首到首个锚点（指标词/数字）之间的 gap 整体必须是「2-6 字 token（+可选连接词）」
_ENTITY_BARE_TOKEN_RE = re.compile(r"([一-龥A-Za-z]{2,6}?)(?:的|为|是|达|录得|约|仅|报)?")
_ENTITY_BARE_BAD_FIRST_CHAR = frozenset(
    "在按若当其该本各每由从对与和及或虽已可能需应因将现此这那以如"
    # DAV-1158: 「据」是引语介词（据东方财富财报…），永非主体名首字
    "据"
)
_ENTITY_BARE_BAD_LAST_CHAR = frozenset(
    "至到为达报收约仅超略共总各其于和与或升降增跌涨破站入出满欠得"
)
# 修饰语子串：token 含以下任一片段即非主体名（「同比增长」「单月」「累计」等）
_ENTITY_BARE_BAD_SUBSTR = frozenset({
    "同比", "环比", "增长", "下降", "上涨", "下跌", "回升", "回落", "提升",
    "提高", "减少", "收窄", "走阔", "扩张", "收缩", "涨跌", "微增", "微降",
    "暴增", "大增", "暴跌", "维持", "突破", "跌破", "达到", "录得", "预计",
    "实现", "显示", "表明", "对应", "折合", "处于", "位于", "高于", "低于",
    "超过", "约为", "截至", "年初", "期末", "期内", "日均", "单月", "单季",
    "累计", "年化", "月度", "季度", "年度", "增速", "增幅", "跌幅", "涨幅",
    "均值", "平均", "合计", "总计", "占比", "比重", "口径", "情景", "假设",
    "测算", "推演", "预计", "预测", "底线", "上限", "下限", "区间", "中枢",
    "极端", "悲观", "乐观", "中性", "压力测试", "支撑", "阻力",
    # DAV-1146: 「中报披露」「年报披露」中的「披露」是动作修饰语——token 含之
    # 即截断取头部（中报披露→中报，落入 stopwords 丢弃），不得整段立为主体。
    "披露",
    # DAV-1163: 裸 token 误抓的修饰/连接片段——「公司上半年」「较过去长期」
    # 「并在」「日内」等被误立为主体后阻断跨报告主体一致性拼合。
    "日内", "并在", "上半年", "下半年", "过去", "长期", "短期", "月初", "月末",
    "当前", "目前", "单日", "单边", "连续", "累计",
    "全天", "量能", "冲高", "上方", "下方", "经历", "短中期", "中长期",
    "最低", "最高", "处于", "位于",
})
_ENTITY_STOPWORDS = frozenset({
    "公司", "本公司", "上市", "子公司", "集团", "报告期内", "报告期", "期内",
    "该股", "标的", "个股", "我们", "预计", "实现", "录得", "达到", "约为",
    "超过", "同比", "环比", "截至", "目前", "当前", "其中", "整体", "主营",
    "业务", "综合", "加权", "平均", "累计", "单季", "年化", "行业", "板块",
    "指数", "大盘", "市场", "同期", "上年", "去年", "今年", "明年", "年报",
    "中报", "季报", "年度", "上半年", "下半年", "一季度", "二季度", "三季度",
    "四季度", "年初", "年末", "季度", "月份", "数据", "显示", "根据", "公告",
    "财报", "业绩", "经营", "财务", "最新", "收盘", "开盘", "盘中", "早盘",
    "尾盘", "全天", "今日", "昨日", "明日", "估计", "维持", "判断", "认为",
    "指出", "表示", "来看", "而言", "方面", "口径", "维度", "情景", "假设",
    "乐观", "悲观", "中性", "基准", "目标", "空间", "弹性", "水平", "位置",
    "区间", "中枢", "附近", "以上", "以下", "以内", "左右", "前后", "之前",
    "之后", "当时", "此前", "此后", "增速", "增幅", "跌幅", "涨幅", "提升",
    "下降", "回升", "回落", "收窄", "走阔", "扩张", "收缩", "改善", "恶化",
    "承压", "修复", "拐点", "趋势", "格局", "逻辑", "驱动", "支撑", "压力",
    "风险", "机会", "对应", "反映", "体现", "表明", "说明", "验证", "证实",
    # DAV-1158: 数据源名（东财/同花顺…）是 provider/source 口径标注而非陈述
    # 主体——裸 token 不得立为 co: 实体，归属由 BoundNumber.provider 承载。
    "东财", "东方财富", "同花顺", "通达信", "大智慧", "万得", "新浪",
    "问财", "上交所", "深交所", "北交所", "港交所", "交易所",
    # DAV-1146: 「实际换手1.11%」「单日换手率达20%」中「实际」「单日」是修饰语
    # 而非主体名——不拦截会被裸 token 规则误抓为 co:实际/co:单日。
    "实际", "单日", "当期", "当季", "当月", "当年",
})
_ENTITY_BENCH_NORM = {"行业平均": "行业均值", "同业平均": "同业均值"}
_ENTITY_COMPANY_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "公司", "集团", "股份", "控股",
)
_ENTITY_CLAUSE_BREAKS = "，。；、！？!?,;：:（）()【】\n"
_ENTITY_INHERIT_WINDOW = 64


def _extract_entity_spans(
    cleaned: str,
    filtered_spans: list[tuple[int, int, str]],
    matches: list[re.Match],
) -> list[tuple[int, int, str]]:
    """抽取实体锚点 span 列表 (start, end, canonical)，canonical 带 co:/sym:/bench: 前缀。"""
    spans: list[tuple[int, int, str]] = []
    for m in _ENTITY_TICKER_RE.finditer(cleaned):
        spans.append((m.start(1), m.end(1), f"sym:{m.group(1)}"))
    for m in _ENTITY_BENCH_RE.finditer(cleaned):
        name = re.sub(r"\s+", "", m.group(0))
        spans.append((m.start(), m.end(), f"bench:{_ENTITY_BENCH_NORM.get(name, name)}"))
    for m in _ENTITY_COMPANY_RE.finditer(cleaned):
        name = m.group(0)
        for suf in _ENTITY_COMPANY_SUFFIXES:
            if name.endswith(suf):
                name = name[: -len(suf)]
                break
        name = name.strip("（）()").lstrip("与和及或跟")
        if len(name) >= 2 and name not in _ENTITY_STOPWORDS:
            spans.append((m.start(), m.end(), f"co:{name}"))
    # 裸主体名：子句首 run（2-6 字）紧贴指标词/数字（可隔连接词）才认作主体。
    # run 长度 >6 说明是「修饰语+指标」长串（如「极端压力测试显示极悲观年化净利
    # 底线」），直接放弃——漏抽按最保守处理，不误抓句中谓语片段。
    anchors = sorted({s[0] for s in filtered_spans} | {m.start() for m in matches})
    clause_begins = [0]
    for i, ch in enumerate(cleaned):
        if ch in _ENTITY_CLAUSE_BREAKS:
            clause_begins.append(i + 1)
    for cs in clause_begins:
        nxt = next((a for a in anchors if a > cs), None)
        if nxt is None:
            continue
        # 子句首→首个锚点之间的 gap 整体必须是「2-6 字 token（+可选连接词）」；
        # 「极端压力测试显示极悲观年化净利底线 60」这类长修饰串直接放弃。
        bm = _ENTITY_BARE_TOKEN_RE.fullmatch(cleaned[cs:nxt].strip())
        if not bm:
            continue
        token = bm.group(1)
        lead = len(token) - len(token.lstrip("与和及或跟"))
        token = token[lead:]
        # 修饰子串出现时截断取头部（「大金单月跌」→「大金」）；截后不足 2 字则弃
        for s in _ENTITY_BARE_BAD_SUBSTR:
            cut = token.find(s)
            if cut >= 0:
                token = token[:cut]
                break
        if (
            len(token) < 2
            or token in _ENTITY_STOPWORDS
            or token in _ENTITY_BARE_BAD_SUBSTR
            or token[0] in _ENTITY_BARE_BAD_FIRST_CHAR
            or token[-1] in _ENTITY_BARE_BAD_LAST_CHAR
            or any(k in token.lower() for k in _SORTED_METRIC_MAP_KEYS)
        ):
            continue
        t_start, t_end = cs + lead, cs + lead + len(token)
        # 与已识别的代码/基准/后缀公司 span 重叠时丢弃裸 token（如「沪深300指数」
        # 中的「沪深」不得再立为独立主体）
        if any(s < t_end and t_start < e for s, e, _ in spans):
            continue
        spans.append((t_start, t_end, f"co:{token}"))
    spans.sort(key=lambda s: (s[0], s[1]))
    return spans


def _bind_entity_for_number(
    text: str,
    n_start: int,
    entity_spans: list[tuple[int, int, str]],
) -> str | None:
    """给数字绑定主体：优先同子句内实体；缺省时向前继承最近主体（主体跨逗号延续）；
    同子句出现多个不同主体 → ENTITY_AMBIGUOUS（归属歧义，最保守处理）。"""
    window_start = max(0, n_start - _ENTITY_INHERIT_WINDOW)
    clause_start = window_start
    for i in range(n_start - 1, window_start - 1, -1):
        if text[i] in _ENTITY_CLAUSE_BREAKS:
            clause_start = i + 1
            break
    in_clause = [
        c for s, e, c in entity_spans if s >= clause_start and e <= n_start
    ]
    if len(set(in_clause)) > 1:
        return ENTITY_AMBIGUOUS
    if in_clause:
        return in_clause[-1]
    prev = [c for s, e, c in entity_spans if e <= clause_start and e > window_start]
    if prev:
        return prev[-1]
    return None


# ── DAV-1146: 语义角色 / 情景 / 期间基准进 binding key ──
# BoundNumber.role 取值：
#   ROLE_ACTUAL       实际值/默认（无修饰标记）
#   ROLE_THRESHOLD    披露阈值/门槛/红线/警戒/上下限等规则界线（「20%龙虎榜披露阈值」）
#   ROLE_SCENARIO     压力测试/情景/假设/测算/预计等假设值（「压力测试净利335–350亿」）
#   ROLE_INCREMENTAL  增量/新增/净增等边际量（「增量营收17亿」vs「总营收4565亿」）
# DAV-1157 残余修复追加：
#   ROLE_AGGREGATE    合计/总计/多分量加总的汇总值（「超大单加大单净流入0.9122亿」
#                     「合计净流入5亿」），不得与任一分项记录互判
#   ROLE_COMPONENT    「其中/分项」引导的分量值（「合计5亿，其中超大单3亿」中的 3亿）
#   ROLE_DELTA        变动量/幅度值（降幅/涨幅/变动/回落/提升等），非水平值——
#                     「LPR环比大跌10.45%至3.00%」中 10.45% 为 delta、3.00% 为水平
# BoundNumber.basis 取值（期间基准，独立于报告期 period 字段）：
#   BASIS_SINGLE_QUARTER  单季        BASIS_ANNUALIZED  年化/折年
#   BASIS_CUMULATIVE      累计/年初至今    BASIS_YOY 同比    BASIS_MOM 环比
#   None 未标注（同比 vs 环比互不可比，单侧未标注按最保守不判）
ROLE_ACTUAL = "actual"
ROLE_THRESHOLD = "threshold"
ROLE_SCENARIO = "scenario"
ROLE_INCREMENTAL = "incremental"
ROLE_AGGREGATE = "aggregate"
ROLE_COMPONENT = "component"
ROLE_DELTA = "delta"
# DAV-1168: 变动前基准水平——「每股净资产已由21元降至9.26元」中 21元 是
# 变动前起点值而非当前水平/变动量，不得与变动后水平值互判冲突
ROLE_BASELINE = "baseline"
BASIS_SINGLE_QUARTER = "single_quarter"
BASIS_ANNUALIZED = "annualized"
BASIS_CUMULATIVE = "cumulative"
BASIS_YOY = "yoy"
BASIS_MOM = "mom"

_ROLE_THRESHOLD_RE = re.compile(
    # 「披露」「退市」等高频歧义词不得裸用：「中报披露净利445亿」是实际披露值
    # 而非界线；「披露」仅在与界线词同现（披露阈值/披露标准…）时算 threshold。
    r"阈值|门槛|红线|警戒|预警|上限|下限|触发|临界|底线|龙虎榜|"
    r"平仓线|止损线|达标|不低于|不超过|不少于|至多|至少|"
    r"披露.{0,4}(?:阈值|门槛|红线|标准|要求)|退市(?:线|风险警示|标准)"
)
_ROLE_SCENARIO_RE = re.compile(
    r"压力测试|情景|情形|假设|测算|推演|预测|预计|预估|敏感性|模拟|"
    r"乐观|悲观|中性|极端|"
    # DAV-1168: 前瞻性投影词——「跌向8.00元」「目标价23.50」「将诱发踩踏杀跌至」
    # 中的数值是情景/目标推演值而非已录得实际值，不得与 actual 互判冲突
    r"跌向|看至|上看|下看|目标价|诱发|踩踏"
)
_ROLE_INCREMENTAL_RE = re.compile(r"增量|新增|净增|多增|增加额|边际")
# DAV-1157: 合计/汇总值——显式汇总词（合计/总计/共计/加总/总和/合并口径），
# 或资金流多分量加总短语（「超大单加大单」「主力和超大单」「超大单加大量」）。
_ROLE_AGGREGATE_RE = re.compile(
    r"合计|总计|共计|加总|总和|合并口径|合并计算|"
    r"(?:超大单|大单|中单|小单|全单|主力|散户)\s*[加和与及+]\s*"
    r"(?:超大单|大单|中单|小单|全单|主力|散户|大量|中量|小量|资金)|"
    # DAV-1168: 资金流分单连写合计——「大单超大单流出15.06亿」「中小单流出
    # 1.25亿」是无连接符的多分量加总，与「大单与中单合计」同义，不得与
    # 任一分项记录互判
    r"(?:(?:超?大|中|小)单){2,}|(?:(?:大|中|小)){2,}单"
)
# 连写/并列合计词命中后，其后若已出现分量列举（分量词或另一数字），该
# 合计词已被占先——「大单与中单博弈 | 大单 -1.1134 亿 / 中单 -1.7948 亿」
# 中两个数值均为分项而非合计值
_ROLE_AGG_COMPONENT_TAIL_RE = re.compile(r"超大单|大单|中单|小单")
# 「其中/分项/分量」引导的分量记录（「合计5亿，其中超大单3亿」中的 3亿）。
_ROLE_COMPONENT_RE = re.compile(r"其中|分项|分量|单项")
# 变动量/幅度值：降幅/涨幅/变动/回落/提升/增减等变化量（非水平值）。
# 「增加|增长」列入但受 _ROLE_LEVEL_SUFFIX_RE 守卫——「增长至4565亿」的 4565亿
# 是水平值而非变动量。
_ROLE_DELTA_RE = re.compile(
    r"降幅|跌幅|涨幅|增幅|变动|上调|下调|加息|降息|回落|回升|"
    r"收窄|走阔|下降|下跌|降低|下滑|增减|减少|大跌|暴跌|大涨|暴涨|"
    r"提升|提高|增长|增加|个百分点|"
    # DAV-1168: 变动词补全——「季降22%」「月环比大降10.45%」「同比骤降183亿」
    # 「环比激增9.83%」均为变动量而非水平值；裸「增/降」不收（增持/增至/降至
    # 等是水平语境，由 _ROLE_LEVEL_SUFFIX_RE 与所有格规则另行处理）
    r"骤降|大降|季降|月降|年降|周降|日降|再降|续降|激增|骤增|暴增|飙涨|飙升"
)
# 紧邻前子句以「至/到/为/达/录得/收于」收尾时，该数字是变动后的水平值，
# 不是变动量本身（「大跌10.45%至3.00%」中的 3.00%）。
_ROLE_LEVEL_SUFFIX_RE = re.compile(r"(?:至|到|为|达|得|维持|录得|收于|报收)\s*$")
_BASIS_SINGLE_Q_RE = re.compile(r"单季|单季度")
_BASIS_ANNUALIZED_RE = re.compile(r"年化|折年")
_BASIS_CUMULATIVE_RE = re.compile(r"累计|年初至今|年初以来|年内累计")
_BASIS_YOY_RE = re.compile(r"同比|较上年|较去年同期|较上年同期|同期相比")
_BASIS_MOM_RE = re.compile(r"环比|较上月|较前期")
_ROLE_PREV_CLAUSE_MAX = 16  # 「在压力测试情景下，净利或降至335亿」类短引导子句
# 前子句并入仅认「引导介词起头」的假设/界线设定子句（在|于|按|若|当|依|据|假设），
# 排除「阈值如上」「前述下限」这类回指性陈述子句对实际值的污染。
_ROLE_PREV_CLAUSE_LEAD_RE = re.compile(r"^\s*(?:在|于|按|若|当|依|据|假设|如果)")


def _classify_role_and_basis(
    text: str,
    n_start: int,
    n_end: int,
    unit: str = "",
) -> tuple[str, str | None]:
    """判定绑定数字的语义角色与期间基准；无修饰标记默认 (actual, None)。

    语境取「同子句前缀 + 数字后紧邻同子句修饰」（阈值/情景/基准常后置，如
    「20%的龙虎榜披露阈值」「335亿（压力测试）」）；前一子句仅当为「引导介词
    起头的短假设/界线设定子句」时并入（覆盖「在压力测试情景下，净利或降至
    335亿」），排除「阈值如上，实际换手1.11%」这类回指性陈述子句的污染。
    角色优先级固定为 threshold > scenario > incremental > aggregate >
    component > delta > actual（规则界线 > 假设值 > 边际量 > 汇总值 > 分量 >
    变动量 > 实际值），如「极端压力测试…净利底线60-65亿」归 threshold。
    delta 判定受 _ROLE_LEVEL_SUFFIX_RE 守卫：前子句以「至/到/为/达/录得」
    收尾的数字是变动后的水平值，归 actual（「大跌10.45%至3.00%」中 3.00%）。
    """
    segs = re.split(r"[，。；、,;（）()【】：:！？!?]", text[max(0, n_start - 40):n_start])
    ctx = segs[-1] if segs else ""
    if len(segs) >= 2 and segs[-2] and len(segs[-2]) <= _ROLE_PREV_CLAUSE_MAX and (
        _ROLE_PREV_CLAUSE_LEAD_RE.match(segs[-2])
        and (_ROLE_SCENARIO_RE.search(segs[-2]) or _ROLE_THRESHOLD_RE.search(segs[-2]))
    ):
        ctx = segs[-2] + ctx
    post = re.split(r"[，。；、,;：:！？!?]", text[n_end:n_end + 12])[0]
    ctx_full = ctx + "|" + post
    if _ROLE_THRESHOLD_RE.search(ctx_full):
        role = ROLE_THRESHOLD
    elif _ROLE_SCENARIO_RE.search(ctx_full):
        role = ROLE_SCENARIO
    elif _ROLE_INCREMENTAL_RE.search(ctx_full):
        role = ROLE_INCREMENTAL
    elif _ROLE_AGGREGATE_RE.search(ctx):
        # DAV-1168: 合计词占先守卫——最后一个合计词命中点之后若已出现分量
        # 列举（分量词或另一数字），当前数字是分项而非合计值本身；合计词
        # 只认数字前缀语境（ctx）——后置「与中小单净流入X亿」枚举的是另
        # 一分项，不得把本数字误标合计
        agg_matches = list(_ROLE_AGGREGATE_RE.finditer(ctx))
        if agg_matches:
            last_agg = agg_matches[-1]
            tail = ctx[last_agg.end():]
            # 枚举式合计词（「大单与中单」「超大单加大单」「大单超大单」）
            # 命中后若尾部再出现分量词，则当前数字是该分量的列举值而非
            # 合计值本身（「大单与中单博弈|大单 -1.1134 亿」）；显式汇总词
            # 「合计超大单…」中分量词是合计对象的限定语，不在此列
            enum_marker = bool(
                re.search(
                    r"[加和与及+]|(?:(?:超?大|中|小)单){2,}|(?:(?:大|中|小)){2,}单",
                    last_agg.group(0),
                )
            )
            role = (
                ROLE_ACTUAL
                if _NUMBER_WITH_UNIT_RE.search(tail)
                or (enum_marker and _ROLE_AGG_COMPONENT_TAIL_RE.search(tail))
                else ROLE_AGGREGATE
            )
        else:
            role = ROLE_AGGREGATE
    elif _ROLE_COMPONENT_RE.search(ctx_full):
        role = ROLE_COMPONENT
    elif _ROLE_DELTA_RE.search(ctx_full):
        role = ROLE_DELTA
        if _ROLE_LEVEL_SUFFIX_RE.search(ctx):
            # 「至/到/为/达」收尾默认水平值；仅「变动N%达X<非%量纲>」结构中
            # X 是变动额本身而非变动后水平（「环比激增9.83%达36.57亿」中
            # 36.57亿为增量）。「至/到/为」仍一律归水平值——「回落30%至50元」
            # 的 50元 是变动后价位。
            level_m = _ROLE_LEVEL_SUFFIX_RE.search(ctx)
            pre = ctx[:level_m.start()]
            pre_nums = list(_NUMBER_WITH_UNIT_RE.finditer(pre))
            is_delta_amount = bool(
                ctx[level_m.start():].startswith("达")
                and pre_nums
                and (pre_nums[-1].group(2) or "") == "%"
                and unit != "%"
                and _ROLE_DELTA_RE.search(pre)
            )
            if not is_delta_amount:
                role = ROLE_ACTUAL
    elif re.search(r"由\s*$", ctx) and re.match(
        r"\s*(?:降|跌|升|涨|增|减|回落|下调|上调|走低|走高)", post
    ):
        # 「由X降至Y」中 X 为变动前基准水平——仅当 X 后紧跟变动动词且自身
        # 无显式期间标注时成立（「由37.91%（2024）降至25.48%」中 37.91% 带
        # 显式期间，仍归 actual 可与同期间记录判真冲突）
        role = ROLE_BASELINE
    else:
        role = ROLE_ACTUAL
    # DAV-1168: 小节标题式情景/界线声明（「**压力测算底线**：」「**汇率
    # 敏感性**：」）作用于同一行内其后所有数字——仅兜底 actual，不覆盖
    # 局部已判定的更强角色（delta/component 等）。
    if role == ROLE_ACTUAL:
        line_start = text.rfind("\n", 0, n_start) + 1
        head_scope = text[line_start:n_start]
        if re.search(
            r"(?:压力测试|情景|情形|假设|测算|推演|预测|预计|预估|敏感性|模拟|"
            r"乐观|悲观|中性|极端)[^，。；、,;：:！？!?\n]{0,12}[：:]",
            head_scope,
        ):
            role = ROLE_SCENARIO
        elif re.search(
            r"(?:阈值|门槛|红线|警戒|预警|上限|下限|临界|底线|平仓线|止损线)"
            r"[^，。；、,;：:！？!?\n]{0,12}[：:]",
            head_scope,
        ):
            role = ROLE_THRESHOLD
    if _BASIS_SINGLE_Q_RE.search(ctx_full):
        basis = BASIS_SINGLE_QUARTER
    elif _BASIS_ANNUALIZED_RE.search(ctx_full):
        basis = BASIS_ANNUALIZED
    elif _BASIS_CUMULATIVE_RE.search(ctx_full):
        basis = BASIS_CUMULATIVE
    elif role == ROLE_DELTA and _BASIS_YOY_RE.search(ctx_full):
        # 同比/环比是比较方向，仅落在变动量上——水平值后的「（同比+4.83%）」
        # 括号注释不得把水平值标成 yoy，否则同指标真矛盾被错放（DAV-1088 A3）。
        basis = BASIS_YOY
    elif role == ROLE_DELTA and _BASIS_MOM_RE.search(ctx_full):
        basis = BASIS_MOM
    else:
        basis = None
    return role, basis


# ── DAV-1158: provider/source 口径命名空间（资金流/行情类指标）──
# 数据源/字段代码口径进 binding key：不同数据源（东财 vs 同花顺）或不同字段口径
# （东财 r0_net vs 同花顺 netamount）的数值语义不同，不得互作 contradiction
# ground truth；同 provider 同口径的真矛盾仍拦。price_basis（复权/收盘口径）
# 归 DAV-1142，本卡只管 provider/source 口径层。
# provider 取值为排序后的 token 元组：
#   src:<source>   数据源（eastmoney/ths/wind/tdx/dzh/sina/tushare/exchange）
#   fld:<field>    字段代码口径（r0_net/netamount/zljlr/main_inflow 等）
# 命名空间仅挂在资金流/行情类规范化指标上——「据东财财报净利润45亿」这类财务
# 科目数值与数据源无关，不得因 provider 单侧标注而放松真矛盾判定。
_PROVIDER_SCOPED_METRICS = {
    # 资金流分单/两融（严格指标）
    "主力", "超大单", "大单", "两融",
    # 未归一化的资金流/行情 raw canonical（词表直通，参与匹配但不判冲突）
    "中单", "小单", "全单", "散户小单", "净流入", "净流出", "流入", "流出",
    "北向", "北向资金", "机构", "外资", "游资", "散户", "龙虎榜",
    "大宗交易", "筹码", "吸筹", "出货", "增仓", "减仓", "席位",
    # 行情/量价（含严格指标 换手率/量比/股价/最高价/最低价）
    "成交量", "成交额", "换手率", "换手", "量比",
    "股价", "收盘价", "开盘价", "开盘", "最高价", "最低价", "布林",
}

# 数据源 token 正则：英文 token 必须以非 [A-Za-z0-9_] 字符为界（Python \b 把
# CJK 算 \w，「显示r0_net」中 \b 失效；「ths/wind/sina」裸用会误中 months/
# window 等英文单词）。
_PROVIDER_SOURCE_RES: tuple[tuple[str, "re.Pattern"], ...] = (
    ("src:eastmoney", re.compile(r"东方财富|东财|(?<![A-Za-z0-9_])(?:eastmoney|choice)(?![A-Za-z0-9_])", re.I)),
    ("src:ths", re.compile(r"同花顺|问财|(?<![A-Za-z0-9_])(?:10jqka|iwencai|ths)(?![A-Za-z0-9_])", re.I)),
    ("src:wind", re.compile(r"万得|(?<![A-Za-z0-9_])wind(?![A-Za-z0-9_])", re.I)),
    ("src:tdx", re.compile(r"通达信")),
    ("src:dzh", re.compile(r"大智慧")),
    ("src:sina", re.compile(r"新浪|(?<![A-Za-z0-9_])sina(?![A-Za-z0-9_])", re.I)),
    ("src:tushare", re.compile(r"(?<![A-Za-z0-9_])tushare(?![A-Za-z0-9_])", re.I)),
    ("src:exchange", re.compile(r"上交所|深交所|北交所|港交所|交易所")),
)
# 字段代码口径（资金流字段名即口径标签：东财 r0_net vs 同花顺 netamount）
_PROVIDER_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(r0_net|r0_in|r0_out|netamount|zljlr|zjlr|"
    r"main_inflow|main_net_inflow|moneyflow|smc_in|smc_out)(?![A-Za-z0-9_])",
    re.I,
)
_PROVIDER_WINDOW = 48  # 「同花顺数据：r0_net 中单净流入1465.71万」类引导前缀窗口
_PROVIDER_POST_WINDOW = 16


def _nearest_tokens(
    scope: str,
    res: tuple[tuple[str, "re.Pattern"], ...],
) -> set[str]:
    """取 scope 内最靠后（离数字最近）的一簇数据源 token；同终点并列时并入。"""
    best_end = -1
    tokens: set[str] = set()
    for name, rx in res:
        for m in rx.finditer(scope):
            if m.end() > best_end:
                best_end = m.end()
                tokens = {name}
            elif m.end() == best_end:
                tokens.add(name)
    return tokens


def _bind_provider_for_number(
    text: str,
    n_start: int,
    n_end: int,
    metric: str | None,
) -> tuple[str, ...] | None:
    """给资金流/行情类数字绑定数据源/口径命名空间；无标注或指标不在作用域返回 None。

    语境 = 数字前 _PROVIDER_WINDOW 窗口内「最近的一簇」provider 标注（覆盖
    「东财口径：」「同花顺数据：r0_net」这类冒号引导前缀——冒号是子句断点，
    口径词作用于其后的整段数值陈述）+ 数字后同子句紧邻标注（「1465.71万
    （东财口径）」）。取最近一簇而非全窗并集，避免「同花顺…东财…」长窗内
    两个源并列污染归属。
    """
    if metric not in _PROVIDER_SCOPED_METRICS:
        return None
    window = text[max(0, n_start - _PROVIDER_WINDOW):n_start]
    post = re.split(r"[，。；、,;：:！？!?]", text[n_end:n_end + _PROVIDER_POST_WINDOW])[0]
    tokens = _nearest_tokens(window, _PROVIDER_SOURCE_RES)
    tokens |= _nearest_tokens(post, _PROVIDER_SOURCE_RES)
    # 字段代码口径同规则：窗内取最近一簇 + 数字后同子句标注
    fld_tokens: set[str] = set()
    best_fld_end = -1
    for m in _PROVIDER_FIELD_RE.finditer(window):
        tok = f"fld:{m.group(1).lower()}"
        if m.end() > best_fld_end:
            best_fld_end = m.end()
            fld_tokens = {tok}
        elif m.end() == best_fld_end:
            fld_tokens.add(tok)
    tokens |= fld_tokens
    tokens |= {f"fld:{m.group(1).lower()}" for m in _PROVIDER_FIELD_RE.finditer(post)}
    return tuple(sorted(tokens)) or None


def _providers_comparable(ev_bn: "BoundNumber", l_bn: "BoundNumber") -> bool:
    """DAV-1158: provider/source 口径可比性——不同数据源或不同字段口径的数值
    不得互作 contradiction ground truth；同 provider 同口径真矛盾仍拦。
    单侧未标注按最保守不判（与 _entities_comparable 同一原则）。"""
    if ev_bn.provider is None and l_bn.provider is None:
        return True
    if ev_bn.provider is None or l_bn.provider is None:
        return False
    return ev_bn.provider == l_bn.provider


# ── DAV-1169: timepoint/价格基准时点命名空间 ──
# 同一 (entity, metric, basis) 但 period/timepoint 不同的数值不得互判冲突：
# 存量时点值 vs 单日流量（两融余额 vs 单日融资净偿还）、历史高点/成本均价
# vs 现价（自 42.48 高点累跌、增持均价 82.71 vs 现价 71.52）。与 provider 层
# 同原则：时点标签挂在作用域指标上，双侧均标注且不同、或单侧未标注按最保
# 守不判（「现价/收盘价」本身不打标签——证据显式现价声明保持未标注，仍可
# 与报告未标注记录判真冲突）。
TIMEPOINT_STOCK = "tp:stock"  # 存量时点值（余额/存量/出清至 X/仓位）
TIMEPOINT_FLOW = "tp:flow"    # 单日/当期流量（净偿还/净流入/单日）
TIMEPOINT_HIST = "tp:hist"    # 历史时点价格锚点（自 X 高点/冲高至 X）
TIMEPOINT_COST = "tp:cost"    # 成本/均价基准（增持均价/成本价/买入均价）

# 余额 vs 流量共用同名指标的族（「两融」同名既指余额存量也指单日净偿还）；
# 超大单/大单等资金流分单不在此列——流出/流入是该族的常规陈述，不得因
# 时点标签单侧化放松真矛盾（DAV-1148 #12 超大单正例守卫）。
_TIMEPOINT_BALANCE_METRICS = {"两融", "融资", "融券", "保证金"}
_TIMEPOINT_PRICE_METRICS = {"股价", "收盘价", "开盘价", "最高价", "最低价", "现价", "价格", "均价"}

_TP_STOCK_RE = re.compile(r"余额|存量|结存|出清|仓位|保有量")
_TP_FLOW_RE = re.compile(
    r"净偿还|净流入|净流出|净买入|净卖出|净申购|净赎回|单日|当日|日净"
)
_TP_COST_PRE_RE = re.compile(
    r"(?:增持|回购|买入|建仓|加仓|减持|持仓|定增|行权|转股)?"
    r"\s*(?:均价|成本价|平均价|成本线)\s*$"
)
_TP_HIST_PRE_RE = re.compile(
    r"(?:自|从|曾至|曾达|冲高至|反弹至|上探|下探至|见高点?|最高点?|峰值|"
    r"历史高点?|前期高点?|前高)\s*$"
)
_TP_HIST_POST_RE = re.compile(
    r"^\s*(?:元)?\s*(?:高点|峰值|累跌|累计跌|回落|见顶|起跌|后回落|后快速回落)"
)


def _bind_timepoint_for_number(
    text: str,
    n_start: int,
    n_end: int,
    metric: str | None,
) -> str | None:
    """给余额/流量共用指标与价格类数字绑定时点/价格基准；无标注或非作用域返回 None。

    语境取「同子句前缀 + 数字后紧邻同子句修饰」（与 _classify_role_and_basis
    同窗口约定）。balance 族：前缀含「余额/存量/出清」归 tp:stock，含「净偿还/
    净流入/单日/当日」归 tp:flow；price 族：前缀以「均价/成本价/成本线」收尾
    归 tp:cost，以「自/从/冲高至/高点」收尾或后随「高点/累跌/回落」归 tp:hist。
    """
    if metric not in _TIMEPOINT_BALANCE_METRICS and metric not in _TIMEPOINT_PRICE_METRICS:
        return None
    segs = re.split(r"[，。；、,;（）()【】：:！？!?]", text[max(0, n_start - 40):n_start])
    ctx = segs[-1] if segs else ""
    post = re.split(r"[，。；、,;：:！？!?]", text[n_end:n_end + 16])[0]
    if metric in _TIMEPOINT_BALANCE_METRICS:
        if _TP_STOCK_RE.search(ctx):
            return TIMEPOINT_STOCK
        if _TP_FLOW_RE.search(ctx) or _TP_FLOW_RE.search(post):
            return TIMEPOINT_FLOW
        return None
    # price 族：成本/均价基准优先于历史时点锚点
    if _TP_COST_PRE_RE.search(ctx):
        return TIMEPOINT_COST
    if _TP_HIST_PRE_RE.search(ctx) or _TP_HIST_POST_RE.match(post):
        return TIMEPOINT_HIST
    return None


def _timepoints_comparable(ev_bn: "BoundNumber", l_bn: "BoundNumber") -> bool:
    """DAV-1169: 时点/价格基准可比性——存量时点 vs 单日流量、历史高点/成本
    均价 vs 现价不得互作 contradiction ground truth；同时点同基准真矛盾仍拦。
    单侧未标注按最保守不判（与 _entities_comparable/_providers_comparable 同原则）。"""
    if ev_bn.timepoint is None and l_bn.timepoint is None:
        return True
    if ev_bn.timepoint is None or l_bn.timepoint is None:
        return False
    return ev_bn.timepoint == l_bn.timepoint


def _timepoints_join_compatible(t1: str | None, t2: str | None) -> bool:
    """DAV-1169 返修（DAV-1173 🟡-2）：佐证/拼合方向的时点兼容门。

    与冲突判定的保守口径不同——match 方向要求数值相等才成立，「证据标注
    时点、报告未标注」的真同源数值不应被单侧标注门拦下（此前 verified
    侧失血 11 条）；仅双侧均标注且不同时才不拼（存量值不得拿流量记录佐证）。"""
    if not t1 or not t2:
        return True
    return t1 == t2
    if ev_bn.timepoint is None and l_bn.timepoint is None:
        return True
    if ev_bn.timepoint is None or l_bn.timepoint is None:
        return False
    return ev_bn.timepoint == l_bn.timepoint


def _semantics_comparable(ev_bn: "BoundNumber", l_bn: "BoundNumber") -> bool:
    """冲突判定的语义角色/期间基准可比性：角色必须相同（actual 不得与 threshold/
    scenario/incremental 互判）；期间基准必须一致（单季 vs 年化 vs 累计互不可比，
    单侧未标注按最保守不判）——与 _entities_comparable 同一保守原则。"""
    if ev_bn.role != l_bn.role:
        return False
    return ev_bn.basis == l_bn.basis


def _entities_consistent_for_join(ev_entity: str | None, l_entity: str | None) -> bool:
    """DAV-1147: 跨报告聚合拼合的主体一致性门。

    与冲突判定的 _entities_comparable 不同：聚合拼合是「验证事实存在性」，
    单侧未指明主体时默认同报告目标股、允许拼；仅当双侧均指明且主体不同
    （或任一侧歧义）时禁止强拼——不同主体的同值不得互相佐证。"""
    if ev_entity is None or l_entity is None:
        return True
    if ev_entity == ENTITY_AMBIGUOUS or l_entity == ENTITY_AMBIGUOUS:
        return False
    return ev_entity == l_entity


def _periods_join_compatible(p1: str | None, p2: str | None) -> bool:
    """DAV-1147: 跨报告聚合拼合的期间一致性门（与 _is_bound_num_match 同一规则）。

    双侧均指明期间时要求相同或共享同一年度前缀；单侧未指明不阻塞拼合
    （验证方向的保守宽松，与数值匹配一致）。"""
    if not p1 or not p2:
        return True
    if p1 == p2:
        return True
    return bool(
        p1[:4] == p2[:4]
        and re.match(r"^\d{4}", p1)
        and re.match(r"^\d{4}", p2)
    )


_WHITESPACE_RE = re.compile(r"\s+")


def _entities_comparable(ev_entity: str | None, l_entity: str | None) -> bool:
    """冲突判定的主体可比性：双侧均未指明主体（默认同一报告目标）或规范名一致才可比。

    单侧缺主体 / 任一侧歧义 / 主体类型或名称不同（公司 vs 指数 / 行业均值）一律
    按最保守——不可比、不判冲突（由调用方记 entity_scope_gap）。"""
    if ev_entity == ENTITY_AMBIGUOUS or l_entity == ENTITY_AMBIGUOUS:
        return False
    if ev_entity is None and l_entity is None:
        return True
    if ev_entity is None or l_entity is None:
        return False
    return ev_entity == l_entity


# DAV-1163: 区间/约数修饰（约X/近X/超X/X余/X左右）不是点值声明，按方向界或
# 放宽容差处理；「A-B」区间对的两个端点合并为一个区间事实，区间内有值即覆盖。
BOUND_MIN = "min"        # 超/超过/逾/不低于/至少/X以上/X余/X多 → 下界（实际值 ≥ X）
BOUND_MAX = "max"        # 不足/不到/低于/至多/X以下/X以内 → 上界（实际值 ≤ X）
BOUND_APPROX = "approx"  # 约/近/左右/上下/前后/附近 → 约数（放宽容差）

# 注意：「高于/低于/大于/小于」不是方向界——「低于现价5.6%」中 5.6% 是
# 差值量而非上界，故不列入（误列会把差值当上界放水）。
_BOUND_MIN_PREFIX_RE = re.compile(r"(?:超过|超|逾|不低于|不少于|至少|≥|>)\s*$")
_BOUND_MAX_PREFIX_RE = re.compile(r"(?:不足|不到|未及|不超过|至多|≤|<)\s*$")
# DAV-1163 返修（DAV-1164 🟡-1）：`~` 移出约数前缀——「变动-3%~2%」中
# `~` 是区间连接符而非约数标记，端点不得被误标 approx。
_BOUND_APPROX_PREFIX_RE = re.compile(r"(?:约为|大约|约|近|接近|大概|差不多|近似)\s*$")
_BOUND_APPROX_SUFFIX_RE = re.compile(r"^\s*(?:左右|上下|前后|附近)")
_BOUND_MIN_SUFFIX_RE = re.compile(
    r"^\s*(?:以上|及以上)|^\s*(?:余|多)(?=\s*(?:亿|万|%|％|元|股|点|倍|个|家|次|手|户|人|吨|桶|天|日|月|年|$|[^一-龥]))"
)
_BOUND_MAX_SUFFIX_RE = re.compile(r"^\s*(?:以下|以内|及以下)")
# 区间连接符（「67-77元」「91%~92%」「20-30亿」）；负号被数字正则吞作符号位时
# 由「紧邻 + 下一数字以 - 起头」形态识别。
_RANGE_CONNECTOR_RE = re.compile(r"\s*[-~–—至到]\s*")
# 「约X」类约数的相对容差（约数本身声明的是近似量级，容差宽于点值 2%）
_APPROX_REL_TOL = 0.10


class BoundNumber:
    __slots__ = ("val", "unit", "raw", "metric", "period", "raw_metric", "stype", "entity", "role", "basis", "bound", "range_span", "provider", "timepoint")

    def __init__(self, val: float, unit: str, raw: str, metric: str | None, period: str | None, raw_metric: str | None, stype: str = STYPE_UNKNOWN, entity: str | None = None, role: str = ROLE_ACTUAL, basis: str | None = None, bound: str | None = None, range_span: tuple[float, float] | None = None, provider: tuple[str, ...] | None = None, timepoint: str | None = None):
        self.val = val
        self.unit = unit
        self.raw = raw
        self.metric = metric          # None = 显式「未绑定」标记
        self.period = period
        self.raw_metric = raw_metric
        self.stype = stype
        self.entity = entity          # None = 未指明主体；ENTITY_AMBIGUOUS = 多主体歧义
        self.role = role              # DAV-1146: actual/threshold/scenario/incremental
        self.basis = basis            # DAV-1146: 单季/年化/累计期间基准，None = 未标注
        self.bound = bound            # DAV-1163: min/max/approx 方向界或约数修饰，None = 点值
        self.range_span = range_span  # DAV-1163: 「A-B」区间对合并的 (lo,hi)，None = 非区间
        self.provider = provider      # DAV-1158: 数据源/口径命名空间（src:*/fld:* 元组），None = 未标注
        self.timepoint = timepoint    # DAV-1169: 时点/价格基准（tp:stock/flow/hist/cost），None = 未标注

    def __repr__(self) -> str:
        return f"BoundNumber({self.raw!r}, val={self.val}, unit={self.unit!r}, metric={self.metric!r}, period={self.period!r}, stype={self.stype!r}, entity={self.entity!r}, role={self.role!r}, basis={self.basis!r}, bound={self.bound!r}, range={self.range_span!r}, provider={self.provider!r}, timepoint={self.timepoint!r})"


# DAV-1157: 数字级期间绑定——整句归一期间（normalize_period(text)）会把同一行
# 共存的两期数字压成同一期间（「毛利率由37.91%（2024）降至25.48%（2026H1）」
# 两值均得 2026H1），真不同期被误判冲突；以下规则按数字就近补绑局部期间。
_PAREN_POST_NUM_RE = re.compile(r"\s*[（(【\[]\s*([^）)】\]]{1,16})")
_CLAUSE_BREAK_FOR_PERIOD = re.compile(r"[，。；、,;：:！？!?（）()【】]")
# DAV-1169: 空格断开的年度/全年期间不在 _DATE_MASK_PATTERN 掩码内，子句
# 引导期间检测需补检，否则「如 2025 全年经营现金流…241.86 亿」漏绑 2025。
_CLAUSE_YEAR_HINT_RE = re.compile(r"\d{4}\s*(?:全\s*年|年(?:度)?)")


def _bind_period_for_number(
    text: str,
    n_start: int,
    n_end: int,
    fallback: str | None,
) -> str | None:
    """按数字就近补绑局部期间；无局部标注时回退整句期间 fallback。

    规则（优先级递减）：
    1. 后置括号期间——「37.91%（2024）」「25.48%（2026H1）」括号内能归一出
       期间时优先采用（解决同一行两期共存）。
    2. 子句引导期间——「2024年营收100亿」「较2024年的37.91%」期间位于本子句
       首个非期间数字之前时绑定本子句数字；尾随前一数字的期间（「37.91%（2024）
       降至25.48%」中的 2024）归前数字所有，不向后绑。
    """
    m = _PAREN_POST_NUM_RE.match(text[n_end:n_end + 20])
    if m:
        inner = m.group(1).strip()
        p = normalize_period(inner)
        if p:
            return p
        # 裸年份括号「（2024）」——normalize_period 要求「年」后缀，此处宽收
        if re.fullmatch(r"\d{4}", inner):
            return inner
    clause = _CLAUSE_BREAK_FOR_PERIOD.split(text[max(0, n_start - 40):n_start])[-1]
    if clause:
        # DAV-1163: 「去年同期/上年同期」把本子句数字的期间前移一年——
        # 「由去年同期+311.37亿元骤降至-21.54亿元」中 311.37 属 2025H1 而非
        # 整句归一期间 2026H1，不前移会被期间门误判不可比。
        yoy_anchor = re.search(r"去年同期|上年同期|上一年同期", clause)
        if yoy_anchor and not _NUMBER_WITH_UNIT_RE.search(clause[yoy_anchor.end():]):
            # 「去年同期」仅锚定紧随其后的第一个数字——其后若已出现其他数字，
            # 锚定已被占先（「去年同期+311.37亿骤降至-21.54亿」中 -21.54 仍属
            # 当期），不得前移。
            generic = normalize_period(clause) or fallback
            if generic and re.match(r"\d{4}", generic):
                return str(int(generic[:4]) - 1) + generic[4:]
        first_period = (
            _DATE_MASK_PATTERN.search(clause)
            or _CLAUSE_YEAR_HINT_RE.search(clause)
        )
        if first_period:
            masked = _DATE_MASK_PATTERN.sub(
                lambda mm: " " * len(mm.group(0)), clause
            )
            # DAV-1169 返修（DAV-1173 🟢-2）：year-hint 命中段一并保位掩码——
            # 「2025 全年」内部数字不得被当作子句首个数值参与位置判断
            masked = _CLAUSE_YEAR_HINT_RE.sub(
                lambda mm: " " * len(mm.group(0)), masked
            )
            first_num = _NUMBER_WITH_UNIT_RE.search(masked)
            if not first_num or first_num.start() >= first_period.start():
                p = normalize_period(clause)
                if p:
                    return p
    return fallback


def extract_bound_numbers(text: str, default_period: str | None = None) -> list[BoundNumber]:
    """Extract numbers from text and bind each number to its closest specific metric and period."""
    if not text:
        return []
    period = normalize_period(text) or default_period
    # DAV-1157 返修：千分位逗号归一（3,046.11 -> 3046.11）必须早于日期掩码——
    # 逗号删除不保位，先掩码后删逗号会使 cleaned 坐标相对 text 左偏，
    # _bind_period_for_number 用 cleaned 坐标切 text 时错过后置括号期间。
    # period_view = 未掩码日期 + 已去逗号，与 cleaned 严格同坐标系，专供
    # 数字级期间绑定读取局部期间标注（「（2024）」「2026H1」）。
    period_view = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    cleaned = _DATE_MASK_PATTERN.sub(lambda m: " " * len(m.group(0)), period_view)
    # DAV-1158: 字段代码口径（r0_net/netamount 等）保位掩码——其内部数字
    # （r0_net 的 0）不得被当作独立数值抽取；口径信息由 provider 绑定另行读取
    cleaned = _PROVIDER_FIELD_RE.sub(lambda m: " " * len(m.group(0)), cleaned)
    text_lower = cleaned.lower()
    metric_spans = []
    for kw in _SORTED_METRIC_MAP_KEYS:
        start = 0
        while True:
            idx = text_lower.find(kw.lower(), start)
            if idx == -1:
                break
            metric_spans.append((idx, idx + len(kw), kw))
            start = idx + len(kw)
    metric_spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    filtered_spans = []
    for s in metric_spans:
        if not filtered_spans or s[0] >= filtered_spans[-1][1]:
            filtered_spans.append(s)

    # 括号内指标词仅作限定语（如「营业支出/成本维度」），主体绑定优先非括号指标
    paren_mask = [False] * (len(cleaned) + 1)
    depth = 0
    for i, ch in enumerate(cleaned):
        if ch in "（(【[":
            depth += 1
        elif ch in "）)】]":
            depth = max(0, depth - 1)
        paren_mask[i] = depth > 0

    matches = list(_NUMBER_WITH_UNIT_RE.finditer(cleaned))
    entity_spans = _extract_entity_spans(cleaned, filtered_spans, matches)
    res = []
    res_spans: list[tuple[int, int]] = []  # 与 res 平行的 (n_start, n_end)
    last_res_match_end = -1  # res 中最后一个 BoundNumber 对应的 match.end()
    for i, m in enumerate(matches):
        val_str = m.group(1)
        unit_str = m.group(2) or ""
        if not unit_str and i + 1 < len(matches):
            between = cleaned[m.end():matches[i+1].start()]
            # DAV-1147: 补全角 dash（–—）——「回购10–12亿元」中 10 须继承单位亿元，
            # 否则前半截归一为 raw 量纲，跨报告数值拼合拼不上。
            if re.fullmatch(r"\s*[-~–—至到]\s*", between) and matches[i+1].group(2):
                unit_str = matches[i+1].group(2)
        norm = normalize_numeric_value(val_str, unit_str)
        if norm is None:
            continue
        val, unit = norm
        raw = m.group(0).strip()
        n_start, n_end = m.span()

        # 前向绑定：指标词只能绑定同一子句内其后最近的数字；若指标与目标数字之间
        # 已隔着另一个数字且无子句边界（，、同比/环比），该指标已被占先，不得再绑
        # （「量比1.4收于0.15」中 0.15 不得绑到量比）。含同比/环比的比较从句允许
        # 更长距离（「现金流量净额达到 3,046.11 亿元，相较于 2025H1…同比暴增 +126.54%」）。
        closest_prec = None
        min_prec_dist = 9999
        closest_prec_paren = None
        min_prec_paren_dist = 9999
        for m_start, m_end, m_raw in filtered_spans:
            if m_end <= n_start:
                # 距离与占先判定均基于剔除括号内容后的文本：
                # 「毛利率（营业总收入扣除营业支出/成本维度）为 50.95%…下降 1.38」中
                # 括号内说明文字不拉开指标与数字的语义距离。
                intervening_raw = cleaned[m_end:n_start]
                intervening = "".join(
                    ch for i, ch in enumerate(intervening_raw, start=m_end)
                    if not paren_mask[i]
                )
                # 占先/语境信号（，、同比/环比）看原文；语义距离看剔除括号后的文本
                ctx = intervening_raw
                max_dist = 64 if re.search(r"同比|环比", ctx) else 40
                if len(intervening) >= max_dist:
                    continue
                if "；" in ctx or ";" in ctx or "。" in ctx:
                    continue
                # DAV-1144: 括号内数字不得跨括号继承远处的无关指标——括号是紧邻
                # 前一数字的限定语（「报20700日元（月环比-15.58%）」中 -15.58% 属
                # 于大金股价的环比，不得回退绑到句首的惠而浦「股价」）；其归属由
                # 下方的「金额+括号同比」显式配对规则处理，配对不上则保持未绑定。
                # DAV-1147: 跨子句逗号同样不得继承——「流动比率1.76，拟10亿元回购」
                # 中逗号后数字属新子句，仅当语境含同比/环比比较从句信号（占先豁免）
                # 时才允许跨数字长距绑定，否则前句指标已被 1.76 占先。
                if _NUMBER_WITH_UNIT_RE.search(ctx) and (
                    paren_mask[n_start]
                    or not re.search(r"同比|环比", ctx)
                ):
                    continue
                dist = len(intervening)
                if any(paren_mask[p] for p in range(m_start, m_end)):
                    if dist < min_prec_paren_dist:
                        min_prec_paren_dist = dist
                        closest_prec_paren = m_raw
                    continue
                if dist < min_prec_dist:
                    min_prec_dist = dist
                    closest_prec = m_raw
        if closest_prec is None:
            closest_prec = closest_prec_paren

        # 后继绑定仅允许数字与指标名紧邻（可隔「的」）。
        # 「45.40元对应PB」中隔着关系动词「对应」的 PB 是碰巧出现的其他指标，
        # 禁止就近猜测误绑；无法确定指标时保持 metric=None（显式未绑定标记）。
        closest_succ = None
        succ_gap = None
        min_succ_dist = 9999
        for m_start, m_end, m_raw in filtered_spans:
            if m_start >= n_end and (m_start - n_end) < 15:
                intervening = cleaned[n_end:m_start]
                # DAV-1163: 「日/月」期间后缀允许后继绑定——「10日均线」「50日
                # SMA」「20日VWMA」中的期限数字属于均线本身。
                if intervening.strip() not in ("", "的", "日", "月", "个月"):
                    continue
                dist = m_start - n_end
                if dist < min_succ_dist:
                    min_succ_dist = dist
                    closest_succ = m_raw
                    succ_gap = intervening

        # DAV-1144: 「金额+括号同比」配对——括号内 % 紧邻前一金额数字时，显式
        # 继承该金额绑定的主体指标（「归母净利2.46亿元(-14.73%)」中 -14.73%
        # 绑净利润而非营收）。词表缺失时不得抓错指标，此规则优先于就近回退。
        if (
            unit == "%" and res and last_res_match_end != -1
            and n_start > 0 and paren_mask[n_start]
        ):
            gap = cleaned[last_res_match_end:n_start]
            if re.fullmatch(r"\s*[（(【\[]\s*", gap) and res[-1].raw_metric:
                closest_prec = res[-1].raw_metric
                closest_succ = None

        # DAV-1163: 「N日均线/N日EMA/N日SMA/N日VWMA」中的期限数字归属均线——
        # 均线族后继绑定优先于前向泛指标（「现价高于10日均线91.14」中 10 属
        # 均线而非现价）。
        if closest_succ and _canonicalize_metric(closest_succ, unit) == "均线":
            closest_prec = None

        # DAV-1159: 「X%的<科目>」所有格结构——% 数字量化的是紧随其后的科目
        # 名词（「压降负债率69.4%的利息支出」中 69.4% 是利息支出的降幅，不得
        # 绑前面的负债率与 60% 阈值互判伪冲突）；「的+指标」后继优先于前向。
        if (
            unit == "%" and closest_succ and succ_gap is not None
            and succ_gap.strip() == "的"
        ):
            closest_prec = None

        # DAV-1163: 「变动量+至/到+水平值」结构中的水平值继承变动量的指标——
        # 「毛利率骤降5.99pct至28.28%」中 28.28% 同属毛利率，占先规则会把它
        # 留在未绑定态（或错绑远处指标），与同事实记录互判失配。
        if closest_prec is None and res and last_res_match_end != -1:
            gap = cleaned[last_res_match_end:n_start]
            if re.search(r"(?:至|到)\s*$", gap) and res[-1].raw_metric:
                closest_prec = res[-1].raw_metric
                closest_succ = None

        raw_metric = closest_prec or closest_succ
        metric = _canonicalize_metric(raw_metric, unit)
        # DAV-1163: 价格类指标只绑元/股量纲——「收盘处于日内绝对低位（0.03）」
        # 中 0.03 是收盘位置分位而非股价，「低于现价5.6%」中 5.6% 是差值比率；
        # 误绑严格股价会与未绑定证据互判失配/伪冲突。
        if metric in {"股价", "开盘价", "最高价", "最低价"} and (
            unit == "%" or (unit not in {"元", "股"} and paren_mask[n_start])
        ):
            metric = None
            raw_metric = None
        stype = _classify_semantic_type(cleaned, n_start, n_end, unit, metric)
        if (
            unit == "%" and stype == STYPE_UNKNOWN and raw_metric
            and n_start > 0 and paren_mask[n_start]
        ):
            # 括号内 % 直接挂在金额数字之后是同比/环比限定语，语义为同比增速
            stype = STYPE_GROWTH
        if unit == "%" and metric == "毛利":
            # % 绑定到「毛利」只可能是毛利率语义（占比/增速下毛利的 % 无意义），
            # 而「净利 + %」不得折叠——它可能是净利增速或占比，保持净利润。
            metric = "毛利率"
        entity = _bind_entity_for_number(cleaned, n_start, entity_spans)
        role, basis = _classify_role_and_basis(cleaned, n_start, n_end, unit)
        # DAV-1157: 数字级期间覆盖（后置括号/子句引导），无局部标注回退整句期间
        num_period = _bind_period_for_number(period_view, n_start, n_end, period)
        # DAV-1163: 区间/约数修饰提取——「约/近/超/不足」前缀与「左右/以上/余」
        # 后缀把点值声明变为方向界或约数，匹配语义在 _value_covered 中处理。
        bound = None
        prefix_ctx = cleaned[max(0, n_start - 8):n_start]
        suffix_ctx = cleaned[n_end:n_end + 8]
        if _BOUND_MIN_PREFIX_RE.search(prefix_ctx) or _BOUND_MIN_SUFFIX_RE.match(suffix_ctx):
            bound = BOUND_MIN
        elif _BOUND_MAX_PREFIX_RE.search(prefix_ctx) or _BOUND_MAX_SUFFIX_RE.match(suffix_ctx):
            bound = BOUND_MAX
        elif _BOUND_APPROX_PREFIX_RE.search(prefix_ctx) or _BOUND_APPROX_SUFFIX_RE.match(suffix_ctx):
            bound = BOUND_APPROX
        # DAV-1158: 资金流/行情类数字的数据源/口径命名空间；读 period_view——
        # cleaned 已把字段代码（r0_net 等）保位掩码，口径标注须从未掩码坐标等价的
        # period_view 读取（与 _bind_period_for_number 同一坐标系约定）。
        provider = _bind_provider_for_number(period_view, n_start, n_end, metric)
        # DAV-1169: 余额/流量共用指标与价格类数字的时点/价格基准命名空间；
        # 同 provider 读取约定（period_view 坐标系）。
        timepoint = _bind_timepoint_for_number(period_view, n_start, n_end, metric)
        bn = BoundNumber(val, unit, raw, metric, num_period, raw_metric, stype, entity, role, basis, bound, None, provider, timepoint)
        res.append(bn)
        res_spans.append((n_start, n_end))
        last_res_match_end = n_end
    # DAV-1163: 「A-B」区间对合并——相邻两数字仅以连接符（- ~ – — 至 到）相隔，
    # 或负号被数字正则吞作符号位（「91%-92%」中 -92 的 - 实为连接符）时，两个
    # 端点合并为一个区间事实：任一端点不被单独要求命中，区间内落值即覆盖。
    for j in range(len(res) - 1):
        gap = cleaned[res_spans[j][1]:res_spans[j + 1][0]]
        is_range = bool(_RANGE_CONNECTOR_RE.fullmatch(gap))
        if not is_range and gap == "" and res[j + 1].raw.startswith("-"):
            # 「A-B」中 B 的负号是连接符而非符号位：取绝对值并标记区间
            is_range = True
            res[j + 1].val = abs(res[j + 1].val)
        if is_range:
            # DAV-1163 返修（DAV-1164 🟡-1）：区间端点保留符号——「-5%至-3%」
            # 归一为 (-5,-3) 而非 (3,5)；取 abs 仅限上方负号吞并分支（无显式
            # 连接符的「91%-92%」形态，该分支已在吞并时完成 abs）。
            lo = min(res[j].val, res[j + 1].val)
            hi = max(res[j].val, res[j + 1].val)
            res[j].range_span = (lo, hi)
            res[j + 1].range_span = (lo, hi)
            # 区间端点共享指标/语义绑定——「市值底线67-77元」中 77 因前一数字
            # 占先而失绑，区间两端同属「市值」；未绑定端点继承对端绑定。
            if res[j + 1].metric is None and res[j].metric is not None:
                res[j + 1].metric = res[j].metric
                res[j + 1].raw_metric = res[j].raw_metric
            if res[j].metric is None and res[j + 1].metric is not None:
                res[j].metric = res[j + 1].metric
                res[j].raw_metric = res[j + 1].raw_metric
            if res[j + 1].stype == STYPE_UNKNOWN and res[j].stype != STYPE_UNKNOWN:
                res[j + 1].stype = res[j].stype
            if res[j].stype == STYPE_UNKNOWN and res[j + 1].stype != STYPE_UNKNOWN:
                res[j].stype = res[j + 1].stype
    return res


_COMPOUND_SPLIT_RE = re.compile(
    r"[;；]|(?<!\d)[,，](?!\d)|(?<=[%\d元股点次倍])\s*(?:且|并且|但|但是|同时|严重背离|背离)\s*"
)

# DAV-1163: 出处引导语（「根据市场分析师报告，」「宏观报告显示」「基本面
# 报告：」）不是事实子句——拆分前剥除，否则引导语会被计为一个 unsupported
# 原子子句，阻断逐事实计分路径。
_EVIDENCE_LEADIN_RE = re.compile(
    r"^\s*(?:根据[^，。；、,;:：\d]{0,30}[，。；、,;:：]|"
    r"[^，。；、,;:：\d]{0,15}?(?:报告|分析|研报)[：:]|"
    r"[^，。；、,;]{0,15}?(?:报告|分析|研报)(?:显示|表明|指出|称|认为|提到)?[，。；、,;]?)"
)


def split_compound_evidence(text: str) -> list[str]:
    """Split a compound evidence sentence into atomic statements."""
    if not text:
        return []
    parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(text) if p.strip()]
    return parts if parts else [text.strip()]


def _decimal_places(raw: str) -> int:
    m = re.search(r"\.(\d+)", raw or "")
    return len(m.group(1)) if m else 0


def _is_rounding_equivalent(ev_bn: BoundNumber, l_bn: BoundNumber) -> bool:
    """DAV-1163: 舍入/精度等价——同一数值按较粗精度舍入后相等即视为同值
    （「3.70亿」vs「+3.7045亿」：3.7045 舍入到 2 位即 3.70，不得判失配）。"""
    d = min(_decimal_places(ev_bn.raw), _decimal_places(l_bn.raw))
    return round(ev_bn.val, d) == round(l_bn.val, d) or round(
        abs(ev_bn.val), d
    ) == round(abs(l_bn.val), d)


def _value_covered(
    ev_bn: BoundNumber,
    l_bn: BoundNumber,
    rel_tol: float = 0.02,
    abs_tol: float = 0.05,
) -> bool:
    """DAV-1163: 数值覆盖语义——区间/约数/方向界表达按声明语义覆盖而非点值相等。

    - ev 「超X/X余/X以上」（下界）：报告值 ≥ X（容差内）即覆盖；
    - ev 「不足X/X以下/X以内」（上界）：报告值 ≤ X 即覆盖；
    - 报告侧带方向界时对称处理；
    - 任一侧「约X/近X/X左右」：容差放宽至 _APPROX_REL_TOL；
    - 「A-B」区间对：两侧区间在容差内重叠即覆盖（区间内落值也算覆盖）；
    - 点值：原容差 + 舍入等价（精度差同值不判失配）。
    """
    unit_compatible = (
        (ev_bn.unit == l_bn.unit)
        or (ev_bn.unit == "raw" and l_bn.unit == "%")
        or (ev_bn.unit == "%" and l_bn.unit == "raw")
        # DAV-1163: 证据常省略「元」单位（「10EMA(91.14)」「现价86.20」），
        # 指标已绑定时允许 raw↔元 量纲互认；双侧均未绑定仍要求严格量纲，
        # 防止裸数字跨语义通配。
        or (
            (ev_bn.metric is not None or l_bn.metric is not None)
            and {ev_bn.unit, l_bn.unit} == {"raw", "元"}
        )
    )
    if not unit_compatible:
        return False
    e_lo, e_hi = ev_bn.range_span or (ev_bn.val, ev_bn.val)
    l_lo, l_hi = l_bn.range_span or (l_bn.val, l_bn.val)
    if ev_bn.bound == BOUND_MIN:
        return l_hi >= ev_bn.val * (1 - rel_tol) - abs_tol
    if ev_bn.bound == BOUND_MAX:
        return l_lo <= ev_bn.val * (1 + rel_tol) + abs_tol
    if l_bn.bound == BOUND_MIN:
        return e_hi >= l_bn.val * (1 - rel_tol) - abs_tol
    if l_bn.bound == BOUND_MAX:
        return e_lo <= l_bn.val * (1 + rel_tol) + abs_tol
    rt = rel_tol
    if ev_bn.bound == BOUND_APPROX or l_bn.bound == BOUND_APPROX:
        rt = max(rel_tol, _APPROX_REL_TOL)
    if ev_bn.range_span is None and l_bn.range_span is None:
        if _is_num_match(ev_bn.val, ev_bn.unit, l_bn.val, l_bn.unit, rt, abs_tol):
            return True
        return _is_rounding_equivalent(ev_bn, l_bn)
    if ev_bn.range_span is None:
        # 证据是点值、报告侧是区间：点值未在报告中字面出现（区间内取值是
        # 衍生值而非记录值），不得按区间包含放行——「低于现价5.6%」不得被
        # 报告「5%-8%回调」区间覆盖（golden CASE-001 红线）。
        return False
    # 证据区间 [e_lo,e_hi] 与报告值/区间在容差内重叠即覆盖：报告点值落在
    # 证据区间内（「底线67-77元」被「77元」覆盖）或两侧区间相交均算命中。
    margin = abs_tol
    return e_lo <= l_hi * (1 + rt) + margin and l_lo <= e_hi * (1 + rt) + margin


def _is_bound_num_match(
    ev_bn: BoundNumber,
    l_bn: BoundNumber,
    rel_tol: float = 0.02,
    abs_tol: float = 0.05,
) -> bool:
    """Whitelist match: 指标、单位（数值容差内）、语义类型、期间四者均兼容才放行。

    DAV-1088: 不再维持「只要不明确矛盾就通过」。绑定到严格指标的数字只能与
    同名严格指标匹配；任一侧严格、另一侧非严格/未绑定即拒绝。两侧均非严格时
    要求规范化指标同名或双方均未绑定；语义类型必须一致，否则不可比。
    """
    if not _value_covered(ev_bn, l_bn, rel_tol, abs_tol):
        return False
    ev_strict = ev_bn.metric if ev_bn.metric in _STRICT_METRICS else None
    l_strict = l_bn.metric if l_bn.metric in _STRICT_METRICS else None
    if ev_strict or l_strict:
        if not ev_strict or not l_strict or ev_strict != l_strict:
            return False
    elif ev_bn.metric != l_bn.metric:
        return False
    if ev_bn.stype != l_bn.stype:
        return False
    # 期间兼容：双方均抽出期间时，要求相同或共享同一年度前缀
    # （2026H1 与 2026 / 2026-07-06 属同一年度粒度，视为兼容；跨年不兼容）。
    # DAV-1147：同一规则兼任跨报告拼合的期间门——不同期间的同值不得强拼。
    if not _periods_join_compatible(ev_bn.period, l_bn.period):
        return False
    # DAV-1169: 时点/价格基准兼容——双侧均标注且不同时（存量 vs 流量、历史/
    # 成本 vs 现价）的同值不得互相佐证；单侧未标注不阻塞拼合（数值相等前提
    # 下的保守宽松，与 _periods_join_compatible 同一原则）。
    return _timepoints_join_compatible(ev_bn.timepoint, l_bn.timepoint)


def _bound_num_value_conflicts(
    ev_bn: BoundNumber,
    l_bn: BoundNumber,
) -> bool:
    """数值层面是否构成冲突（同名严格指标 + 同单位 + 同语义类型 + 同期间 + 数值发散）。

    不含主体维度——供冲突判定与 entity_scope_gap 记录共用。
    DAV-1163: 方向界/约数/区间表达不是点值声明，不参与点值冲突判定
    （「超100亿」与「130亿」是覆盖关系而非冲突）。
    """
    if (
        ev_bn.bound is not None
        or l_bn.bound is not None
        or ev_bn.range_span is not None
        or l_bn.range_span is not None
    ):
        return False
    ev_strict = ev_bn.metric if ev_bn.metric in _STRICT_METRICS else None
    l_strict = l_bn.metric if l_bn.metric in _STRICT_METRICS else None
    if not ev_strict or not l_strict or ev_strict != l_strict:
        return False
    if ev_bn.unit != l_bn.unit or ev_bn.unit not in {"%", "元", "股"}:
        return False
    # 语义类型不一致（占比 vs 同比增速、分项影响额 vs 总量）一律不可比，不得判冲突
    if ev_bn.stype != l_bn.stype:
        return False
    if ev_bn.unit == "%":
        if ev_bn.period != l_bn.period:
            return False
    else:
        if ev_bn.period and l_bn.period and ev_bn.period != l_bn.period:
            return False
    diff_pct = abs(abs(ev_bn.val) - abs(l_bn.val)) / (abs(l_bn.val) + 1e-9)
    return diff_pct > 0.05


def _is_bound_num_contradicted(
    ev_bn: BoundNumber,
    l_bn: BoundNumber,
) -> bool:
    """Check if evidence bound number contradicts line bound number.

    Requires matching metric name, unit, and period (for percentages) before judging conflict.
    DAV-1145: 另要求主体可比——同指标不同主体（跨公司/个股 vs 指数行业基准/
    主体歧义/单侧未指明主体）不得互判 contradicted。
    DAV-1146: 另要求语义角色/期间基准可比——actual vs threshold/scenario/
    incremental、单季 vs 年化/累计 不得互判 contradicted。
    """
    if not _entities_comparable(ev_bn.entity, l_bn.entity):
        return False
    if not _semantics_comparable(ev_bn, l_bn):
        return False
    # DAV-1158: 不同数据源/口径（东财 vs 同花顺、r0_net vs netamount）不得互判
    if not _providers_comparable(ev_bn, l_bn):
        return False
    # DAV-1169: 不同时点/价格基准（存量时点 vs 单日流量、历史高点/成本均价 vs
    # 现价）不得互判
    if not _timepoints_comparable(ev_bn, l_bn):
        return False
    return _bound_num_value_conflicts(ev_bn, l_bn)


class EvidenceFactualTruthEvaluator:
    """Evaluates evidence statements against 7 analyst reports and market context."""

    def __init__(self, relative_tolerance: float = 0.02, absolute_tolerance: float = 0.05):
        self.rel_tol = relative_tolerance
        self.abs_tol = absolute_tolerance

    def _extract_unavailable_sources(
        self,
        market_data_context: Mapping[str, Any] | None,
        social_data_context: Mapping[str, Any] | None = None,
    ) -> set[str]:
        unavailable = set()
        if isinstance(market_data_context, Mapping):
            # Check data_failure_ledger
            ledger = market_data_context.get("data_failure_ledger")
            if isinstance(ledger, list):
                for entry in ledger:
                    if isinstance(entry, dict):
                        src = str(entry.get("source", "")).strip().lower()
                        status = str(entry.get("status", "")).strip().lower()
                        prov_status = str(entry.get("provenance_status", "")).strip().lower()
                        if src and (not status or status in UNAVAILABLE_STATUSES or prov_status in {"unverified", "refused", "future"}):
                            unavailable.add(src)
                            # Add alias name if available
                            name = str(entry.get("name", "")).strip().lower()
                            if name:
                                unavailable.add(name)

            # Check source_provenance
            prov = market_data_context.get("source_provenance")
            if isinstance(prov, dict):
                for src, info in prov.items():
                    if isinstance(info, dict):
                        status = str(info.get("status", "")).strip().lower()
                        prov_status = str(info.get("provenance_status", "")).strip().lower()
                        if status in UNAVAILABLE_STATUSES or prov_status in {"unverified", "refused", "future"}:
                            unavailable.add(src.strip().lower())

            # Check data_gaps
            gaps = market_data_context.get("data_gaps")
            if isinstance(gaps, list):
                for g in gaps:
                    if isinstance(g, str):
                        unavailable.add(g.strip().lower())

        if isinstance(social_data_context, Mapping):
            social_ledger = social_data_context.get("data_failure_ledger")
            if isinstance(social_ledger, list):
                for entry in social_ledger:
                    if isinstance(entry, dict):
                        src = str(entry.get("source", "")).strip().lower()
                        status = str(entry.get("status", "")).strip().lower()
                        prov_status = str(entry.get("provenance_status", "")).strip().lower()
                        if src and (not status or status in UNAVAILABLE_STATUSES or prov_status in {"unverified", "refused", "future"}):
                            unavailable.add(src)
                            name = str(entry.get("name", "")).strip().lower()
                            if name:
                                unavailable.add(name)

            social_prov = social_data_context.get("source_provenance")
            if isinstance(social_prov, dict):
                for src, info in social_prov.items():
                    if isinstance(info, dict):
                        status = str(info.get("status", "")).strip().lower()
                        prov_status = str(info.get("provenance_status", "")).strip().lower()
                        if status in UNAVAILABLE_STATUSES or prov_status in {"unverified", "refused", "future"}:
                            unavailable.add(src.strip().lower())

            social_status = str(social_data_context.get("status", "")).strip().lower()
            if social_status in UNAVAILABLE_STATUSES:
                unavailable.add("social_archive")
                unavailable.add("social_data")
                unavailable.add("social")

        return unavailable

    def _check_source_unavailable(
        self, raw_evidence: str, unavailable_sources: set[str]
    ) -> tuple[bool, str]:
        if not unavailable_sources or not raw_evidence:
            return False, ""
        evidence_lower = raw_evidence.lower()
        sorted_sources = sorted(unavailable_sources, key=len, reverse=True)
        for src in sorted_sources:
            if src and (src in evidence_lower or evidence_lower in src):
                return True, src
        return False, ""

    def _is_report_unavailable(
        self, role_key: str, unavailable_sources: set[str]
    ) -> bool:
        """Check if a report's underlying provenance sources are marked unavailable/unverified."""
        if not unavailable_sources:
            return False
        mapped_sources = (
            REPORT_TO_PROVENANCE_SOURCES.get(role_key)
            or REPORT_TO_PROVENANCE_SOURCES.get(f"{role_key}_report", ())
        )
        return any(src in unavailable_sources for src in mapped_sources)

    def _check_anti_lookahead(
        self, raw_evidence: str, baseline_date_obj: date | None
    ) -> tuple[bool, str]:
        if baseline_date_obj is None or not raw_evidence:
            return True, ""
        ev_date = _parse_date(raw_evidence)
        if ev_date and ev_date > baseline_date_obj:
            # If the text does not indicate forward prediction/target, it violates lookahead
            if not any(w in raw_evidence for w in ("预测", "预期", "目标", "展望", "情景", "未来")):
                return False, f"日期 {ev_date.isoformat()} 晚于基准分析日期 {baseline_date_obj.isoformat()}，存在前视偏差"
        return True, ""

    def evaluate_single_evidence(
        self,
        raw_evidence: str,
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None = None,
        analysis_baseline_date: str | None = None,
        claim_id: str | None = None,
        social_data_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate a single evidence string for truthfulness, lookahead, and fatal hallucination."""
        raw_text = str(raw_evidence or "").strip()
        if not raw_text:
            return {
                "raw": raw_evidence,
                "claim_id": claim_id,
                "matched_role": None,
                "matched_source": None,
                "status": STATUS_UNSUPPORTED,
                "is_fatal": False,
                "details": "证据文本为空",
            }

        # 1. Check for unavailable data sources (Fatal Hallucination)
        unavailable_sources = self._extract_unavailable_sources(market_data_context, social_data_context)
        is_unavail, failed_src = self._check_source_unavailable(raw_text, unavailable_sources)
        if is_unavail:
            return {
                "raw": raw_text,
                "claim_id": claim_id,
                "matched_role": None,
                "matched_source": failed_src,
                "status": STATUS_SOURCE_UNAVAILABLE,
                "is_fatal": True,
                "details": f"引用了失败账本中不可用或缺失的数据源指标: {failed_src}，属于严重幻觉",
            }

        # 1.1 Check if evidence asserts a directional social sentiment score when direction is disallowed or social is insufficient/empty
        if isinstance(social_data_context, Mapping):
            social_status = str(social_data_context.get("status", "")).strip().lower()
            social_mode = str(social_data_context.get("mode", "")).strip().lower()
            dir_allowed = bool(social_data_context.get("direction_allowed", False))
            bundle = social_data_context.get("bundle") if isinstance(social_data_context.get("bundle"), dict) else {}
            sent_label = bundle.get("social_sentiment", {}).get("label") if isinstance(bundle.get("social_sentiment"), dict) else None

            if (
                not dir_allowed
                or social_mode in ("disabled", "shadow")
                or social_status in ("insufficient", "empty", "not_applicable", "failed", "timeout", "refused")
                or sent_label == "insufficient"
            ):
                raw_lower = raw_text.lower()
                is_social_mention = any(kw in raw_lower for kw in ("社交", "舆情", "散户", "小红书", "抖音", "social"))
                has_score_or_dir = any(kw in raw_lower for kw in ("得分", "分数", "score", "看多", "看空", "多头", "空头", "0.", "极度", "狂热", "高涨", "情绪分"))
                if is_social_mention and has_score_or_dir:
                    return {
                        "raw": raw_text,
                        "claim_id": claim_id,
                        "matched_role": None,
                        "matched_source": None,
                        "status": STATUS_UNSUPPORTED,
                        "is_fatal": False,
                        "details": "社交数据处于不可用/不足态或 direction_allowed=false，禁止将社交情绪分数或多空方向作为已验证事实",
                    }

        # 2. Check Anti-lookahead Date
        baseline_date_obj = None
        if analysis_baseline_date:
            baseline_date_obj = _parse_date(analysis_baseline_date)
        elif isinstance(market_data_context, Mapping):
            b_str = market_data_context.get("analysis_baseline_date") or market_data_context.get("data_as_of") or market_data_context.get("trade_date")
            if b_str:
                baseline_date_obj = _parse_date(str(b_str))

        date_ok, date_reason = self._check_anti_lookahead(raw_text, baseline_date_obj)
        if not date_ok:
            return {
                "raw": raw_text,
                "claim_id": claim_id,
                "matched_role": None,
                "matched_source": None,
                "status": STATUS_CONTRADICTED,
                "is_fatal": False,
                "details": date_reason,
            }

        # 3. Deterministic Matching against 7 Reports
        ev_keywords = _extract_metric_keywords(raw_text)

        # 3.1 Exact substring match in any report
        for role_key in SEVEN_REPORT_KEYS:
            if self._is_report_unavailable(role_key, unavailable_sources):
                continue
            report_body = str(seven_reports.get(role_key, "") or "")
            if not report_body.strip():
                continue
            if raw_text in report_body or any(len(p) >= 4 and p in report_body for p in raw_text.split("，")):
                return {
                    "raw": raw_text,
                    "claim_id": claim_id,
                    "matched_role": role_key,
                    "matched_source": role_key.replace("_report", ""),
                    "status": STATUS_VERIFIED,
                    "is_fatal": False,
                    "details": f"在 {role_key} 中找到精确匹配事实",
                }

        parent_period = normalize_period(raw_text)
        atomic_clauses = split_compound_evidence(raw_text)

        # 3.1b DAV-1147: 原子子句跨报告逐字聚合——复合证据的多个事实分处不同
        # 报告时，每个 ≥4 字的原子子句只要在任一可用报告中逐字命中（容忍报告
        # 换行/空白差异），整条证据即可聚合成立。聚合的是事实存在性，不拼数字
        # 推因果；期间与主体一致性由子句文本自身承载（逐字命中即同期同主体），
        # 数值拼合的期间/主体门在 3.3 跨报告路径另行强制。
        substantive_clauses = [c for c in atomic_clauses if len(c) >= 4]
        if len(substantive_clauses) >= 2:
            clause_hit_roles: dict[str, set[str]] = {}
            all_clauses_hit = True
            for clause in substantive_clauses:
                clause_norm = _WHITESPACE_RE.sub("", clause)
                for role_key in SEVEN_REPORT_KEYS:
                    if self._is_report_unavailable(role_key, unavailable_sources):
                        continue
                    report_body = str(seven_reports.get(role_key, "") or "")
                    if not report_body.strip():
                        continue
                    if clause in report_body or (
                        clause_norm
                        and clause_norm in _WHITESPACE_RE.sub("", report_body)
                    ):
                        clause_hit_roles.setdefault(clause, set()).add(role_key)
                if clause not in clause_hit_roles:
                    all_clauses_hit = False
                    break
            if all_clauses_hit:
                matched_roles = sorted(
                    {r for roles in clause_hit_roles.values() for r in roles},
                    key=SEVEN_REPORT_KEYS.index,
                )
                return {
                    "raw": raw_text,
                    "claim_id": claim_id,
                    "matched_role": ",".join(matched_roles) if len(matched_roles) > 1 else matched_roles[0],
                    "matched_source": ",".join(r.replace("_report", "") for r in matched_roles),
                    "status": STATUS_VERIFIED,
                    "is_fatal": False,
                    "details": f"原子子句跨报告逐字聚合验证 (verbatim_atomic_aggregation): {','.join(matched_roles)}",
                }

        all_ev_bns: list[BoundNumber] = []
        for clause in atomic_clauses:
            clause_period = normalize_period(clause) or parent_period
            c_bns = extract_bound_numbers(clause, default_period=clause_period)
            all_ev_bns.extend(c_bns)

        # 3.2 Single-line full match in reports
        for role_key in SEVEN_REPORT_KEYS:
            if self._is_report_unavailable(role_key, unavailable_sources):
                continue
            report_body = str(seven_reports.get(role_key, "") or "")
            if not report_body.strip():
                continue

            for line in report_body.splitlines():
                line_text = line.strip()
                if not line_text:
                    continue

                line_keywords = _extract_metric_keywords(line_text)
                ev_canon = {_METRIC_CANONICAL_MAP.get(k, k) for k in ev_keywords}
                line_canon = {_METRIC_CANONICAL_MAP.get(k, k) for k in line_keywords}
                common_kw = ev_canon.intersection(line_canon)
                line_bns = extract_bound_numbers(line_text)

                if not line_bns and not all_ev_bns:
                    # Pure qualitative text match if keywords strongly match
                    if len(common_kw) >= 2 and any(kw in line_text for kw in ev_keywords):
                        return {
                            "raw": raw_text,
                            "claim_id": claim_id,
                            "matched_role": role_key,
                            "matched_source": role_key.replace("_report", ""),
                            "status": STATUS_VERIFIED,
                            "is_fatal": False,
                            "details": f"在 {role_key} 中找到定性指标匹配: {', '.join(common_kw)}",
                        }
                    continue

                # If numbers exist, check single-line full value match with metric binding
                if all_ev_bns:
                    matched_all_numbers = True
                    found_match = False
                    for ev_bn in all_ev_bns:
                        num_found_in_line = False
                        for l_bn in line_bns:
                            if _is_bound_num_match(ev_bn, l_bn, self.rel_tol, self.abs_tol):
                                num_found_in_line = True
                                found_match = True
                                break
                        if not num_found_in_line:
                            matched_all_numbers = False
                            break

                    if found_match and matched_all_numbers:
                        if not ev_keywords or len(common_kw) >= 1:
                            return {
                                "raw": raw_text,
                                "claim_id": claim_id,
                                "matched_role": role_key,
                                "matched_source": role_key.replace("_report", ""),
                                "status": STATUS_VERIFIED,
                                "is_fatal": False,
                                "details": f"在 {role_key} 中验证数值与关键词匹配",
                            }

        # 3.3 Multi-line / atomic aggregation mode (when single line did not match all numbers)
        if all_ev_bns:
            num_hits_by_report: dict[str, set[int]] = {}
            all_hit_num_indices: set[int] = set()
            # DAV-1147: 主体一致的命中（供跨报告拼合，防止跨主体同值强拼）
            cross_hits_by_report: dict[str, set[int]] = {}
            all_cross_hit_num_indices: set[int] = set()

            for num_idx, ev_bn in enumerate(all_ev_bns):
                for role_key in SEVEN_REPORT_KEYS:
                    if self._is_report_unavailable(role_key, unavailable_sources):
                        continue
                    report_body = str(seven_reports.get(role_key, "") or "")
                    if not report_body.strip():
                        continue
                    matched_in_report = False
                    for line in report_body.splitlines():
                        line_text = line.strip()
                        if not line_text:
                            continue
                        line_keywords = _extract_metric_keywords(line_text)
                        ev_canon = {_METRIC_CANONICAL_MAP.get(k, k) for k in ev_keywords}
                        line_canon = {_METRIC_CANONICAL_MAP.get(k, k) for k in line_keywords}
                        common_kw = ev_canon.intersection(line_canon)
                        # Each hit line must share >= 1 canonical keyword with the evidence sentence
                        if ev_keywords and not common_kw:
                            continue
                        line_bns = extract_bound_numbers(line_text)
                        for l_bn in line_bns:
                            if _is_bound_num_match(ev_bn, l_bn, self.rel_tol, self.abs_tol):
                                num_hits_by_report.setdefault(role_key, set()).add(num_idx)
                                all_hit_num_indices.add(num_idx)
                                if _entities_consistent_for_join(ev_bn.entity, l_bn.entity):
                                    cross_hits_by_report.setdefault(role_key, set()).add(num_idx)
                                    all_cross_hit_num_indices.add(num_idx)
                                matched_in_report = True
                                break
                        if matched_in_report:
                            break

            total_nums = len(all_ev_bns)
            # Check single-report multi-line aggregation first
            for role_key in SEVEN_REPORT_KEYS:
                if self._is_report_unavailable(role_key, unavailable_sources):
                    continue
                hit_set = num_hits_by_report.get(role_key, set())
                if len(hit_set) == total_nums:
                    return {
                        "raw": raw_text,
                        "claim_id": claim_id,
                        "matched_role": role_key,
                        "matched_source": role_key.replace("_report", ""),
                        "status": STATUS_VERIFIED,
                        "is_fatal": False,
                        "details": f"在 {role_key} 中通过多行聚合验证数值与关键词匹配 (multi_line_match)",
                    }

            # Check cross-report multi-line aggregation
            # DAV-1147: 跨报告拼合加主体一致性门——不同主体/主体歧义的同值不得
            # 强拼（期间兼容性已在 _is_bound_num_match 内强制）。
            if len(all_cross_hit_num_indices) == total_nums:
                matched_roles = [
                    r for r in SEVEN_REPORT_KEYS
                    if r in cross_hits_by_report and cross_hits_by_report[r]
                    and not self._is_report_unavailable(r, unavailable_sources)
                ]
                if matched_roles:
                    return {
                        "raw": raw_text,
                        "claim_id": claim_id,
                        "matched_role": ",".join(matched_roles) if len(matched_roles) > 1 else matched_roles[0],
                        "matched_source": ",".join(r.replace("_report", "") for r in matched_roles),
                        "status": STATUS_VERIFIED,
                        "is_fatal": False,
                        "details": f"在 {','.join(matched_roles)} 中跨报告多行聚合验证数值与关键词匹配 (multi_line_match)",
                    }

        # 3.4 Contradiction check across reports when evidence is not verified
        contradicted_candidate = None
        # DAV-1145: 数值层面构成冲突、仅因主体不同/歧义/单侧未指明而被跳过的比较 → 记 gap
        entity_scope_gaps: list[str] = []
        # DAV-1146: 数值层面构成冲突、仅因语义角色/期间基准不同而被跳过的比较 → 记 gap
        semantic_role_gaps: list[str] = []
        # DAV-1158: 数值层面构成冲突、仅因数据源/口径不同或单侧未标注而被跳过的比较 → 记 gap
        provider_scope_gaps: list[str] = []
        # DAV-1169: 数值层面构成冲突、仅因时点/价格基准不同或单侧未标注而被跳过的比较 → 记 gap
        timepoint_scope_gaps: list[str] = []
        if all_ev_bns:
            for role_key in SEVEN_REPORT_KEYS:
                if self._is_report_unavailable(role_key, unavailable_sources):
                    continue
                report_body = str(seven_reports.get(role_key, "") or "")
                if not report_body.strip():
                    continue
                for line in report_body.splitlines():
                    line_text = line.strip()
                    if not line_text:
                        continue
                    line_bns = extract_bound_numbers(line_text)
                    for num_idx, ev_bn in enumerate(all_ev_bns):
                        if num_idx in all_hit_num_indices:
                            continue
                        if not ev_bn.metric:
                            continue
                        for l_bn in line_bns:
                            if not l_bn.metric:
                                continue
                            if _is_bound_num_contradicted(ev_bn, l_bn):
                                contradicted_candidate = (
                                    role_key,
                                    f"在 {role_key} 中指标 '{ev_bn.metric}' 数据冲突: 证据声称 {ev_bn.raw}，报告记录为 {l_bn.raw}",
                                )
                                break
                            if _bound_num_value_conflicts(ev_bn, l_bn):
                                if not _entities_comparable(ev_bn.entity, l_bn.entity):
                                    gap_note = (
                                        f"跨主体比较已跳过(entity_scope): 证据 {ev_bn.raw}"
                                        f"(主体={ev_bn.entity}) vs {role_key} 记录 {l_bn.raw}"
                                        f"(主体={l_bn.entity})"
                                    )
                                    if gap_note not in entity_scope_gaps:
                                        entity_scope_gaps.append(gap_note)
                                elif not _semantics_comparable(ev_bn, l_bn):
                                    gap_note = (
                                        f"跨语义角色/期间基准比较已跳过(semantic_role): 证据 {ev_bn.raw}"
                                        f"(role={ev_bn.role},basis={ev_bn.basis}) vs {role_key} 记录 {l_bn.raw}"
                                        f"(role={l_bn.role},basis={l_bn.basis})"
                                    )
                                    if gap_note not in semantic_role_gaps:
                                        semantic_role_gaps.append(gap_note)
                                elif not _providers_comparable(ev_bn, l_bn):
                                    gap_note = (
                                        f"跨数据源/口径比较已跳过(provider_scope): 证据 {ev_bn.raw}"
                                        f"(provider={ev_bn.provider}) vs {role_key} 记录 {l_bn.raw}"
                                        f"(provider={l_bn.provider})"
                                    )
                                    if gap_note not in provider_scope_gaps:
                                        provider_scope_gaps.append(gap_note)
                                else:
                                    # 主体/语义/provider 均可比而 _is_bound_num_contradicted
                                    # 已否，仅剩时点/价格基准差异为跳过原因
                                    gap_note = (
                                        f"跨时点/价格基准比较已跳过(timepoint_scope): 证据 {ev_bn.raw}"
                                        f"(timepoint={ev_bn.timepoint}) vs {role_key} 记录 {l_bn.raw}"
                                        f"(timepoint={l_bn.timepoint})"
                                    )
                                    if gap_note not in timepoint_scope_gaps:
                                        timepoint_scope_gaps.append(gap_note)
                        if contradicted_candidate:
                            break
                    if contradicted_candidate:
                        break
                if contradicted_candidate:
                    break

        if contradicted_candidate:
            res = {
                "raw": raw_text,
                "claim_id": claim_id,
                "matched_role": contradicted_candidate[0],
                "matched_source": contradicted_candidate[0].replace("_report", ""),
                "status": STATUS_CONTRADICTED,
                "is_fatal": False,
                "details": contradicted_candidate[1],
            }
            if entity_scope_gaps:
                res["entity_scope_gaps"] = entity_scope_gaps
            if semantic_role_gaps:
                res["semantic_role_gaps"] = semantic_role_gaps
            if provider_scope_gaps:
                res["provider_scope_gaps"] = provider_scope_gaps
            if timepoint_scope_gaps:
                res["timepoint_scope_gaps"] = timepoint_scope_gaps
            return res

        # 4. Check market_data_context if provided
        if isinstance(market_data_context, Mapping):
            # Check quotes, indicators, fund_flow_evidence (excluding metadata and unavailable sources)
            for k, v in market_data_context.items():
                if k in {
                    "source_provenance",
                    "data_failure_ledger",
                    "data_gaps",
                    "analysis_baseline_date",
                    "trade_date",
                    "data_as_of",
                }:
                    continue
                if str(k).strip().lower() in unavailable_sources:
                    continue
                v_str = str(v or "")
                if raw_text in v_str:
                    return {
                        "raw": raw_text,
                        "claim_id": claim_id,
                        "matched_role": "market_data_context",
                        "matched_source": str(k),
                        "status": STATUS_VERIFIED,
                        "is_fatal": False,
                        "details": f"在 market_data_context[{k}] 中找到匹配数据",
                    }

        # 5. Unsupported
        res = {
            "raw": raw_text,
            "claim_id": claim_id,
            "matched_role": None,
            "matched_source": None,
            "status": STATUS_UNSUPPORTED,
            "is_fatal": False,
            "details": "未在七份分析师报告或市场数据上下文中找到该事实或数据支撑",
        }
        # DAV-1163: 数字级事实覆盖清单——复合句中已命中的数字事实与未命中
        # 事实分列，供 _verify_evidence_or_decompose 做逐事实独立计分（单点
        # 失配不拖垮整条，部分覆盖经原子计分落 partial）。
        if all_ev_bns:
            res["fact_coverage"] = {
                "total": len(all_ev_bns),
                "verified_facts": [
                    all_ev_bns[i].raw for i in sorted(all_hit_num_indices)
                ],
                "unverified_facts": [
                    all_ev_bns[i].raw
                    for i in range(len(all_ev_bns))
                    if i not in all_hit_num_indices
                ],
            }
        if entity_scope_gaps:
            res["entity_scope_gaps"] = entity_scope_gaps
        if semantic_role_gaps:
            res["semantic_role_gaps"] = semantic_role_gaps
        if provider_scope_gaps:
            res["provider_scope_gaps"] = provider_scope_gaps
        return res

    def _verify_evidence_or_decompose(
        self,
        ev_str: str,
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None,
        analysis_baseline_date: str | None,
        claim_id: str | None,
        social_data_context: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """DAV-1163: 复合句逐事实独立计分——整条证据核验失败（unsupported）且可拆
        出 ≥2 个实义原子子句时，逐子句独立重评：任一子句获验即按原子粒度计入
        verified，单点失配不再拖垮整条（部分覆盖经原子计分自然落 partial）。

        verified/contradicted/source_unavailable/致命结果语义不变，不拆分；全部
        子句均未获验时回退整条单点结论，不膨胀 coverage 分母。拆分后各子项带
        parent_evidence/atomic_index 溯源字段。
        """
        res = self.evaluate_single_evidence(
            raw_evidence=ev_str,
            seven_reports=seven_reports,
            market_data_context=market_data_context,
            analysis_baseline_date=analysis_baseline_date,
            claim_id=claim_id,
            social_data_context=social_data_context,
        )
        if res.get("status") != STATUS_UNSUPPORTED:
            return [res]
        ev_core = _EVIDENCE_LEADIN_RE.sub("", ev_str, count=1)
        substantive = [
            c for c in split_compound_evidence(ev_core) if len(c.strip()) >= 4
        ]
        if len(substantive) >= 2:
            sub_results = [
                self.evaluate_single_evidence(
                    raw_evidence=clause,
                    seven_reports=seven_reports,
                    market_data_context=market_data_context,
                    analysis_baseline_date=analysis_baseline_date,
                    claim_id=claim_id,
                    social_data_context=social_data_context,
                )
                for clause in substantive
            ]
            if any(s.get("status") == STATUS_VERIFIED for s in sub_results):
                items: list[dict[str, Any]] = []
                for idx, sub in enumerate(sub_results):
                    for it in self._facts_or_self(sub, substantive[idx], ev_str):
                        it["atomic_index"] = idx
                        # DAV-1164 🟢-2：溯源三件套齐备——非拆分项也补 clause
                        it.setdefault("clause", substantive[idx])
                        items.append(it)
                return items
            return [res]
        # 单子句多数字：整句未获验但部分数字事实已命中 → 逐事实独立计分
        items = self._facts_or_self(res, ev_str, ev_str)
        for it in items:
            it.setdefault("clause", ev_str)
        return items

    @staticmethod
    def _facts_or_self(
        res: dict[str, Any], clause: str, parent: str
    ) -> list[dict[str, Any]]:
        """把「多数字、部分命中」的子句拆成逐事实计分项。

        fact_coverage 中 verified_facts 是已在报告中字面命中的数字事实
        （绑定指标/语义/期间门均通过），独立计为 verified；未命中事实仍计
        unsupported——不放宽 verified 标准，只是不让单点失配拖垮整句。
        全部命中（应已 verified）或全部未命中时保持原子句单点结论。
        """
        fc = res.get("fact_coverage") or {}
        verified_facts = fc.get("verified_facts") or []
        unverified_facts = fc.get("unverified_facts") or []
        if not verified_facts or not unverified_facts:
            res["parent_evidence"] = parent
            return [res]
        items = []
        for fact in verified_facts:
            items.append(
                {
                    "raw": fact,
                    "claim_id": res.get("claim_id"),
                    "matched_role": res.get("matched_role"),
                    "matched_source": res.get("matched_source"),
                    "status": STATUS_VERIFIED,
                    "is_fatal": False,
                    "details": f"复合句原子事实已在报告中命中 (atomic_fact): {fact}",
                    "parent_evidence": parent,
                    "clause": clause,
                }
            )
        for fact in unverified_facts:
            items.append(
                {
                    "raw": fact,
                    "claim_id": res.get("claim_id"),
                    "matched_role": None,
                    "matched_source": None,
                    "status": STATUS_UNSUPPORTED,
                    "is_fatal": False,
                    "details": f"复合句原子事实未在报告中找到支撑 (atomic_fact): {fact}",
                    "parent_evidence": parent,
                    "clause": clause,
                }
            )
        return items

    def evaluate_claims(
        self,
        claims: Sequence[Mapping[str, Any]],
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None = None,
        analysis_baseline_date: str | None = None,
        social_data_context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate all evidence items across tracked debate claims."""
        results: list[dict[str, Any]] = []
        for claim in claims:
            cid = str(claim.get("claim_id", "")).strip() or None
            evidence_list = claim.get("evidence") or []
            if isinstance(evidence_list, str):
                evidence_list = [evidence_list]

            for ev in evidence_list:
                ev_str = str(ev).strip()
                if not ev_str:
                    continue
                results.extend(
                    self._verify_evidence_or_decompose(
                        ev_str,
                        seven_reports,
                        market_data_context,
                        analysis_baseline_date,
                        cid,
                        social_data_context,
                    )
                )

        return results

    def evaluate_challenges(
        self,
        challenges: Sequence[Mapping[str, Any]],
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None = None,
        analysis_baseline_date: str | None = None,
        social_data_context: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate all evidence items across challenges and compute challenge-level evidence status."""
        results: list[dict[str, Any]] = []
        for ch in challenges:
            chid = str(ch.get("challenge_id", "")).strip() or None
            target_id = str(ch.get("target_claim_id", "")).strip()
            speaker = str(ch.get("speaker") or ch.get("speaker_key") or "")
            speaker_key = str(ch.get("speaker_key") or ch.get("speaker") or "")
            severity = str(ch.get("severity", "major")).strip().lower()
            ev_list = ch.get("evidence") or []
            if isinstance(ev_list, str):
                ev_list = [ev_list]

            ver_items: list[dict[str, Any]] = []
            for ev in ev_list:
                ev_str = str(ev).strip()
                if not ev_str:
                    continue
                ver_items.extend(
                    self._verify_evidence_or_decompose(
                        ev_str,
                        seven_reports,
                        market_data_context,
                        analysis_baseline_date,
                        chid,
                        social_data_context,
                    )
                )

            verified_items = [v for v in ver_items if v.get("status") == STATUS_VERIFIED]
            contradicted_items = [v for v in ver_items if v.get("status") == STATUS_CONTRADICTED]
            unavail_items = [
                v for v in ver_items
                if (v.get("status") == STATUS_SOURCE_UNAVAILABLE and v.get("is_fatal") is not False)
                or v.get("is_fatal") is True
            ]
            unsupported_items = [v for v in ver_items if v.get("status") == STATUS_UNSUPPORTED]

            total_count = len(ver_items)
            verified_count = len(verified_items)
            contradicted_count = len(contradicted_items)
            unavail_count = len(unavail_items)
            unsupported_count = len(unsupported_items)

            if contradicted_count > 0 or unavail_count > 0:
                evidence_status = "contradicted"
            elif verified_count == total_count and total_count > 0:
                evidence_status = "verified"
            else:
                evidence_status = "unsupported"

            ch_res = {
                "challenge_id": chid,
                "target_claim_id": target_id,
                "speaker": speaker,
                "speaker_key": speaker_key,
                "severity": severity,
                "evidence_status": evidence_status,
                "counts": {
                    "total": total_count,
                    "verified": verified_count,
                    "unsupported": unsupported_count,
                    "contradicted": contradicted_count,
                    "source_unavailable": unavail_count,
                },
                "verification_items": ver_items,
            }
            results.append(ch_res)

        return results

    def aggregate_claim_evidence(
        self,
        claims: Sequence[Mapping[str, Any]] | None = None,
        claims_verification: Sequence[Mapping[str, Any]] | None = None,
        *,
        analysis_baseline_date: str | None = None,
        expected_symbol: str | None = None,
        market_data_context: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Aggregate evidence verification by claim and compute coverage ratio and adoption decisions."""
        return aggregate_claim_evidence(
            claims=claims,
            claims_verification=claims_verification,
            analysis_baseline_date=analysis_baseline_date,
            expected_symbol=expected_symbol,
            market_data_context=market_data_context,
        )


_OBSERVATION_HYPOTHESIS_PATTERN = re.compile(
    r"(?:^|[\[\(【（])\s*(?:观察|假设|假说|推测|情景假设|hypothesis|observation)\s*(?:[\]\)】）]|$|:|\s)",
    re.IGNORECASE,
)
_OBSERVATION_HYPOTHESIS_TYPES = frozenset(
    {
        "observation",
        "hypothesis",
        "hypothetical",
        "assumption",
        "observational",
        "conjecture",
        "scenario",
    }
)


def is_observation_or_hypothesis_claim(claim: Mapping[str, Any] | Any) -> bool:
    """Check if a claim is an observation or hypothesis rather than a verified fact assertion (E-03c)."""
    if not isinstance(claim, Mapping):
        if isinstance(claim, str):
            return bool(_OBSERVATION_HYPOTHESIS_PATTERN.search(claim))
        return False
    if bool(claim.get("is_observation")) or bool(claim.get("is_hypothesis")):
        return True
    for k in ("claim_type", "type", "epistemic_status", "claim_nature", "nature"):
        val = str(claim.get(k) or "").strip().lower()
        if val in _OBSERVATION_HYPOTHESIS_TYPES:
            return True
    claim_text = str(claim.get("claim") or "").strip()
    if _OBSERVATION_HYPOTHESIS_PATTERN.search(claim_text):
        return True
    if claim_text.startswith(("观察：", "假设：", "推测：", "情景：", "Observation:", "Hypothesis:")):
        return True
    status_val = str(claim.get("status") or "").strip().lower()
    if status_val in {"observation", "hypothesis"}:
        return True
    return False


def _check_invalidation_condition_triggered(
    cond: Mapping[str, Any],
    market_data_context: Mapping[str, Any],
) -> tuple[bool, str]:
    """Check whether a machine-checkable invalidation condition has triggered (E-03c)."""
    metric = str(cond.get("metric") or "").strip().lower()
    op = str(cond.get("operator") or "").strip()
    threshold = cond.get("threshold")
    if threshold is None or not op:
        return False, ""
    try:
        th_val = float(threshold)
    except (ValueError, TypeError):
        return False, ""

    actual_val = None
    for k in (metric, metric.replace(" ", "_"), f"current_{metric}", f"last_{metric}"):
        if k in market_data_context:
            v = market_data_context[k]
            if v is not None and not isinstance(v, bool):
                try:
                    actual_val = float(v)
                    break
                except (ValueError, TypeError):
                    pass
    if actual_val is None:
        for sub_key in ("indicators", "quotes", "daily", "realtime"):
            sub = market_data_context.get(sub_key)
            if isinstance(sub, Mapping) and metric in sub:
                v = sub[metric]
                if v is not None and not isinstance(v, bool):
                    try:
                        actual_val = float(v)
                        break
                    except (ValueError, TypeError):
                        pass

    if actual_val is None:
        return False, ""

    triggered = False
    if op == "<":
        triggered = (actual_val < th_val)
    elif op == "<=":
        triggered = (actual_val <= th_val)
    elif op == ">":
        triggered = (actual_val > th_val)
    elif op == ">=":
        triggered = (actual_val >= th_val)
    elif op == "==":
        triggered = math.isclose(actual_val, th_val, abs_tol=1e-5)
    elif op == "!=":
        triggered = not math.isclose(actual_val, th_val, abs_tol=1e-5)

    if triggered:
        cid = cond.get("condition_id", "cond")
        return True, f"失效条件 [{cid}] 已触发: 实际指标 {metric}={actual_val} 满足 {op} 阈值 {th_val}"
    return False, ""


def aggregate_claim_evidence(
    claims: Sequence[Mapping[str, Any]] | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
    *,
    analysis_baseline_date: str | None = None,
    expected_symbol: str | None = None,
    market_data_context: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate evidence verification results by claim_id and evaluate deterministic decisions.

    Returns:
        A dict mapping claim_id -> {
            "claim_id": str,
            "speaker": str,
            "speaker_key": str,
            "stance": str,
            "claim": str,
            "counts": {
                "total": int,
                "verified": int,
                "unsupported": int,
                "contradicted": int,
                "source_unavailable": int,
            },
            "coverage": float,
            "decision": "adopt" | "partial" | "reject",
            "reason": str,
            "verified_evidence": list[str],
            "unsupported_evidence": list[str],
            "contradicted_evidence": list[str],
            "source_unavailable_evidence": list[str],
            "excluded_evidence": list[str],
            "is_observation_or_hypothesis": bool,
            "applicability": dict | None,
            "invalidation_conditions": list[dict],
            "pit_failed": bool,
        }
    """
    claims_list = list(claims or [])
    ver_list = list(claims_verification or [])

    effective_baseline_date = analysis_baseline_date
    if not effective_baseline_date and isinstance(market_data_context, Mapping):
        effective_baseline_date = str(
            market_data_context.get("analysis_baseline_date")
            or market_data_context.get("data_as_of")
            or market_data_context.get("trade_date")
            or ""
        ).strip() or None

    effective_expected_symbol = expected_symbol
    if not effective_expected_symbol and isinstance(market_data_context, Mapping):
        effective_expected_symbol = str(
            market_data_context.get("symbol")
            or market_data_context.get("ticker")
            or ""
        ).strip() or None

    # Map verification items by claim_id
    ver_by_cid: dict[str, list[Mapping[str, Any]]] = {}
    for item in ver_list:
        cid = str(item.get("claim_id", "") or "").strip()
        if cid:
            ver_by_cid.setdefault(cid, []).append(item)

    # All known claim objects
    known_claims: dict[str, Mapping[str, Any]] = {}
    for c in claims_list:
        cid = str(c.get("claim_id", "") or "").strip()
        if cid:
            known_claims[cid] = c

    # Union of all CIDs preserving order
    all_cids = list(known_claims.keys())
    for cid in ver_by_cid:
        if cid not in known_claims:
            all_cids.append(cid)

    summary_map: dict[str, dict[str, Any]] = {}
    for cid in all_cids:
        claim_obj = known_claims.get(cid, {})
        claim_ver_items = ver_by_cid.get(cid, [])

        is_obs_hypo = is_observation_or_hypothesis_claim(claim_obj)

        # 1. Applicability validation
        norm_applicability: dict[str, Any] | None = None
        applicability_pit_failed = False
        applicability_failed = False
        app_error_msg = ""
        if "applicability" in claim_obj and claim_obj["applicability"] is not None:
            app_val = claim_obj["applicability"]
            if isinstance(app_val, ClaimApplicability):
                app_val = app_val.to_dict()
            ok_app, err_app, norm_app = validate_applicability(
                app_val,
                expected_symbol=effective_expected_symbol,
                current_trade_date=effective_baseline_date,
            )
            if not ok_app:
                if err_app == ERR_SPEC_LOOKAHEAD_PIT:
                    applicability_pit_failed = True
                    app_error_msg = f"适用性规格存在前视偏差/PIT失败 (pit_date={app_val.get('pit_date')} > baseline={effective_baseline_date})"
                else:
                    applicability_failed = True
                    app_error_msg = f"适用性规格校验失败: {err_app}"
            else:
                norm_applicability = norm_app

        # 2. Invalidation conditions validation
        norm_conditions: list[dict[str, Any]] = []
        conditions_pit_failed = False
        conditions_failed = False
        conditions_triggered = False
        cond_error_msgs: list[str] = []
        if "invalidation_conditions" in claim_obj and claim_obj["invalidation_conditions"] is not None:
            raw_conds = claim_obj["invalidation_conditions"]
            if isinstance(raw_conds, (list, tuple, Sequence)) and not isinstance(raw_conds, (str, bytes, Mapping)):
                cond_baseline = effective_baseline_date or (norm_applicability.get("pit_date") if norm_applicability else None)
                for idx, cond in enumerate(raw_conds, start=1):
                    if isinstance(cond, ClaimInvalidationCondition):
                        cond = cond.to_dict()
                    ok_c, err_c, norm_c = validate_invalidation_condition(
                        cond,
                        index=idx,
                        current_trade_date=cond_baseline,
                    )
                    if not ok_c:
                        if err_c == ERR_SPEC_LOOKAHEAD_PIT:
                            conditions_pit_failed = True
                            cond_error_msgs.append(f"失效条件 [{cond.get('condition_id', idx)}] 存在前视偏差/PIT失败 ({err_c})")
                        else:
                            conditions_failed = True
                            cond_error_msgs.append(f"失效条件 [{cond.get('condition_id', idx)}] 规格校验失败 ({err_c})")
                    else:
                        norm_conditions.append(norm_c)
                        if market_data_context and isinstance(market_data_context, Mapping):
                            is_trig, trig_msg = _check_invalidation_condition_triggered(norm_c, market_data_context)
                            if is_trig:
                                conditions_triggered = True
                                cond_error_msgs.append(trig_msg)

        pit_failed = applicability_pit_failed or conditions_pit_failed

        verified_items = [v for v in claim_ver_items if v.get("status") == STATUS_VERIFIED]
        unsupported_items = [v for v in claim_ver_items if v.get("status") == STATUS_UNSUPPORTED]
        contradicted_items = [v for v in claim_ver_items if v.get("status") == STATUS_CONTRADICTED]
        source_unavail_items = [
            v for v in claim_ver_items
            if (v.get("status") == STATUS_SOURCE_UNAVAILABLE and v.get("is_fatal") is not False)
            or (v.get("is_fatal") is True and v.get("status") != STATUS_CONTRADICTED)
        ]
        claim_has_fatal = any(
            v.get("is_fatal") is True or (v.get("is_fatal") is None and v.get("status") == STATUS_SOURCE_UNAVAILABLE)
            for v in claim_ver_items
        )

        total_count = len(claim_ver_items)
        if total_count == 0:
            ev_field = claim_obj.get("evidence") or []
            if isinstance(ev_field, str):
                ev_field = [ev_field]
            total_count = len([e for e in ev_field if str(e).strip()])

        verified_count = len(verified_items)
        unsupported_count = len(unsupported_items)
        contradicted_count = len(contradicted_items)
        source_unavail_count = len(source_unavail_items)

        if pit_failed:
            contradicted_count = max(contradicted_count, 1)

        counts = {
            "total": total_count,
            "verified": verified_count,
            "unsupported": unsupported_count,
            "contradicted": contradicted_count,
            "source_unavailable": source_unavail_count,
        }

        coverage = (verified_count / total_count) if total_count > 0 else 0.0

        verified_ev = [str(v.get("raw", "")).strip() for v in verified_items if str(v.get("raw", "")).strip()]
        unsupported_ev = [str(v.get("raw", "")).strip() for v in unsupported_items if str(v.get("raw", "")).strip()]
        contradicted_ev = [str(v.get("raw", "")).strip() for v in contradicted_items if str(v.get("raw", "")).strip()]
        source_unavail_ev = [str(v.get("raw", "")).strip() for v in source_unavail_items if str(v.get("raw", "")).strip()]
        excluded_ev = [
            str(v.get("raw", "")).strip()
            for v in claim_ver_items
            if v.get("status") != STATUS_VERIFIED and str(v.get("raw", "")).strip()
        ]

        if pit_failed:
            decision = DECISION_REJECT
            all_pit_reasons = []
            if applicability_pit_failed:
                all_pit_reasons.append(app_error_msg)
            if conditions_pit_failed:
                all_pit_reasons.extend(cond_error_msgs)
            reason = f"命题规格契约存在前视偏差/PIT失败 (contradicted): {'; '.join(all_pit_reasons)}"
        elif applicability_failed or conditions_failed:
            decision = DECISION_REJECT
            all_spec_reasons = []
            if applicability_failed:
                all_spec_reasons.append(app_error_msg)
            if conditions_failed:
                all_spec_reasons.extend(cond_error_msgs)
            reason = f"命题规格契约校验失败: {'; '.join(all_spec_reasons)}"
        elif conditions_triggered:
            decision = DECISION_REJECT
            reason = f"命题失效条件已触发证伪 (contradicted): {'; '.join(cond_error_msgs)}"
            contradicted_count = max(contradicted_count, 1)
            counts["contradicted"] = contradicted_count
        elif contradicted_count > 0:
            decision = DECISION_REJECT
            reason = f"存在 {contradicted_count} 条与报告事实冲突/前视偏差证据 (contradicted)"
        elif source_unavail_count > 0:
            decision = DECISION_REJECT
            reason = f"存在 {source_unavail_count} 条引用不可用数据源的严重幻觉证据 (source_unavailable)"
        elif total_count == 0 or verified_count == 0:
            decision = DECISION_REJECT
            reason = "未提供有效证据或全部证据未获验证 (unsupported)"
        elif verified_count == total_count:
            if is_obs_hypo:
                decision = DECISION_PARTIAL
                reason = f"观察/假设类命题 (observation/hypothesis) 核验通过但须留在可审计的观察状态 (verified {verified_count}/{total_count})，不升级为已验证事实 (adopt)"
            else:
                decision = DECISION_ADOPT
                reason = f"全部证据核验通过 (verified {verified_count}/{total_count}, coverage=100.0%)"
        elif coverage >= MIN_COVERAGE_THRESHOLD or round(coverage, 2) >= MIN_COVERAGE_THRESHOLD or math.isclose(coverage, 2 / 3, abs_tol=1e-3):
            decision = DECISION_PARTIAL
            reason = f"混合证据部分通过核验 (verified {verified_count}/{total_count}, coverage={coverage:.1%})，仅可采纳 verified 子结论并剔除未验证项"
        else:
            decision = DECISION_REJECT
            reason = f"证据覆盖率不足 (verified {verified_count}/{total_count}, coverage={coverage:.1%} < {MIN_COVERAGE_THRESHOLD:.0%})，予以驳回/降权"

        summary_map[cid] = {
            "claim_id": cid,
            "speaker": str(claim_obj.get("speaker", "") or ""),
            "speaker_key": str(claim_obj.get("speaker_key", "") or ""),
            "stance": str(claim_obj.get("stance", "") or ""),
            "claim": str(claim_obj.get("claim", "") or ""),
            "counts": counts,
            "coverage": coverage,
            "decision": decision,
            "reason": reason,
            "verified_evidence": verified_ev,
            "unsupported_evidence": unsupported_ev,
            "contradicted_evidence": contradicted_ev,
            "source_unavailable_evidence": source_unavail_ev,
            "excluded_evidence": excluded_ev,
            "is_observation_or_hypothesis": is_obs_hypo,
            "applicability": norm_applicability,
            "invalidation_conditions": norm_conditions,
            "pit_failed": pit_failed,
            "is_fatal": claim_has_fatal,
        }

    return summary_map


def format_claims_with_verification_for_prompt(
    claims: Sequence[Mapping[str, Any]] | None,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
    claim_evidence_summary: Mapping[str, Mapping[str, Any]] | None = None,
    focus_claim_ids: Sequence[str] | None = None,
    empty_message: str = "当前没有已登记 claim。",
) -> str:
    """Format claim overview with deterministic factual verification details for research manager prompt."""
    claim_list = list(claims or [])
    if not claim_list and not claims_verification:
        return empty_message

    if claim_evidence_summary is None:
        summary_map = aggregate_claim_evidence(claim_list, claims_verification or [])
    else:
        summary_map = dict(claim_evidence_summary)

    focus_set = {str(item) for item in (focus_claim_ids or []) if str(item).strip()}
    lines: list[str] = []

    badge_map = {
        DECISION_ADOPT: "证据充分 / 全Verified",
        DECISION_PARTIAL: "部分支持 / 混合证据(仅采纳Verified子结论)",
        DECISION_REJECT: "证据薄弱/不支持/矛盾(驳回)",
    }

    for claim in claim_list:
        cid = str(claim.get("claim_id", "")).strip()
        status = str(claim.get("status", "open")).strip() or "open"
        speaker = str(claim.get("speaker", "")).strip() or "Unknown"
        stance = str(claim.get("stance", "")).strip() or ""
        summary_text = str(claim.get("claim", "")).strip() or "未提供 claim 文本"

        sum_info = summary_map.get(cid)
        prefix = "* " if cid in focus_set else "- "
        stance_str = f" ({stance})" if stance else ""

        if not sum_info:
            evidence = claim.get("evidence") or []
            if isinstance(evidence, str):
                evidence = [evidence]
            ev_text = "；".join(str(e).strip() for e in evidence if str(e).strip()) or "无明确证据"
            lines.append(f"{prefix}{cid} [{status}] {speaker}{stance_str}: {summary_text} | 证据: {ev_text}")
            continue

        decision = sum_info.get("decision", DECISION_REJECT)
        is_obs = sum_info.get("is_observation_or_hypothesis", False)
        badge = "观察/假设类命题 (可审计未验证/部分采纳)" if is_obs else badge_map.get(decision, "待核验")
        cov = sum_info.get("coverage", 0.0)
        counts = sum_info.get("counts", {})
        total = counts.get("total", 0)
        verified = counts.get("verified", 0)
        reason = sum_info.get("reason", "")

        lines.append(f"{prefix}{cid} [{status}] {speaker}{stance_str}: {summary_text}")
        lines.append(f"  * 核验评级: 【{badge}】 覆盖率={cov:.1%} ({verified}/{total} verified) | 规则判定: {decision}")
        lines.append(f"  * 判定说明: {reason}")
        if sum_info.get("applicability"):
            app = sum_info["applicability"]
            lines.append(f"  * 适用档规格: 标的={app.get('symbol')}, 观察窗={app.get('horizon')}, 计量基准={app.get('metric_basis')}, PIT截止日={app.get('pit_date')}")
        if sum_info.get("invalidation_conditions"):
            for cond in sum_info["invalidation_conditions"]:
                lines.append(f"  * 证伪/失效条件 [{cond.get('condition_id')}]: {cond.get('metric')} {cond.get('operator')} {cond.get('threshold')} {cond.get('unit')} (周期: {cond.get('period')}, 来源: {cond.get('source')}, PIT={cond.get('pit_date')})")

        ver_ev = sum_info.get("verified_evidence", [])
        unsupp_ev = sum_info.get("unsupported_evidence", [])
        contra_ev = sum_info.get("contradicted_evidence", [])
        unavail_ev = sum_info.get("source_unavailable_evidence", [])

        if ver_ev:
            for e in ver_ev:
                lines.append(f"    - [VERIFIED / 真实核验] {e}")
        if unsupp_ev:
            for e in unsupp_ev:
                lines.append(f"    - [UNSUPPORTED / 未获支撑] {e} (严禁作为采纳依据，必须剔除)")
        if contra_ev:
            for e in contra_ev:
                lines.append(f"    - [CONTRADICTED / 事实冲突] {e} (严禁采纳，必须驳回)")
        if unavail_ev:
            for e in unavail_ev:
                lines.append(f"    - [UNAVAILABLE / 严重幻觉] {e} (数据源不可用，严禁采纳)")

    return "\n".join(lines)


def format_challenges_for_prompt(
    challenges: Sequence[Mapping[str, Any]] | None,
    challenge_verification: Sequence[Mapping[str, Any]] | None = None,
    empty_message: str = "当前没有提出交叉盘问 (challenges)。",
) -> str:
    """Format challenges with verification status for research manager prompt."""
    ch_list = list(challenges or [])
    if not ch_list:
        return empty_message

    ver_map: dict[str, Mapping[str, Any]] = {}
    if challenge_verification:
        for v in challenge_verification:
            chid = str(v.get("challenge_id", "")).strip()
            if chid:
                ver_map[chid] = v

    lines: list[str] = []
    for ch in ch_list:
        chid = str(ch.get("challenge_id", "")).strip()
        speaker = str(ch.get("speaker") or ch.get("speaker_key") or "Unknown").strip()
        target_id = str(ch.get("target_claim_id", "")).strip()
        weakest = str(ch.get("weakest_point", "")).strip()
        sev = str(ch.get("severity", "major")).strip().lower()
        ev_list = [str(e).strip() for e in (ch.get("evidence") or []) if str(e).strip()]
        ev_str = "；".join(ev_list) if ev_list else "无"
        status = str(ch.get("status", "open")).strip()

        ver_info = ver_map.get(chid)
        ev_status = ver_info.get("evidence_status") if ver_info else ch.get("evidence_status", "unverified")
        badge = f"【证据核验: {ev_status}】" if ev_status else ""

        line = f"- {chid} [{status}] {speaker} 攻击对手 {target_id} (严厉度: {sev}) {badge}: 弱点={weakest} | 证据: {ev_str}"
        lines.append(line)

    return "\n".join(lines)


def format_challenge_verification_summary(
    challenges: Sequence[Mapping[str, Any]] | None,
    challenge_verification: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Format summary of challenge evidence verification status for research manager prompt."""
    ch_list = list(challenges or [])
    if not ch_list:
        return "暂无交叉盘问核验数据。"

    ver_list = list(challenge_verification or [])
    if not ver_list:
        return "交叉盘问证据尚未核验。"

    verified_count = sum(1 for v in ver_list if v.get("evidence_status") == "verified")
    unsupported_count = sum(1 for v in ver_list if v.get("evidence_status") == "unsupported")
    contradicted_count = sum(1 for v in ver_list if v.get("evidence_status") == "contradicted")

    lines = [
        f"交叉盘问核验汇总 (共 {len(ver_list)} 项): Verified={verified_count}, Unsupported={unsupported_count}, Contradicted={contradicted_count}",
    ]
    for v in ver_list:
        chid = v.get("challenge_id", "")
        target_id = v.get("target_claim_id", "")
        sev = v.get("severity", "")
        ev_st = v.get("evidence_status", "")
        lines.append(f"  * {chid} (针对 {target_id}, 严厉度: {sev}): 证据状态={ev_st}")

    return "\n".join(lines)


def format_battlefield_coverage(claims: Sequence[Mapping[str, Any]] | None) -> str:
    """Format summary of covered battlefields by camp for research manager prompt."""
    claim_list = list(claims or [])
    if not claim_list:
        return "暂无战场覆盖数据。"

    bull_bfs = set()
    bear_bfs = set()
    for c in claim_list:
        bf = str(c.get("battlefield", "")).strip()
        if not bf:
            continue
        sp = str(c.get("speaker_key") or c.get("speaker") or "")
        st = str(c.get("stance") or "").lower()
        if "bull" in sp.lower() or "bull" in st:
            bull_bfs.add(bf)
        elif "bear" in sp.lower() or "bear" in st:
            bear_bfs.add(bf)

    b_str = ", ".join(sorted(bull_bfs)) if bull_bfs else "未指定"
    be_str = ", ".join(sorted(bear_bfs)) if bear_bfs else "未指定"
    return f"多头覆盖战场 ({len(bull_bfs)}/5): {b_str} | 空头覆盖战场 ({len(bear_bfs)}/5): {be_str}"


def normalize_winner(winner_raw: Any, direction_raw: Any = "") -> str:
    """Normalize winner string to one of 'bull', 'bear', 'tie'."""
    w_str = str(winner_raw or "").strip().lower()
    if w_str in {"bull", "bullish", "多头", "多方", "多头胜", "多方胜", "多头全面胜出"}:
        return "bull"
    elif w_str in {"bear", "bearish", "空头", "空方", "空头胜", "空方胜", "空头全面胜出"}:
        return "bear"
    elif w_str in {"tie", "neutral", "平局", "势均力敌", "分歧", "观望", "hold", "中性", "unresolved"}:
        return "tie"

    # Infer from direction if winner is not explicit
    d_str = str(direction_raw or "").strip().lower()
    if d_str in {"看多", "偏多", "buy", "bullish", "lean_bullish", "买入", "增持"}:
        return "bull"
    elif d_str in {"看空", "偏空", "sell", "bearish", "lean_bearish", "卖出", "减持"}:
        return "bear"
    elif d_str in {"中性", "观望", "hold", "neutral", "持有"}:
        return "tie"

    return "tie"



def is_daily_ohlcv_unavailable(market_data_context: Any) -> bool:
    """True when daily OHLCV is missing or failed in a *provided* market_data_context.

    Compatibility: ``None`` (caller did not pass context) returns False so legacy
    call sites keep prior behavior. Any explicitly provided Mapping — including
    ``{}``, missing ``stock_data`` provenance, or no usable daily as_of — is
    treated as unavailable (fail-closed).
    """
    if market_data_context is None:
        return False
    if not isinstance(market_data_context, Mapping):
        return True

    provenance = market_data_context.get("source_provenance")
    if isinstance(provenance, Mapping):
        stock = provenance.get("stock_data")
        if isinstance(stock, Mapping):
            status = str(stock.get("status") or "").strip().lower()
            prov_status = str(stock.get("provenance_status") or "").strip().lower()
            if status in {"unavailable", "failed", "timeout", "refused", "error", "future", "available_unverified_as_of"}:
                return True
            if prov_status in {"refused", "future", "unverified"}:
                return True
            gap = str(stock.get("gap") or "")
            if "无有效完整日线" in gap or "【数据获取失败】stock_data" in gap:
                return True
            if status == "available" and stock.get("as_of") and prov_status == "verified":
                return False
            if status == "available" and stock.get("as_of") and not prov_status:
                return False
            # Provenance present but not a usable available+as_of bar.
            return True
        # Context provided with provenance map but no stock_data entry.
        if "stock_data" not in provenance:
            # Fall through to daily / ledger checks before concluding.
            pass
        else:
            return True

    ledger = market_data_context.get("data_failure_ledger")
    if isinstance(ledger, Sequence) and not isinstance(ledger, (str, bytes)):
        for entry in ledger:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("source") or "").strip() != "stock_data":
                continue
            status = str(entry.get("status") or "").strip().lower()
            prov_status = str(entry.get("provenance_status") or "").strip().lower()
            gap = str(entry.get("gap") or "")
            if status in {"unavailable", "failed", "timeout", "refused", "error", "future", "available_unverified_as_of"}:
                return True
            if prov_status in {"refused", "future", "unverified"}:
                return True
            if "无有效完整日线" in gap or "【数据获取失败】stock_data" in gap:
                return True

    daily = market_data_context.get("daily")
    if isinstance(daily, Mapping):
        daily_status = str(daily.get("status") or "").strip().lower()
        if daily_status in {"unavailable", "failed", "timeout", "refused", "error", "future", "available_unverified_as_of"}:
            return True
        completeness = str(daily.get("completeness") or "").strip().lower()
        if completeness == "completed" and daily.get("as_of"):
            return False
        if daily.get("as_of") and completeness not in {"unavailable", ""}:
            return False
        # Explicit daily block without usable as_of.
        if "as_of" in daily or "completeness" in daily or "status" in daily:
            return True

    # Provided context but no usable OHLCV evidence anywhere.
    return True



_FUND_FLOW_IN_MARKERS = ("流入", "净流入", "吸筹", "inflow", "accumulation")
_FUND_FLOW_OUT_MARKERS = ("流出", "净流出", "派发", "出货", "outflow", "distribution")
_FUND_FLOW_ABSORPTION_MARKERS = (
    "流出=吸筹",
    "流出等于吸筹",
    "流出即吸筹",
    "边打边吸",
    "边出边吸",
    "流出当作吸筹",
)


def is_conflicting_fund_flow_dispute(
    data_point: str = "",
    bull_interpretation: str = "",
    bear_interpretation: str = "",
    evidence_decision: str = "",
) -> bool:
    """True when a dispute row mixes inflow/outflow prints or uses outflow=absorption."""
    blob = " ".join(
        [
            str(data_point or ""),
            str(bull_interpretation or ""),
            str(bear_interpretation or ""),
            str(evidence_decision or ""),
        ]
    )
    if not blob.strip():
        return False
    if any(marker in blob for marker in _FUND_FLOW_ABSORPTION_MARKERS):
        return True
    has_in = any(marker in blob for marker in _FUND_FLOW_IN_MARKERS)
    has_out = any(marker in blob for marker in _FUND_FLOW_OUT_MARKERS)
    return has_in and has_out


# ── Claim ID Grammar & Token Matching (DAV-1106) ───────────────────────────
#
# 合法 claim_id 语法：由英文字母、数字、下划线及连字符组成（[A-Za-z0-9_-]+），
# 形如 INV-1, INV-10, RISK-2, CLM-OBS 等。
#
# 边界契约：
#   - 前向边界 (?<![A-Za-z0-9_-])：防止被较长标识符或复合 ID 前缀误捕获（如 comp_INV-1 中前置下划线）。
#   - 后向边界 (?![A-Za-z0-9_-])：防止被扩展数字或复合 ID 后缀误捕获（如 INV-1 误命中 INV-10/11/12，或 comp_INV-1_xxx 中后置下划线）。
#
# comp_INV-1_xxx 复合 ID 的引用语义（契约明确，严禁靠猜）：
#   comp_{cid}_{hash} 属于证据图规约（claim clustering / reduction）生成的 FoldedComponent
#   组件级标识符，用于独立性审计（global_contribution_cap 约束）。
#   其内部携带的前缀 {cid} 仅代表该拓扑分量中按字典序排序的首个遍历节点，并不构成对 claim {cid}
#   本身的采纳、驳回或'证据充分'性质评。
#   裁决自洽检查（consistency gate）核验的是经理对单一原子 claim 的证据核验裁定，因此复合组件 ID
#   严禁被识别为原子 claim 的正文引用。Token 边界前后约束严格将 comp_INV-1_xxx 排除在
#   INV-1 的匹配范围之外。


def build_claim_id_token_pattern(cid: str) -> str:
    """构建带严格 token 边界的 claim_id 正则子模式 (DAV-1106).

    前后均带边界约束：
      前向 (?<![A-Za-z0-9_-])：防止命中复合前缀（如 comp_INV-1）
      后向 (?![A-Za-z0-9_-])：防止命中数字前缀（如 INV-1 命中 INV-10/11）及复合后缀（如 INV-1_xxx）
    """
    clean_cid = str(cid or "").strip()
    if not clean_cid:
        return ""
    return rf"(?<![A-Za-z0-9_-]){re.escape(clean_cid)}(?![A-Za-z0-9_-])"


def build_claim_evidence_sufficient_pattern(cid: str) -> re.Pattern:
    """构建检测正文将 claim 标注为'证据充分'的正则模式 (DAV-1106).

    匹配格式：
      1. <claim_id>[^\\n。；]*?证据充分
      2. 证据充分[^\\n。；]*?<claim_id>
    两分支中 claim_id 均具备完整 token 边界。
    """
    token_pat = build_claim_id_token_pattern(cid)
    if not token_pat:
        return re.compile(r"(?!)")
    return re.compile(
        rf"{token_pat}[^\n。；]*?证据充分|证据充分[^\n。；]*?{token_pat}",
        re.IGNORECASE,
    )


def extract_and_validate_manager_verdict(
    raw_response: str,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
    claims: Sequence[Mapping[str, Any]] | None = None,
    challenges: Sequence[Mapping[str, Any]] | None = None,
    challenges_verification: Sequence[Mapping[str, Any]] | None = None,
    market_data_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract structured manager verdict and perform strict consistency check.

    Returns:
        manager_verdict dict with keys:
        - direction
        - winner ("bull" | "bear" | "tie")
        - reason
        - position_pct
        - entry
        - target
        - stop_loss
        - upside
        - downside
        - odds
        - adopted_claim_ids
        - partially_adopted_claims
        - rejected_claim_ids
        - excluded_evidence
        - claim_evidence_summary
        - dispute_map
        - consistency_check_passed
        - failed_checks
    """
    from tradingagents.agents.utils.debate_utils import extract_tagged_json, strip_tagged_json

    # Attempt to extract MANAGER_VERDICT block first, then fallback to VERDICT block
    payload = extract_tagged_json(raw_response, "MANAGER_VERDICT")
    if not payload:
        payload = extract_tagged_json(raw_response, "VERDICT")

    failed_checks: list[str] = []

    if not payload:
        failed_checks.append("未提取到有效的研究总监结构化裁决机读块 (MANAGER_VERDICT 或 VERDICT)")

    direction = str(payload.get("direction", "")).strip() if payload else ""
    winner = normalize_winner(payload.get("winner"), direction)
    reason = str(payload.get("reason", "")).strip() if payload else ""
    ohlcv_gate_applied = False

    # A2: missing daily OHLCV → fail-closed; never allow bull/bear winner.
    if is_daily_ohlcv_unavailable(market_data_context) and winner in {"bull", "bear"}:
        winner = "tie"
        direction = "中性"
        gate_note = "日线 OHLCV 不可用，禁止方向性裁决"
        reason = f"{reason}；{gate_note}" if reason else gate_note
        ohlcv_gate_applied = True

    position_pct = payload.get("position_pct") if payload else None
    entry = payload.get("entry") if payload else None
    target = payload.get("target") if payload else None
    stop_loss = payload.get("stop_loss") if payload else None
    upside = payload.get("upside") if payload else None
    downside = payload.get("downside") if payload else None
    odds = payload.get("odds") if payload else None

    # Helper for extracting claim id lists
    def _to_str_list(val: Any) -> list[str]:
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
        elif isinstance(val, str) and val.strip():
            return [val.strip()]
        return []

    adopted_claim_ids = _to_str_list(payload.get("adopted_claim_ids")) if payload else []
    partially_adopted_claims = _to_str_list(payload.get("partially_adopted_claims")) if payload else []
    rejected_claim_ids = _to_str_list(payload.get("rejected_claim_ids")) if payload else []
    excluded_evidence = _to_str_list(payload.get("excluded_evidence")) if payload else []

    # ── Extract Dispute Map ───────────────────────────────────────────────
    raw_dispute_map = payload.get("dispute_map") or []
    dispute_map: list[dict[str, Any]] = []
    fund_flow_dispute_gate_applied = False
    if isinstance(raw_dispute_map, list):
        for item in raw_dispute_map:
            if isinstance(item, Mapping):
                dp = str(item.get("data_point") or "").strip()
                b_interp = str(item.get("bull_interpretation") or "").strip()
                be_interp = str(item.get("bear_interpretation") or "").strip()
                ev_dec = str(item.get("evidence_decision") or "").strip()
                w_raw = str(item.get("winner") or "").strip()
                row_winner = normalize_winner(w_raw)
                if is_conflicting_fund_flow_dispute(dp, b_interp, be_interp, ev_dec):
                    row_winner = "tie"
                    ev_dec = "分单对打/冲突资金流不得单独支撑方向（禁止流出=吸筹）"
                    fund_flow_dispute_gate_applied = True
                dispute_map.append({
                    "data_point": dp,
                    "bull_interpretation": b_interp,
                    "bear_interpretation": be_interp,
                    "evidence_decision": ev_dec,
                    "winner": row_winner,
                })

    # A4: if every directional dispute was fund-flow conflict, do not keep bull/bear.
    if fund_flow_dispute_gate_applied and winner in {"bull", "bear"}:
        directional_rows = [r for r in dispute_map if r.get("winner") in {"bull", "bear"}]
        if not directional_rows:
            winner = "tie"
            direction = "中性"
            gate_note = "资金流分单冲突，禁止据此给出方向性裁决"
            reason = f"{reason}；{gate_note}" if reason else gate_note

    # ── Deterministic Claim Evidence Summary Computation ──────────────────
    claim_evidence_summary: dict[str, dict[str, Any]] = {}
    if claims is not None or claims_verification is not None:
        b_date = None
        exp_sym = None
        if isinstance(market_data_context, Mapping):
            b_date = str(
                market_data_context.get("analysis_baseline_date")
                or market_data_context.get("data_as_of")
                or market_data_context.get("trade_date")
                or ""
            ).strip() or None
            exp_sym = str(
                market_data_context.get("symbol")
                or market_data_context.get("ticker")
                or ""
            ).strip() or None
        claim_evidence_summary = aggregate_claim_evidence(
            claims=claims,
            claims_verification=claims_verification,
            analysis_baseline_date=b_date,
            expected_symbol=exp_sym,
            market_data_context=market_data_context,
        )
    elif payload and isinstance(payload.get("claim_evidence_summary"), dict):
        claim_evidence_summary = payload["claim_evidence_summary"]

    deterministic_excluded: list[str] = []
    for cid, s in claim_evidence_summary.items():
        if cid in partially_adopted_claims or cid in rejected_claim_ids or s.get("decision") in {DECISION_PARTIAL, DECISION_REJECT}:
            deterministic_excluded.extend(s.get("excluded_evidence", []))
    combined_excluded = list(dict.fromkeys(excluded_evidence + deterministic_excluded))

    # ── Consistency Hard Gate Validation ──────────────────────────────────
    # Check 1: Winner vs Direction
    if winner == "bear":
        if direction.upper() in {"BUY", "BULLISH", "LEAN_BULLISH"} or direction in {"看多", "偏多", "买入", "增持"}:
            failed_checks.append(f"空头胜裁决下方向不得为看多/买入 (当前: {direction})")
    elif winner == "bull":
        if direction.upper() in {"SELL", "BEARISH", "LEAN_BEARISH"} or direction in {"看空", "偏空", "卖出", "减持"}:
            failed_checks.append(f"多头胜裁决下方向不得为看空/卖出 (当前: {direction})")

    # Check 2: Bear position percentage
    if winner == "bear" and position_pct is not None:
        try:
            pos_val = float(str(position_pct).replace("%", "").strip())
            pos_ratio = pos_val / 100.0 if pos_val > 1.0 else pos_val
            if pos_ratio > 0.20:
                failed_checks.append(f"空头胜裁决下建议仓位({pos_val}%)过高，不得高于20%")
        except (ValueError, TypeError):
            pass

    # Check 3: Bull stop loss requirement & validation
    has_manager_block = bool(extract_tagged_json(raw_response, "MANAGER_VERDICT"))
    if winner == "bull":
        if has_manager_block and (not stop_loss or str(stop_loss).strip() in {"无", "null", "None", ""}):
            failed_checks.append("多头胜裁决必须设定明确有效的止损位 (stop_loss)")
        elif stop_loss and str(stop_loss).strip() not in {"无", "null", "None", ""}:
            # If both entry and stop_loss are numbers, stop_loss must be lower than entry
            try:
                e_clean = str(entry).split("-")[0].replace("元", "").strip() if entry else ""
                s_clean = str(stop_loss).replace("元", "").strip()
                if e_clean:
                    e_num = float(e_clean)
                    s_num = float(s_clean)
                    if s_num >= e_num:
                        failed_checks.append(f"多头胜止损位({s_num})必须严格低于入场价({e_num})")
            except (ValueError, TypeError):
                pass

    # Check 4: Tie / Hold validation
    if winner == "tie" or direction.upper() in {"HOLD", "NEUTRAL"} or direction in {"中性", "观望", "持有"}:
        if position_pct is not None:
            try:
                pos_val = float(str(position_pct).replace("%", "").strip())
                pos_ratio = pos_val / 100.0 if pos_val > 1.0 else pos_val
                if pos_ratio > 0.30:
                    failed_checks.append(f"势均力敌/观望裁决下建议仓位({pos_val}%)过高，不得高于30%")
            except (ValueError, TypeError):
                pass

    # Check 5: Contradiction between prose text and verdict winner
    prose = strip_tagged_json(raw_response, "MANAGER_VERDICT")
    prose = strip_tagged_json(prose, "VERDICT")
    if "空头胜" in prose or "空方胜" in prose or "空头全面占优" in prose:
        if winner == "bull":
            failed_checks.append("正文明确判定空头胜，但机读块为多头胜(bull)，正文与机读裁决严重矛盾")
    elif "多头胜" in prose or "多方胜" in prose or "多头全面占优" in prose:
        if winner == "bear":
            failed_checks.append("正文明确判定多头胜，但机读块为空头胜(bear)，正文与机读裁决严重矛盾")

    # Check 6: Claim ledger subset and existence validation
    if claims is not None:
        known_cids = {
            str(c.get("claim_id", "")).strip()
            for c in claims
            if str(c.get("claim_id", "")).strip()
        }
        for cid in adopted_claim_ids:
            if cid not in known_cids:
                failed_checks.append(f"裁决采纳了不存在的 claim ID: {cid} (当前账本: {sorted(known_cids)})")
        for cid in partially_adopted_claims:
            if cid not in known_cids:
                failed_checks.append(f"裁决部分采纳了不存在的 claim ID: {cid} (当前账本: {sorted(known_cids)})")
        for cid in rejected_claim_ids:
            if cid not in known_cids:
                failed_checks.append(f"裁决拒绝了不存在的 claim ID: {cid} (当前账本: {sorted(known_cids)})")

    # Check 7: Claim Evidence Coverage & Consistency Hard Gate
    if claim_evidence_summary:
        for cid in adopted_claim_ids:
            if cid in claim_evidence_summary:
                s = claim_evidence_summary[cid]
                cnt = s.get("counts", {})
                cov = s.get("coverage", 0.0)
                dec = s.get("decision")
                is_obs = s.get("is_observation_or_hypothesis", False)
                if s.get("pit_failed") or cnt.get("contradicted", 0) > 0:
                    failed_checks.append(f"裁决采纳了存在事实冲突/前视偏差的矛盾 claim: {cid}")
                elif cnt.get("source_unavailable", 0) > 0:
                    failed_checks.append(f"裁决采纳了不可用数据源的严重幻觉 claim: {cid}")
                elif is_obs:
                    failed_checks.append(f"裁决全额采纳了观察/假设类 claim: {cid}，观察/假设类命题不得升级为已验证事实 (adopt)")
                elif cnt.get("verified", 0) == 0 or cnt.get("total", 0) == 0:
                    failed_checks.append(f"裁决采纳了全部证据未获验证 (unsupported) 的 claim: {cid}")
                elif cov < MIN_COVERAGE_THRESHOLD and not math.isclose(cov, 2 / 3, abs_tol=1e-3):
                    failed_checks.append(f"裁决采纳了证据覆盖率不足 ({cov:.1%} < 67%) 的 claim: {cid}")
                elif dec == DECISION_PARTIAL or (0.67 <= cov < 1.0 and not math.isclose(cov, 1.0)):
                    failed_checks.append(
                        f"裁决全额采纳了含未核实混合证据的 claim: {cid} (coverage={cov:.1%})，混合证据仅允许记录于 partially_adopted_claims 并剔除未验证项"
                    )

        for cid in partially_adopted_claims:
            if cid in claim_evidence_summary:
                s = claim_evidence_summary[cid]
                cnt = s.get("counts", {})
                cov = s.get("coverage", 0.0)
                is_obs = s.get("is_observation_or_hypothesis", False)
                if s.get("pit_failed") or cnt.get("contradicted", 0) > 0:
                    failed_checks.append(f"部分采纳列表中包含了存在事实冲突/前视偏差的矛盾 claim: {cid}")
                elif cnt.get("source_unavailable", 0) > 0:
                    failed_checks.append(f"部分采纳列表中包含了不可用数据源的严重幻觉 claim: {cid}")
                elif not is_obs and (cnt.get("verified", 0) == 0 or cnt.get("total", 0) == 0):
                    failed_checks.append(f"部分采纳列表中包含了全部证据未获验证 (unsupported) 的 claim: {cid}")
                elif not is_obs and (cov < MIN_COVERAGE_THRESHOLD and not math.isclose(cov, 2 / 3, abs_tol=1e-3)):
                    failed_checks.append(f"部分采纳列表中包含了证据覆盖率不足 ({cov:.1%} < 67%) 的 claim: {cid}")

        # Check prose consistency against claim verification
        for cid, s in claim_evidence_summary.items():
            cov = s.get("coverage", 0.0)
            dec = s.get("decision")
            if dec != DECISION_ADOPT or cov < 1.0:
                pattern = build_claim_evidence_sufficient_pattern(cid)
                for line in prose.splitlines():
                    if pattern.search(line):
                        if not re.search(r"非[^\n]*?证据充分|不[^\n]*?证据充分|未[^\n]*?证据充分|不能[^\n]*?证据充分", line):
                            failed_checks.append(
                                f"裁决正文将未完全核实的 claim {cid} (coverage={cov:.1%}, decision={dec}) 标注为'证据充分'，正文与证据核验严重冲突"
                            )
                            break
    elif claims_verification:
        # Fallback fatal check if only raw verification list was provided without claim summary
        fatal_cids = {
            str(item.get("claim_id"))
            for item in claims_verification
            if item.get("is_fatal") is True or (item.get("is_fatal") is None and item.get("status") == STATUS_SOURCE_UNAVAILABLE)
        }
        for cid in adopted_claim_ids:
            if str(cid) in fatal_cids:
                failed_checks.append(f"裁决采纳了不可用数据源的严重幻觉 claim: {cid}")

    # ── Check 8: Fatal Challenge Consistency Hard Gate ──────────────────
    ch_map: dict[str, Mapping[str, Any]] = {}
    if challenges:
        for ch in challenges:
            chid = str(ch.get("challenge_id", "")).strip()
            if chid:
                ch_map[chid] = ch

    ch_ver_map: dict[str, Mapping[str, Any]] = {}
    if challenges_verification:
        for cv in challenges_verification:
            chid = str(cv.get("challenge_id", "")).strip()
            if chid:
                ch_ver_map[chid] = cv

    # Rule 8.1: Unverified fatal challenge cannot reject 100% verified claim
    for chid, ch in ch_map.items():
        sev = str(ch.get("severity", "major")).strip().lower()
        if sev == "fatal":
            target_id = str(ch.get("target_claim_id", "")).strip()
            cv = ch_ver_map.get(chid, {})
            ev_status = cv.get("evidence_status") or ch.get("evidence_status", "unverified")

            # If fatal challenge evidence is unsupported or contradicted
            if ev_status in ("unsupported", "contradicted", "unverified"):
                if target_id in rejected_claim_ids and claim_evidence_summary:
                    target_summary = claim_evidence_summary.get(target_id, {})
                    target_cov = target_summary.get("coverage", 0.0)
                    target_dec = target_summary.get("decision")
                    if target_dec == DECISION_ADOPT and (target_cov >= 1.0 or math.isclose(target_cov, 1.0)):
                        failed_checks.append(
                            f"未经验证的 fatal challenge ({chid}, status={ev_status}) 不得作为否决高质量已验证 claim {target_id} 的依据"
                        )

            # Rule 8.2: Contradicted fatal challenge must be rejected
            if ev_status == "contradicted":
                ch_status = str(ch.get("status", "")).strip().lower()
                adopted_challenges = payload.get("adopted_challenge_ids") or []
                if ch_status == "adopted" or chid in adopted_challenges:
                    failed_checks.append(
                        f"存在事实冲突的 fatal challenge ({chid}) 必须被驳回，不得采纳"
                    )

    consistency_passed = (len(failed_checks) == 0)

    return {
        "direction": direction,
        "winner": winner,
        "reason": reason,
        "position_pct": position_pct,
        "entry": entry,
        "target": target,
        "stop_loss": stop_loss,
        "upside": upside,
        "downside": downside,
        "odds": odds,
        "adopted_claim_ids": adopted_claim_ids,
        "partially_adopted_claims": partially_adopted_claims,
        "rejected_claim_ids": rejected_claim_ids,
        "excluded_evidence": combined_excluded,
        "claim_evidence_summary": claim_evidence_summary,
        "dispute_map": dispute_map,
        "consistency_check_passed": consistency_passed,
        "failed_checks": failed_checks,
        "ohlcv_gate_applied": ohlcv_gate_applied,
        "fund_flow_dispute_gate_applied": fund_flow_dispute_gate_applied,
    }

