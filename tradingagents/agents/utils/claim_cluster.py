from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Mapping, Sequence

from tradingagents.agents.utils.debate_utils import normalize_text

logger = logging.getLogger(__name__)

CLUSTER_TYPE_PRICE_SHOCK = "price_shock"
CLUSTER_TYPE_FUNDAMENTALS = "fundamentals"
CLUSTER_TYPE_MACRO_POLICY = "macro_policy"
CLUSTER_TYPE_SENTIMENT_NEWS = "sentiment_news"
CLUSTER_TYPE_UNSUPPORTED = "unsupported"

_PRICE_SHOCK_KEYWORDS = frozenset({
    "收盘", "收盘价", "开盘", "开盘价", "最高价", "最低价", "现价", "股价", "价格",
    "突破", "跌破", "阻力位", "支撑位", "均线", "ma5", "ma10", "ma20", "ma60",
    "ma", "ema", "vwma", "vwap", "k线", "实体", "阳线", "阴线", "跳空", "缺口",
    "涨幅", "跌幅", "涨跌幅", "涨停", "跌停", "当日涨幅", "当日跌幅", "振幅", "涨跌", "冲高", "回落", "连板",
    "成交量", "成交额", "换手率", "换手", "量比", "放量", "缩量", "地量", "天量", "量价", "量能",
    "macd", "kdj", "rsi", "boll", "布林", "atr", "dif", "dea", "金叉", "死叉", "顶背离", "底背离", "超买", "超卖", "多头排列", "空头排列",
    "主力资金", "主力净流入", "主力净额", "资金净流入", "资金流出", "主力流出", "超大单", "大单", "中单", "小单", "全单",
    "主力净买入", "主力建仓", "主力吸筹", "主力出货", "北向资金", "两融", "融资买入", "资金面", "长阳",
})

_FUNDAMENTALS_KEYWORDS = frozenset({
    "营收", "营业收入", "收入", "净利润", "扣非净利润", "扣非", "归母净利润", "利润", "毛利", "毛利率", "净利率",
    "roe", "roa", "eps", "每股收益", "资产负债率", "负债率", "现金流", "自由现金流", "fcf", "经营性现金流",
    "资本开支", "capex", "研发费用", "研发投入", "研发", "在手订单", "订单", "存货", "应收账款", "周转率", "周转天数",
    "产能", "产能利用率", "减值", "商誉", "分红", "股息率", "股息", "pe", "pb", "ps", "估值", "市盈率", "市净率",
    "净资产", "总资产", "中报", "年报", "季报", "一季报", "三季报", "业绩预告", "业绩", "财报",
})

_MACRO_POLICY_KEYWORDS = frozenset({
    "降息", "降准", "加息", "央行", "lpr", "m2", "cpi", "ppi", "gdp", "财政赤字", "赤字", "汇率",
    "美联储", "关税", "补贴", "以旧换新", "政策红利", "货币政策", "产业政策", "外需", "内需", "美债",
    "美债收益率", "美元", "原油", "油价", "黄金", "大宗商品", "铜价", "宏观", "货币",
})

_SENTIMENT_NEWS_KEYWORDS = frozenset({
    "舆情", "情绪", "散户情绪", "看多占比", "看空占比", "机构调研", "行业新政", "诉讼", "重组", "获批",
    "临床", "专利", "合作", "license-out", "突发事件", "传闻", "舆论", "热搜", "新闻",
})

_DATE_PATTERN = re.compile(r"\b(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{4}Q[1-4]|\d{4}H[12]|\d{4}年报|\d{4}中报)\b")
_SYMBOL_PATTERN = re.compile(r"\b(\d{6})(?:\.(?:SH|SZ|BJ))?\b", re.IGNORECASE)
_NUMERIC_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?(?:%|亿元|万元|万手|手|元|bp|倍)?")


def normalize_symbol(symbol: str | None) -> str:
    """Extract and normalize 6-digit stock symbol or entity string."""
    if not symbol:
        return "default_symbol"
    text = str(symbol).strip()
    match = _SYMBOL_PATTERN.search(text)
    if match:
        return match.group(1)
    cleaned = re.sub(r"[^\w一-鿿]+", "", text)
    return cleaned or "default_symbol"


def normalize_date_period(date_str: str | None) -> str:
    """Normalize date or period string."""
    if not date_str:
        return "default_date"
    text = str(date_str).strip()
    match = _DATE_PATTERN.search(text)
    if match:
        raw_match = match.group(1)
        return raw_match.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    return text.replace("/", "-")


