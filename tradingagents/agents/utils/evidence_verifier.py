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
    {"failed", "unavailable", "empty", "error", "missing", "partial_failure", "not_found", "rejected"}
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

# Chinese and English quantity/unit patterns
_UNIT_STR = r"(?:万股|亿股|股|亿元|万元|万|亿|%|％|pct|bp|点|元|港元|美元|倍|次|手)"

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
    r"(?<![\d.])\d{4}[hHqQ][1-4](?![\d.])|"
    r"(?<![\d.])[hHqQ][1-4](?![\d.])"
)

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
    "大金", "惠而浦", "乌东德", "三峡", "美的", "恒瑞", "礼来"
]


def _extract_metric_keywords(text: str) -> list[str]:
    """Extract financial and market metric keywords from a string."""
    found = []
    text_lower = text.lower()
    for kw in _METRIC_KEYWORDS:
        if kw in text_lower:
            found.append(kw)
    return found


class EvidenceFactualTruthEvaluator:
    """Evaluates evidence statements against 7 analyst reports and market context."""

    def __init__(self, relative_tolerance: float = 0.02, absolute_tolerance: float = 0.05):
        self.rel_tol = relative_tolerance
        self.abs_tol = absolute_tolerance

    def _extract_unavailable_sources(
        self, market_data_context: Mapping[str, Any] | None
    ) -> set[str]:
        unavailable = set()
        if not isinstance(market_data_context, Mapping):
            return unavailable

        # Check data_failure_ledger
        ledger = market_data_context.get("data_failure_ledger")
        if isinstance(ledger, list):
            for entry in ledger:
                if isinstance(entry, dict):
                    src = str(entry.get("source", "")).strip().lower()
                    status = str(entry.get("status", "")).strip().lower()
                    if src and (not status or status in UNAVAILABLE_STATUSES):
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
                    if status in UNAVAILABLE_STATUSES:
                        unavailable.add(src.strip().lower())

        # Check data_gaps
        gaps = market_data_context.get("data_gaps")
        if isinstance(gaps, list):
            for g in gaps:
                if isinstance(g, str):
                    unavailable.add(g.strip().lower())

        return unavailable

    def _check_source_unavailable(
        self, raw_evidence: str, unavailable_sources: set[str]
    ) -> tuple[bool, str]:
        if not unavailable_sources or not raw_evidence:
            return False, ""
        evidence_lower = raw_evidence.lower()
        for src in unavailable_sources:
            if src and (src in evidence_lower or evidence_lower in src):
                return True, src
        return False, ""

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
        unavailable_sources = self._extract_unavailable_sources(market_data_context)
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
        ev_numbers = _extract_numbers_and_units(raw_text)
        ev_keywords = _extract_metric_keywords(raw_text)

        # 3.1 Exact substring match in any report
        for role_key in SEVEN_REPORT_KEYS:
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

        # 3.2 Single-line full match in reports
        for role_key in SEVEN_REPORT_KEYS:
            report_body = str(seven_reports.get(role_key, "") or "")
            if not report_body.strip():
                continue

            for line in report_body.splitlines():
                line_text = line.strip()
                if not line_text:
                    continue

                # Check keyword overlap
                line_keywords = _extract_metric_keywords(line_text)
                common_kw = set(ev_keywords).intersection(set(line_keywords))

                line_numbers = _extract_numbers_and_units(line_text)
                if not line_numbers and not ev_numbers:
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

                # If numbers exist, check single-line full value match
                if ev_numbers:
                    matched_all_numbers = True
                    found_match = False
                    for ev_num, ev_unit, ev_raw in ev_numbers:
                        num_found_in_line = False
                        for l_num, l_unit, l_raw in line_numbers:
                            if _is_num_match(ev_num, ev_unit, l_num, l_unit, self.rel_tol, self.abs_tol):
                                num_found_in_line = True
                                found_match = True
                                break
                        if not num_found_in_line:
                            matched_all_numbers = False

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

        # 3.3 Multi-line aggregation mode (when single line did not match all numbers)
        if ev_numbers:
            num_hits_by_report: dict[str, set[int]] = {}
            all_hit_num_indices: set[int] = set()

            for num_idx, (ev_num, ev_unit, ev_raw) in enumerate(ev_numbers):
                for role_key in SEVEN_REPORT_KEYS:
                    report_body = str(seven_reports.get(role_key, "") or "")
                    if not report_body.strip():
                        continue
                    matched_in_report = False
                    for line in report_body.splitlines():
                        line_text = line.strip()
                        if not line_text:
                            continue
                        line_keywords = _extract_metric_keywords(line_text)
                        common_kw = set(ev_keywords).intersection(set(line_keywords))
                        # Each hit line must share >= 1 keyword with the evidence sentence
                        if ev_keywords and not common_kw:
                            continue
                        line_numbers = _extract_numbers_and_units(line_text)
                        for l_num, l_unit, l_raw in line_numbers:
                            if _is_num_match(ev_num, ev_unit, l_num, l_unit, self.rel_tol, self.abs_tol):
                                num_hits_by_report.setdefault(role_key, set()).add(num_idx)
                                all_hit_num_indices.add(num_idx)
                                matched_in_report = True
                                break
                        if matched_in_report:
                            break

            total_nums = len(ev_numbers)
            # Check single-report multi-line aggregation first
            for role_key in SEVEN_REPORT_KEYS:
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
            if len(all_hit_num_indices) == total_nums:
                matched_roles = [r for r in SEVEN_REPORT_KEYS if r in num_hits_by_report and num_hits_by_report[r]]
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
        if ev_numbers and ev_keywords:
            for role_key in SEVEN_REPORT_KEYS:
                report_body = str(seven_reports.get(role_key, "") or "")
                if not report_body.strip():
                    continue
                for line in report_body.splitlines():
                    line_text = line.strip()
                    if not line_text:
                        continue
                    line_keywords = _extract_metric_keywords(line_text)
                    common_kw = set(ev_keywords).intersection(set(line_keywords))
                    # Match specific metric keywords (avoid generic tokens triggering false contradiction)
                    metric_overlap = [kw for kw in common_kw if kw in {
                        "毛利率", "毛利", "净利率", "净利润", "净利", "营收", "收入",
                        "roe", "eps", "pe", "pb", "m2", "cpi", "ppi", "gdp", "lpr",
                        "主力", "净流入", "净流出", "降息", "降准", "关税", "量比", "换手率"
                    }]
                    if not metric_overlap:
                        continue
                    line_numbers = _extract_numbers_and_units(line_text)
                    for num_idx, (ev_num, ev_unit, ev_raw) in enumerate(ev_numbers):
                        if num_idx in all_hit_num_indices:
                            continue
                        for l_num, l_unit, l_raw in line_numbers:
                            if ev_unit == l_unit and ev_unit in {"%", "元", "股"}:
                                diff_pct = abs(abs(ev_num) - abs(l_num)) / (abs(l_num) + 1e-9)
                                if diff_pct > 0.05:
                                    contradicted_candidate = (
                                        role_key,
                                        f"在 {role_key} 中关键词 '{', '.join(metric_overlap)}' 数据冲突: 证据声称 {ev_raw}，报告记录为 {l_raw}",
                                    )
                                    break
                        if contradicted_candidate:
                            break
                if contradicted_candidate:
                    break

        if contradicted_candidate:
            return {
                "raw": raw_text,
                "claim_id": claim_id,
                "matched_role": contradicted_candidate[0],
                "matched_source": contradicted_candidate[0].replace("_report", ""),
                "status": STATUS_CONTRADICTED,
                "is_fatal": False,
                "details": contradicted_candidate[1],
            }

        # 4. Check market_data_context if provided
        if isinstance(market_data_context, Mapping):
            # Check quotes, indicators, fund_flow_evidence
            md_str = str(market_data_context)
            if raw_text in md_str:
                return {
                    "raw": raw_text,
                    "claim_id": claim_id,
                    "matched_role": "market_data_context",
                    "matched_source": "market_data_context",
                    "status": STATUS_VERIFIED,
                    "is_fatal": False,
                    "details": "在 market_data_context 中找到匹配数据",
                }

        # 5. Unsupported
        return {
            "raw": raw_text,
            "claim_id": claim_id,
            "matched_role": None,
            "matched_source": None,
            "status": STATUS_UNSUPPORTED,
            "is_fatal": False,
            "details": "未在七份分析师报告或市场数据上下文中找到该事实或数据支撑",
        }

    def evaluate_claims(
        self,
        claims: Sequence[Mapping[str, Any]],
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None = None,
        analysis_baseline_date: str | None = None,
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
                ver_res = self.evaluate_single_evidence(
                    raw_evidence=ev_str,
                    seven_reports=seven_reports,
                    market_data_context=market_data_context,
                    analysis_baseline_date=analysis_baseline_date,
                    claim_id=cid,
                )
                results.append(ver_res)

        return results

    def evaluate_challenges(
        self,
        challenges: Sequence[Mapping[str, Any]],
        seven_reports: Mapping[str, str],
        market_data_context: Mapping[str, Any] | None = None,
        analysis_baseline_date: str | None = None,
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
                ver_res = self.evaluate_single_evidence(
                    raw_evidence=ev_str,
                    seven_reports=seven_reports,
                    market_data_context=market_data_context,
                    analysis_baseline_date=analysis_baseline_date,
                    claim_id=chid,
                )
                ver_items.append(ver_res)

            verified_items = [v for v in ver_items if v.get("status") == STATUS_VERIFIED]
            contradicted_items = [v for v in ver_items if v.get("status") == STATUS_CONTRADICTED]
            unavail_items = [
                v for v in ver_items
                if v.get("status") == STATUS_SOURCE_UNAVAILABLE or v.get("is_fatal")
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
    ) -> dict[str, dict[str, Any]]:
        """Aggregate evidence verification by claim and compute coverage ratio and adoption decisions."""
        return aggregate_claim_evidence(claims=claims, claims_verification=claims_verification)


def aggregate_claim_evidence(
    claims: Sequence[Mapping[str, Any]] | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
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
        }
    """
    claims_list = list(claims or [])
    ver_list = list(claims_verification or [])

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

        verified_items = [v for v in claim_ver_items if v.get("status") == STATUS_VERIFIED]
        unsupported_items = [v for v in claim_ver_items if v.get("status") == STATUS_UNSUPPORTED]
        contradicted_items = [v for v in claim_ver_items if v.get("status") == STATUS_CONTRADICTED]
        source_unavail_items = [
            v for v in claim_ver_items
            if v.get("status") == STATUS_SOURCE_UNAVAILABLE or v.get("is_fatal")
        ]

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

        if contradicted_count > 0:
            decision = DECISION_REJECT
            reason = f"存在 {contradicted_count} 条与报告事实冲突/前视偏差证据 (contradicted)"
        elif source_unavail_count > 0:
            decision = DECISION_REJECT
            reason = f"存在 {source_unavail_count} 条引用不可用数据源的严重幻觉证据 (source_unavailable)"
        elif total_count == 0 or verified_count == 0:
            decision = DECISION_REJECT
            reason = "未提供有效证据或全部证据未获验证 (unsupported)"
        elif verified_count == total_count:
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
        badge = badge_map.get(decision, "待核验")
        cov = sum_info.get("coverage", 0.0)
        counts = sum_info.get("counts", {})
        total = counts.get("total", 0)
        verified = counts.get("verified", 0)
        reason = sum_info.get("reason", "")

        lines.append(f"{prefix}{cid} [{status}] {speaker}{stance_str}: {summary_text}")
        lines.append(f"  * 核验评级: 【{badge}】 覆盖率={cov:.1%} ({verified}/{total} verified) | 规则判定: {decision}")
        lines.append(f"  * 判定说明: {reason}")

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


def extract_and_validate_manager_verdict(
    raw_response: str,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
    claims: Sequence[Mapping[str, Any]] | None = None,
    challenges: Sequence[Mapping[str, Any]] | None = None,
    challenges_verification: Sequence[Mapping[str, Any]] | None = None,
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
    if isinstance(raw_dispute_map, list):
        for item in raw_dispute_map:
            if isinstance(item, Mapping):
                dp = str(item.get("data_point") or "").strip()
                b_interp = str(item.get("bull_interpretation") or "").strip()
                be_interp = str(item.get("bear_interpretation") or "").strip()
                ev_dec = str(item.get("evidence_decision") or "").strip()
                w_raw = str(item.get("winner") or "").strip()
                dispute_map.append({
                    "data_point": dp,
                    "bull_interpretation": b_interp,
                    "bear_interpretation": be_interp,
                    "evidence_decision": ev_dec,
                    "winner": normalize_winner(w_raw),
                })

    # ── Deterministic Claim Evidence Summary Computation ──────────────────
    claim_evidence_summary: dict[str, dict[str, Any]] = {}
    if claims is not None or claims_verification is not None:
        claim_evidence_summary = aggregate_claim_evidence(
            claims=claims, claims_verification=claims_verification
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
                if cnt.get("contradicted", 0) > 0:
                    failed_checks.append(f"裁决采纳了存在事实冲突/前视偏差的矛盾 claim: {cid}")
                elif cnt.get("source_unavailable", 0) > 0:
                    failed_checks.append(f"裁决采纳了不可用数据源的严重幻觉 claim: {cid}")
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
                if cnt.get("contradicted", 0) > 0:
                    failed_checks.append(f"部分采纳列表中包含了存在事实冲突/前视偏差的矛盾 claim: {cid}")
                elif cnt.get("source_unavailable", 0) > 0:
                    failed_checks.append(f"部分采纳列表中包含了不可用数据源的严重幻觉 claim: {cid}")
                elif cnt.get("verified", 0) == 0 or cnt.get("total", 0) == 0:
                    failed_checks.append(f"部分采纳列表中包含了全部证据未获验证 (unsupported) 的 claim: {cid}")
                elif cov < MIN_COVERAGE_THRESHOLD and not math.isclose(cov, 2 / 3, abs_tol=1e-3):
                    failed_checks.append(f"部分采纳列表中包含了证据覆盖率不足 ({cov:.1%} < 67%) 的 claim: {cid}")

        # Check prose consistency against claim verification
        for cid, s in claim_evidence_summary.items():
            cov = s.get("coverage", 0.0)
            dec = s.get("decision")
            if dec != DECISION_ADOPT or cov < 1.0:
                pattern = re.compile(
                    rf"{re.escape(cid)}[^\n。；]*?证据充分|证据充分[^\n。；]*?{re.escape(cid)}",
                    re.IGNORECASE,
                )
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
            if item.get("is_fatal") or item.get("status") == STATUS_SOURCE_UNAVAILABLE
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
    }