def compute_cluster_id(
    cluster_type: str,
    symbol: str | None = None,
    date: str | None = None,
) -> str:
    """Compute deterministic, stable hash-based cluster identifier."""
    norm_type = (cluster_type or CLUSTER_TYPE_UNSUPPORTED).strip().lower()
    if norm_type == CLUSTER_TYPE_UNSUPPORTED:
        return ""
    norm_sym = normalize_symbol(symbol)
    norm_date = normalize_date_period(date)
    raw_key = f"{norm_type}:{norm_sym}:{norm_date}"
    hash_str = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]
    return f"cluster_{norm_type}_{hash_str}"


def classify_evidence_text(
    evidence_text: str,
    fallback_symbol: str | None = None,
    fallback_date: str | None = None,
) -> tuple[str, str, str]:
    """Classify evidence string into domain cluster type and extract symbol/date context."""
    text = str(evidence_text or "").strip()
    if not text:
        return CLUSTER_TYPE_UNSUPPORTED, "", ""

    lower_text = text.lower()
    sym_match = _SYMBOL_PATTERN.search(text)
    detected_symbol = sym_match.group(1) if sym_match else normalize_symbol(fallback_symbol)

    date_match = _DATE_PATTERN.search(text)
    detected_date = date_match.group(1) if date_match else normalize_date_period(fallback_date)

    # Check keyword memberships
    has_numbers = bool(_NUMERIC_PATTERN.search(text))
    has_price = any(kw in lower_text for kw in _PRICE_SHOCK_KEYWORDS)
    has_fund = any(kw in lower_text for kw in _FUNDAMENTALS_KEYWORDS)
    has_macro = any(kw in lower_text for kw in _MACRO_POLICY_KEYWORDS)
    has_sentiment = any(kw in lower_text for kw in _SENTIMENT_NEWS_KEYWORDS)

    if not (has_price or has_fund or has_macro or has_sentiment or has_numbers):
        return CLUSTER_TYPE_UNSUPPORTED, detected_symbol, detected_date

    if has_price:
        return CLUSTER_TYPE_PRICE_SHOCK, detected_symbol, detected_date
    if has_fund:
        return CLUSTER_TYPE_FUNDAMENTALS, detected_symbol, detected_date
    if has_macro:
        return CLUSTER_TYPE_MACRO_POLICY, detected_symbol, detected_date
    if has_sentiment:
        return CLUSTER_TYPE_SENTIMENT_NEWS, detected_symbol, detected_date

    # If only numbers present without specific keywords
    return CLUSTER_TYPE_PRICE_SHOCK, detected_symbol, detected_date


def compute_evidence_id(
    cluster_id: str,
    raw_evidence: str,
    index: int = 1,
) -> str:
    """Generate deterministic evidence ID bound to cluster and normalized text."""
    norm_ev = normalize_text(raw_evidence)
    ev_hash = hashlib.sha256(f"{cluster_id}:{norm_ev}".encode("utf-8")).hexdigest()[:10]
    return f"ev_{ev_hash}_{index}"


def extract_evidence_ids(
    evidence_items: Sequence[Any] | None,
    cluster_id: str,
) -> list[str]:
    """Extract deterministic evidence IDs for a list of evidence strings."""
    if not evidence_items or not cluster_id:
        return []
    ids: list[str] = []
    for idx, item in enumerate(evidence_items, start=1):
        text = str(item or "").strip()
        if text:
            ids.append(compute_evidence_id(cluster_id, text, idx))
    return ids


def _build_verification_index(
    claims_verification: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]], set[str], set[str], bool]:
    if claims_verification is None:
        return set(), set(), set(), set(), False

    verified_cid_raw: set[tuple[str, str]] = set()
    verified_cid_norm: set[tuple[str, str]] = set()
    verified_raw: set[str] = set()
    verified_norm: set[str] = set()

    if isinstance(claims_verification, Sequence):
        for item in claims_verification:
            if not isinstance(item, Mapping):
                continue
            st = str(item.get("status") or "").strip().lower()
            is_fatal = bool(item.get("is_fatal", False))
            if st in {"verified", "pass", "ok"} and not is_fatal and st not in {
                "contradicted",
                "unsupported",
                "source_unavailable",
                "failed",
                "unavailable",
                "error",
                "missing",
            }:
                cid = str(item.get("claim_id") or "").strip()
                raw = str(item.get("raw") or item.get("evidence") or "").strip()
                if raw:
                    norm = normalize_text(raw)
                    if cid:
                        verified_cid_raw.add((cid, raw))
                        verified_cid_norm.add((cid, norm))
                    verified_raw.add(raw)
                    verified_norm.add(norm)
    elif isinstance(claims_verification, Mapping):
        for cid, info in claims_verification.items():
            if isinstance(info, Mapping):
                for raw in (info.get("verified_evidence") or []):
                    r_str = str(raw).strip()
                    if r_str:
                        norm = normalize_text(r_str)
                        verified_cid_raw.add((str(cid).strip(), r_str))
                        verified_cid_norm.add((str(cid).strip(), norm))
                        verified_raw.add(r_str)
                        verified_norm.add(norm)

    return verified_cid_raw, verified_cid_norm, verified_raw, verified_norm, True


def _is_evidence_verified(
    claim_id: str | None,
    raw_evidence: str,
    ver_index: tuple[set[tuple[str, str]], set[tuple[str, str]], set[str], set[str], bool],
) -> bool:
    v_cid_raw, v_cid_norm, v_raw, v_norm, is_active = ver_index
    if not is_active:
        return True
    raw_str = str(raw_evidence).strip()
    if not raw_str:
        return False
    norm_str = normalize_text(raw_str)
    cid_str = str(claim_id or "").strip()
    if cid_str:
        if (cid_str, raw_str) in v_cid_raw or (cid_str, norm_str) in v_cid_norm:
            return True
    return raw_str in v_raw or norm_str in v_norm


def assign_claim_cluster(
    claim: Mapping[str, Any],
    symbol: str | None = None,
    date: str | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assign deterministic cluster_id and evidence_ids to a claim object."""
    claim_dict = dict(claim)
    claim_id = str(claim_dict.get("claim_id") or "").strip()
    evidence_list = claim_dict.get("evidence") or []
    if isinstance(evidence_list, str):
        evidence_list = [evidence_list]
    valid_evidence = [str(e).strip() for e in evidence_list if str(e).strip()]

    ver_index = _build_verification_index(claims_verification)
    verified_evidence = [e for e in valid_evidence if _is_evidence_verified(claim_id, e, ver_index)]

    if not valid_evidence:
        claim_dict["cluster_id"] = None
        claim_dict["evidence_ids"] = []
        claim_dict["verified_evidence"] = []
        claim_dict["verified_evidence_ids"] = []
        claim_dict["cluster_status"] = "unsupported"
        return claim_dict

    cluster_votes: dict[str, int] = {}
    cluster_info: dict[str, tuple[str, str]] = {}
    for ev in valid_evidence:
        ctype, sym, dt = classify_evidence_text(ev, fallback_symbol=symbol, fallback_date=date)
        if ctype != CLUSTER_TYPE_UNSUPPORTED:
            cluster_votes[ctype] = cluster_votes.get(ctype, 0) + 1
            cluster_info[ctype] = (sym, dt)

    if not cluster_votes:
        claim_dict["cluster_id"] = None
        claim_dict["evidence_ids"] = []
        claim_dict["verified_evidence"] = []
        claim_dict["verified_evidence_ids"] = []
        claim_dict["cluster_status"] = "unsupported"
        return claim_dict

    primary_type = max(cluster_votes.items(), key=lambda x: x[1])[0]
    sym, dt = cluster_info[primary_type]
    cid = compute_cluster_id(primary_type, symbol=sym, date=dt)
    ev_ids = extract_evidence_ids(valid_evidence, cid)
    ver_ev_ids = extract_evidence_ids(verified_evidence, cid)

    claim_dict["cluster_id"] = cid
    claim_dict["evidence_ids"] = ev_ids
    claim_dict["verified_evidence"] = verified_evidence
    claim_dict["verified_evidence_ids"] = ver_ev_ids
    claim_dict["cluster_type"] = primary_type
    claim_dict["cluster_status"] = "clustered"
    return claim_dict


def cluster_claims(
    claims: Sequence[Mapping[str, Any]],
    symbol: str | None = None,
    date: str | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Enrich all claims with deterministic cluster_id and evidence_ids."""
    return [
        assign_claim_cluster(c, symbol=symbol, date=date, claims_verification=claims_verification)
        for c in claims
    ]


def _normalize_stance(raw_stance: str | None, speaker: str | None = None) -> str:
    s = (raw_stance or "").strip().lower()
    if s in {"bull", "bullish", "long", "buy", "多", "多头", "看多", "偏多"}:
        return "bull"
    if s in {"bear", "bearish", "short", "sell", "空", "空头", "看空", "偏空"}:
        return "bear"
    sp = (speaker or "").strip().lower()
    if "bull" in sp:
        return "bull"
    if "bear" in sp:
        return "bear"
    return "neutral"


def _extract_analyst_count(
    claims: Sequence[Mapping[str, Any]],
    reports: Mapping[str, str] | None = None,
) -> int:
    speakers = set()
    for c in claims:
        sp = str(c.get("speaker") or c.get("speaker_key") or "").strip()
        if sp:
            speakers.add(sp)
    if reports:
        for rk, rbody in reports.items():
            if str(rbody or "").strip():
                speakers.add(rk)
    return len(speakers) if speakers else len(claims)


def _build_clusters_map(
    enriched_claims: Sequence[Mapping[str, Any]],
    claims_verification_active: bool = False,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    clusters_map: dict[str, dict[str, Any]] = {}
    unsupported_ids: list[str] = []

    for c in enriched_claims:
        cid = c.get("cluster_id")
        claim_id = str(c.get("claim_id") or "").strip()
        if not cid or c.get("cluster_status") == "unsupported":
            if claim_id:
                unsupported_ids.append(claim_id)
            continue

        stance = _normalize_stance(c.get("stance"), c.get("speaker") or c.get("speaker_key"))
        if cid not in clusters_map:
            clusters_map[cid] = {
                "cluster_id": cid,
                "cluster_type": c.get("cluster_type", "unknown"),
                "claims": [],
                "speakers": set(),
                "stances": set(),
                "evidence_ids": set(),
                "verified_evidence_ids": set(),
                "direction_votes": {"bull": 0, "bear": 0, "neutral": 0},
            }
        cluster_entry = clusters_map[cid]
        if claim_id:
            cluster_entry["claims"].append(claim_id)
        sp = str(c.get("speaker") or c.get("speaker_key") or "").strip()
        if sp:
            cluster_entry["speakers"].add(sp)
        cluster_entry["stances"].add(stance)
        for evid in (c.get("evidence_ids") or []):
            cluster_entry["evidence_ids"].add(evid)
        for evid in (c.get("verified_evidence_ids") or []):
            cluster_entry["verified_evidence_ids"].add(evid)

        # Every cluster casts at most ONE vote per direction
        cluster_entry["direction_votes"][stance] = 1

    return clusters_map, unsupported_ids


def tally_cluster_votes(
    claims: Sequence[Mapping[str, Any]] | None = None,
    reports: Mapping[str, str] | None = None,
    claims_verification: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    symbol: str | None = None,
    trade_date: str | None = None,
    horizon: str | None = None,
) -> dict[str, Any]:
    """Tally directional votes across deduplicated evidence clusters."""
    claims_list = list(claims or [])
    enriched_claims = cluster_claims(
        claims_list,
        symbol=symbol,
        date=trade_date,
        claims_verification=claims_verification,
    )

    analyst_count = _extract_analyst_count(claims_list, reports)
    has_ver = claims_verification is not None
    clusters_map, unsupported_ids = _build_clusters_map(
        enriched_claims,
        claims_verification_active=has_ver,
    )

    bull_cluster_count = sum(1 for cl in clusters_map.values() if cl["direction_votes"].get("bull"))
    bear_cluster_count = sum(1 for cl in clusters_map.values() if cl["direction_votes"].get("bear"))
    neutral_cluster_count = sum(1 for cl in clusters_map.values() if cl["direction_votes"].get("neutral"))
    independent_cluster_count = len(clusters_map)

    if has_ver:
        verified_evidence_count = sum(len(cl["verified_evidence_ids"]) for cl in clusters_map.values())
    else:
        verified_evidence_count = sum(len(cl["evidence_ids"]) for cl in clusters_map.values())

    active_dir_total = bull_cluster_count + bear_cluster_count
    if active_dir_total > 0:
        bull_weight = round(bull_cluster_count / active_dir_total, 4)
        bear_weight = round(bear_cluster_count / active_dir_total, 4)
    else:
        bull_weight = 0.0
        bear_weight = 0.0

    return {
        "analyst_count": analyst_count,
        "independent_cluster_count": independent_cluster_count,
        "verified_evidence_count": verified_evidence_count,
        "bull_cluster_count": bull_cluster_count,
        "bear_cluster_count": bear_cluster_count,
        "neutral_cluster_count": neutral_cluster_count,
        "direction_cluster_counts": {
            "bull": bull_cluster_count,
            "bear": bear_cluster_count,
            "neutral": neutral_cluster_count,
        },
        "cluster_weights": {
            "bull": bull_weight,
            "bear": bear_weight,
            "neutral": 1.0 - (bull_weight + bear_weight) if active_dir_total == 0 else 0.0,
        },
        "unsupported_claim_ids": unsupported_ids,
        "clusters": [
            {
                "cluster_id": cl["cluster_id"],
                "cluster_type": cl["cluster_type"],
                "claims": cl["claims"],
                "speakers": sorted(cl["speakers"]),
                "stances": sorted(cl["stances"]),
                "evidence_count": len(cl["verified_evidence_ids"]) if has_ver else len(cl["evidence_ids"]),
                "direction_votes": cl["direction_votes"],
            }
            for cl in clusters_map.values()
        ],
    }


def format_claim_cluster_summary_for_prompt(
    metrics: Mapping[str, Any] | None,
    language: str = "zh",
) -> str:
    """Format a concise, human/LLM-consumable summary of claim evidence cluster metrics."""
    if not metrics:
        return ""
    analyst_count = metrics.get("analyst_count", 0)
    independent_cluster_count = metrics.get("independent_cluster_count", 0)
    verified_evidence_count = metrics.get("verified_evidence_count", 0)
    bull_clusters = metrics.get("bull_cluster_count", 0)
    bear_clusters = metrics.get("bear_cluster_count", 0)
    neutral_clusters = metrics.get("neutral_cluster_count", 0)
    cluster_weights = metrics.get("cluster_weights", {}) or {}
    bull_weight = cluster_weights.get("bull", 0.0)
    bear_weight = cluster_weights.get("bear", 0.0)

    if language == "en":
        lines = [
            "### Claim Evidence Cluster Metrics (Deduplication Summary)",
            f"- analyst_count: {analyst_count} (explanatory context only; do NOT use directly as voting weight)",
            f"- independent_cluster_count: {independent_cluster_count} (bull clusters={bull_clusters}, bear clusters={bear_clusters}, neutral={neutral_clusters})",
            f"- verified_evidence_count: {verified_evidence_count} (only factual verified evidence counted)",
            f"- directional_cluster_weights: Bull={bull_weight:.1%}, Bear={bear_weight:.1%}",
        ]
        clusters = metrics.get("clusters") or []
        if clusters:
            lines.append("- evidence_clusters:")
            for cl in clusters:
                c_id = cl.get("cluster_id")
                c_type = cl.get("cluster_type")
                claims_str = ", ".join(cl.get("claims") or []) or "none"
                speakers_str = ", ".join(cl.get("speakers") or []) or "none"
                votes = cl.get("direction_votes") or {}
                lines.append(f"  * [{c_id}] type={c_type}, claims=[{claims_str}], speakers=[{speakers_str}], votes={votes}")
        return "\n".join(lines)

    lines = [
        "### Claim 证据簇与去重计票全景 (claim_cluster_metrics)",
        f"- 参与分析师人数 (analyst_count): {analyst_count}（仅作解释性参考，严禁直接作为多空投票权重）",
        f"- 独立证据簇数 (independent_cluster_count): {independent_cluster_count}（多头独立簇={bull_clusters}, 空头独立簇={bear_clusters}, 中性={neutral_clusters}）",
        f"- 真实核验有效证据数 (verified_evidence_count): {verified_evidence_count}（仅统计经核验通过证据，矛盾/未支撑项不计入）",
        f"- 证据簇方向权重 (cluster_weights): 多头权重={bull_weight:.1%}, 空头权重={bear_weight:.1%}",
    ]
    clusters = metrics.get("clusters") or []
    if clusters:
        lines.append("- 独立证据簇明细:")
        for cl in clusters:
            c_id = cl.get("cluster_id")
            c_type = cl.get("cluster_type")
            claims_str = ", ".join(cl.get("claims") or []) or "无"
            speakers_str = ", ".join(cl.get("speakers") or []) or "无"
            votes = cl.get("direction_votes") or {}
            lines.append(f"  * 【{c_id}】 类型={c_type} | 关联 claim=[{claims_str}] | 发言分析师=[{speakers_str}] | 方向投票={votes}")
    return "\n".join(lines)
