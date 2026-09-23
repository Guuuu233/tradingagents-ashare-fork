"""PriceRef registry and per-reference basis/provenance audit layer (DAV-1142 / DAV-1198 / DAV-1224).

Pure bypass audit — ZERO effect on decision, target/stop, H1b eligibility, or any
production behavior. It extracts price references from report texts into a
per-run registry, assigns deterministic canonical basis labels, records
provenance, and emits side-channel fields on the final state:

- ``state["price_refs"]``           — registry entries (value/basis/source/as_of/lineage)
- ``state["price_basis_gaps"]``     — missing basis / missing as_of / basis mismatch gaps
- ``state["price_basis_validation"]``— preview-only validation verdict (never fail-closed)

Contract rules (DAV-1142):

- basis reuses the existing canonical short labels (``vendor_qfq`` / ``raw`` /
  ``pit_raw`` / ``unspecified``) — no new ``disclosure_raw``/``derived`` basis.
- known typed disclosures are deterministically labelled: 大宗交易/龙虎榜 → ``raw``;
  增持/减持/回购/定增·增发·发行 → ``pit_raw``. Untyped news prices NEVER default to raw.
- technical market reports inherit ``vendor_qfq`` (their data channel is known).
- models cannot self-certify basis: a model-emitted price that cannot be traced
  back to a registry entry stays ``unspecified``; one matching a registered
  vendor_qfq value inherits it via registry back-reference.
- derived/converted prices keep ``derived_from`` / ``conversion`` lineage;
  "derived" is provenance, never a basis. A raw→qfq conversion missing factor
  provenance, or with ``factor_as_of`` later than the run cutoff, previews invalid.

DAV-1224 semantic-contract tightening (five levers, validated on the frozen
DAV-1222 corpus by DAV-1225):

- C1 extraction guards: non-price units/contexts (亿/万/%/倍/股/手/日/月/年,
  date fragments, Markdown list numbers, JSON blobs), foreign-currency quotes,
  and per-share financial indicators never produce price coordinates.
- C2 typed disclosure is verdict-driven: a value is a disclosure price only when
  it is the price of that disclosure kind (commodity 大宗 / position 增减持 /
  note·share-capital 发行 / 逆回购 / coordinate price in a repurchase context
  are rejected). ``invalid_conversion`` requires real basis-conversion
  semantics (复权/前复权/不复权/因子); 「折合/折算」 as valuation arithmetic no
  longer counts.
- C3 ``derived_estimate`` semantic role: a value bound (same sub-clause, ≤25
  chars before the number) to valuation-arithmetic vocabulary is not a
  coordinate — it carries no decision-driving basis/as_of accountability, may
  not back an executable level, and does not join cross-basis mixing checks.
- C4 shared executable-level parser: registry and gate share one anchor
  vocabulary (目标价/目标位/止盈/止损/入场/进场/买入/卖出/建仓/开仓/出场/加仓/
  减仓) and one false-level filter (list ordinals, percentages, share counts,
  dates).
- C5 strict source-backed bridging: an ``unspecified`` ref may inherit
  ``vendor_qfq`` from the run's own market data only when its context names a
  concrete field (indicator name, explicit date + OHLC word, or limit-up/down)
  and the value matches exactly. Bare value equality never bridges.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_PIT_RAW,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
)

# ---------------------------------------------------------------------------
# Canonical basis vocabulary (re-exported for consumers/tests)
# ---------------------------------------------------------------------------

CANONICAL_PRICE_BASES = frozenset(
    (
        PRICE_BASIS_VENDOR_QFQ,
        PRICE_BASIS_RAW,
        PRICE_BASIS_PIT_RAW,
        PRICE_BASIS_UNSPECIFIED,
    )
)

# Reports scanned by the audit. market/volume_price are built on the vendor
# qfq data channel, so their unmarked prices inherit vendor_qfq. All other
# reports are model-authored: their prices must trace back to the registry
# (back-reference) or stay unspecified.
TECHNICAL_REPORT_FIELDS = ("market_report", "volume_price_report")
MODEL_REPORT_FIELDS = (
    "news_report",
    "fundamentals_report",
    "sentiment_report",
    "macro_report",
    "smart_money_report",
    "game_theory_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
REPORT_FIELDS = TECHNICAL_REPORT_FIELDS + MODEL_REPORT_FIELDS

# ---------------------------------------------------------------------------
# Typed disclosure vocabulary → deterministic basis
# ---------------------------------------------------------------------------

# keyword -> disclosure_type. Order matters only for longest-match preference.
TYPED_DISCLOSURE_KEYWORDS: Mapping[str, str] = {
    "大宗交易": "block_trade",
    "大宗": "block_trade",
    "龙虎榜": "dragon_tiger_list",
    "增持": "shareholder_increase",
    "减持": "shareholder_decrease",
    "回购": "repurchase",
    "定增": "private_placement",
    "增发": "private_placement",
    "发行": "issuance",
}

# Deterministic disclosure_type -> canonical basis.
# Contemporaneous exchange-published quotes (block trades, dragon-tiger) are raw;
# point-in-time holder-transaction / issuance disclosures are pit_raw.
DISCLOSURE_TYPE_BASIS: Mapping[str, str] = {
    "block_trade": PRICE_BASIS_RAW,
    "dragon_tiger_list": PRICE_BASIS_RAW,
    "shareholder_increase": PRICE_BASIS_PIT_RAW,
    "shareholder_decrease": PRICE_BASIS_PIT_RAW,
    "repurchase": PRICE_BASIS_PIT_RAW,
    "private_placement": PRICE_BASIS_PIT_RAW,
    "issuance": PRICE_BASIS_PIT_RAW,
}

# Words anchoring a price to the technical (qfq) coordinate system. A raw/pit_raw
# price referenced next to these inside the same report is a candidate mismatch.
COORDINATE_KEYWORDS = (
    "现价",
    "最新价",
    "当前价",
    "支撑",
    "压力",
    "阻力",
    "锚",
    "均线",
    "技术位",
    "止损",
    "止盈",
    "目标价",
)

# Markers that a price was derived / converted from another price.
DERIVED_KEYWORDS = ("换算", "折算", "折合", "复权因子", "前复权", "除权")

_PRICE_KEYWORD_PATTERN = re.compile(
    r"(?:现价|最新价|当前价|收盘价?|收于|开盘价?|最高|最低|均价|成本价?|"
    r"目标价|止损|止盈|支撑|压力位?|阻力位?|成交价|作价|单价|定增价|"
    r"发行价|回购价|增持价|减持价|投标价|锚定?|报价|每股)"
    r"[^0-9%]{0,8}?(\d+(?:\.\d+)?)(?!\s*[%％倍分角]|亿|万|股|手|户|家|次|日|天|年|月)"
)

_BARE_YUAN_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*元(?!\s*[/%％]|/股|吨|克|人|次)"
)

_PER_SHARE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*元/股|每股\s*(\d+(?:\.\d+)?)\s*元"
)

_DATE_PATTERN = re.compile(
    r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?"
)

_FACTOR_PATTERN = re.compile(r"(?:复权)?因子\s*[:：为是]?\s*(\d+(?:\.\d+)?)")

_SENTENCE_SPLIT_PATTERN = re.compile(r"[。；;！!？?\n\r]+")

_VALUE_MATCH_TOLERANCE = 5e-3

# ---------------------------------------------------------------------------
# [C1] Extraction guards — non-price / foreign mention filters
# ---------------------------------------------------------------------------

_UNIT_TAIL = {
    # e.g. 「50%」回溯截出的 5、LaTeX \%
    "percent": re.compile(r"^\s*\d*\\?[%％]"),
    # 「1.8-2.0倍」区间倍数
    "multiple": re.compile(r"^\s*[-~—]?\s*\d*\s*倍"),
    "shares": re.compile(r"^\s*(?:亿|万)?\s*(?:股|手|户|份)"),
    # 时间/日期碎片：含范围写法「1-2周」「3 个月」「10 日 EMA」
    "time": re.compile(
        r"^\s*[-~—]?\s*\d*\s*(?:个)?\s*"
        r"(?:分钟|小时|交易日|日|天|周|个月|月|年|季度|期|次|条|家|人|档|位)"
    ),
    "ratio": re.compile(r"^\s*[:：]"),
}

_PERSHARE_FIN = re.compile(
    r"每股净资产|每股收益|每股派|每股股利|每股现金|每股盈余|"
    r"净资产收益|每股未分配|每股公积金|每股经营"
)
_JSON_BLOB = re.compile(r"MANAGER_VERDICT|\"reason\"|\"confidence\"|\"winner\"|position_pct|JSON")
_DATE_TOKEN = re.compile(r"20\d{2}\s*[-/年.]")

_FOREIGN_CTX = re.compile(
    r"美元|USD|usd|美股|美债|US10Y|纳指|道指|标普|纳斯达克|道琼斯|恒生|港股|港元|"
    r"日元|韩元|欧元|英镑|离岸|LME|COMEX|WTI|布伦特|原油|黄金|伦敦|纽约|外汇|"
    r"比特币|BTC|ETH|联邦基金|美联储|日经|德国DAX|法国CAC|富时|VIX|vix|"
    r"台湾|日经225|韩国|印度|越南|新兴市场|\.KS\b|\.HK\b|\.N\b|\.O\b"
)


def _token_tail_flags(sentence: str, start: int, end: int) -> Optional[str]:
    """数字 token 紧跟的单位/语境 → non-price 子类名；否则 None。"""
    tail = sentence[end:end + 12]
    head = sentence[max(0, start - 12):start]
    if _UNIT_TAIL["percent"].match(tail):
        return "percent"
    if _UNIT_TAIL["multiple"].match(tail):
        return "multiple"
    if _UNIT_TAIL["shares"].match(tail):
        return "shares_or_lots"
    if _UNIT_TAIL["ratio"].match(tail):
        return "ratio_token"
    if _UNIT_TAIL["time"].match(tail):
        # 「10日 EMA」式截断：数字本属于日期/周期
        return "time_or_date_fragment"
    if _DATE_TOKEN.search(sentence[max(0, start - 4):end + 4]):
        return "date_fragment"
    # 每股财务指标必须是 token 级：数字本身处在「每股净资产 X 元 / EPS X」
    # 短语内；不能因句内别处出现「每股净资产」而误伤同句的派生股价。
    if _PERSHARE_FIN.search(head) or re.search(
            r"每股\s*$|EPS\s*(?:约|为)?\s*$|eps\s*(?:约|为)?\s*$", head):
        return "per_share_financial"
    if _PER_SHARE_PATTERN.search(sentence[max(0, start - 8):end + 8]):
        return "per_share_financial"
    # 金额/市值/数量：亿/万紧贴 token（含回溯截断，如「900亿元」截出 90
    # → tail='0亿元'，及区间「300-390亿元」的 '300' → tail='-390亿'）；
    # 真实股价不会被 亿/万 直接修饰。
    if re.match(r"^\s*[-~—]?\s*\d*\s*(?:亿|万)", tail):
        return "amount_or_marketcap"
    return None


def _match_spans(sentence: str, value: float, tol: float = _VALUE_MATCH_TOLERANCE) -> List[Tuple[int, int, str]]:
    """返回 (num_start, num_end, pattern) — 命中该值的所有数字 token 位置。"""
    spans: List[Tuple[int, int, str]] = []
    for m in _PER_SHARE_PATTERN.finditer(sentence):
        num = m.group(1) or m.group(2)
        try:
            if abs(float(num) - value) <= tol:
                gs = m.start(1) if m.group(1) else m.start(2)
                ge = m.end(1) if m.group(1) else m.end(2)
                spans.append((gs, ge, "per_share"))
        except (TypeError, ValueError):
            pass
    for m in _BARE_YUAN_PATTERN.finditer(sentence):
        try:
            if abs(float(m.group(1)) - value) <= tol:
                spans.append((m.start(1), m.end(1), "bare_yuan"))
        except (TypeError, ValueError):
            pass
    for m in _PRICE_KEYWORD_PATTERN.finditer(sentence):
        try:
            if abs(float(m.group(1)) - value) <= tol:
                spans.append((m.start(1), m.end(1), "keyword"))
        except (TypeError, ValueError):
            pass
    if not spans:  # fallback：裸数字定位（如日期截断值）
        for m in re.finditer(r"\d+(?:\.\d+)?", sentence):
            try:
                if abs(float(m.group(0)) - value) <= tol:
                    spans.append((m.start(), m.end(), "bare"))
            except ValueError:
                pass
    return spans


def _is_false_mention(sentence: str, start: int, end: int, value: float) -> Optional[str]:
    """[C1] 返回伪命中子类名；不是伪命中返回 None。"""
    flag = _token_tail_flags(sentence, start, end)
    if flag:
        return flag
    if _JSON_BLOB.search(sentence) and "元" not in sentence:
        return "json_or_prob_token"
    if _FOREIGN_CTX.search(sentence):
        return "foreign_or_commodity"
    return None


# ---------------------------------------------------------------------------
# [C2] Typed-disclosure verdict rules — 判定对象是「该值是否该类披露价」
# ---------------------------------------------------------------------------

def _occurrences(ctx: str, kw: str) -> List[int]:
    return [m.start() for m in re.finditer(re.escape(kw), ctx)]


def _kw_window(ctx: str, i: int, n: int = 10) -> str:
    return ctx[max(0, i - n):i + n]


_GOODS_CTX = re.compile(
    r"商品|物资|原料|材料|成本|金属|能源|化石|资产|涨价|通胀|"
    r"LME|COMEX|原油|布伦特|黄金|贵金属|铜|铝|镍|上游|采购|BOM|进口"
)
_REAL_BLOCK = re.compile(r"大宗交易|大宗平价|平价大宗|大宗\s*成交")


def _num_windows(ctx: str, value: float, half: int = 15) -> List[str]:
    """返回每个与 value 同值数字 token 的 ±half 字窗口。"""
    wins = []
    for m in re.finditer(r"\d+(?:\.\d+)?", ctx or ""):
        try:
            if abs(float(m.group(0)) - value) <= _VALUE_MATCH_TOLERANCE:
                wins.append(ctx[max(0, m.start() - half):m.end() + half])
        except ValueError:
            pass
    return wins


def classify_typed_disclosure(ref: Mapping[str, Any],
                              stock_name: Optional[str] = None) -> Dict[str, Any]:
    """typed_disclosure ref → verdict true / false / ambiguous。

    判定对象收紧为「该 ref 的值是否该类披露价」：
    - 交易建议里的减持/增持仓位、资金流描述的减持 → false；
    - 大宗商品/材料/成本语境的『大宗』→ false；
    - 票据/中票/股本『发行』、他标的发行价 → false（他标的单列 subtype）；
    - 回购语境中的坐标价（安全垫/站稳/关口）→ false；
    - ambiguous 只留真不可判者。
    """
    ctx = ref.get("context") or ""
    dtype = ref.get("disclosure_type") or ""
    value = ref.get("value")
    wins = _num_windows(ctx, value) if isinstance(value, (int, float)) else []
    verdict = "ambiguous"
    reason = ""
    subtype = ""

    if dtype == "block_trade":
        goods = bool(_GOODS_CTX.search(ctx)) and not _REAL_BLOCK.search(ctx)
        # 值紧邻『大宗交易/平价/成交/折价/席位/接盘』→ 真披露价
        real = any(re.search(r"大宗（交易|平价|成交|折价|溢价|席位|接盘|买入）"
                             r"|（交易|平价|折价|溢价|席位|接盘）[^。]{0,10}大宗", w)
                   for w in wins)
        if real:
            verdict, reason = "true", "数值紧邻大宗交易/平价/成交语义"
        elif goods or any(_GOODS_CTX.search(w) for w in wins):
            verdict, reason = "false", "大宗为商品/材料/成本语境，非披露价"
        elif _REAL_BLOCK.search(ctx):
            # 句中有真大宗语义但该值不紧邻 → 该值不是披露价
            verdict, reason = "false", "句内有大宗交易但该值非披露价"
        else:
            verdict, reason = "ambiguous", "大宗语境无法定夺"

    elif dtype == "issuance":
        occ_fa = _occurrences(ctx, "发行")
        real_fa = [i for i in occ_fa if not (i > 0 and ctx[i - 1] == "突")]
        other = _occurrences(ctx, "定增") + _occurrences(ctx, "增发")
        # 他标的：『发行价/申购』前的主语公司名 ≠ 本标的
        if re.search(r"发行价|申购", ctx) and stock_name:
            subj = None
            m = re.search(r"([一-龥]{2,7})\s*(?:科创板|创业板|主板)?\s*"
                          r"(?:开启|开始)?\s*申购|([一-龥]{2,7})[^。]{0,10}发行价", ctx)
            if m:
                subj = m.group(1) or m.group(2)
            if subj and subj != stock_name and stock_name not in subj:
                verdict, subtype = "false", "other_symbol"
                reason = f"发行价主语为『{subj}』，非本标的 {stock_name}"
        if verdict != "false":
            if re.search(r"票据|中票|中期票据|债券|总股本|股本|成本|已完成|已发行", ctx):
                verdict, reason = "false", "发行属票据/股本/成本语境，非股票发行披露价"
            elif other:
                verdict, reason = "true", "定增/增发为确定披露词"
            elif real_fa:
                w = _kw_window(ctx, real_fa[0])
                if re.search(r"IPO|新股|转债|配售|上市|募资|公开发行|定向", w):
                    verdict, reason = "true", f"发行处于募资/IPO 语境：{w}"
                elif re.search(r"发行价", ctx):
                    verdict, reason = "ambiguous", "发行价但无法确认标的主语"
                else:
                    verdict, reason = "ambiguous", f"发行语境不含明确募资语义：{w}"
            elif occ_fa:
                verdict, reason = "false", "『发行』均为『突发行业』类跨词假命中"
            else:
                verdict, reason = "ambiguous", "未定位关键词"

    elif dtype == "repurchase":
        bad = any((w and re.search(r"逆回购|央行|国债", w)) or
                  (i > 0 and ctx[i - 1] == "逆")
                  for i in _occurrences(ctx, "回购") for w in [_kw_window(ctx, i)])
        # 真披露价：回购价/回购金额/回购均价/以 X 元回购股份 等；
        # 「回购为股价提供 X 元安全垫」中的 X 是坐标价，非披露价。
        price_like = any(re.search(r"回购（价|金额|均价|上限|下限|价格|股份|注销|方案|"
                                   r"拟|计划|公告）|（以|按|不超过）[^。]{0,6}元[^。]{0,4}回购", w)
                         for w in wins)
        coord = any(re.search(r"支撑|站稳|安全垫|关口|一线", w) for w in wins)
        if bad:
            verdict, reason = "false", "逆回购/央行/国债回购语境"
        elif price_like:
            verdict, reason = "true", "回购价/回购方案语义贴近该值"
        elif coord or wins:
            verdict, reason = "false", "该值为坐标语境价位，非回购披露价"
        else:
            verdict, reason = "false", "值不可定位/JSON 截断，非回购披露价"

    elif dtype in ("shareholder_increase", "shareholder_decrease"):
        kw = "增持" if dtype == "shareholder_increase" else "减持"
        occ = _occurrences(ctx, kw)
        # 值紧邻『增持/减持 + 成交/价/披露』→ 披露价；否则一律非披露价
        disc_price = any(
            re.search(rf"{kw}[^。]{{0,8}}(?:价|成交|均价|披露|公告|股|万股)|"
                      rf"(?:股东|公告|披露)[^。]{{0,10}}{kw}", w)
            for w in wins
        )
        pos_or_flow = any(
            re.search(rf"{kw}\s*(?:仓|头寸|比例|幅度|至|到|\d|%|获利|避险|离场|"
                      rf"抛盘|兑现)|逢高|挂单|清仓|减仓|仓位|头寸|持仓|外资|"
                      rf"融资盘|中单|超大单|融券|杠杆|ETF|做空", w)
            for w in wins
        ) or any(
            re.search(r"逢高|挂单|清仓|减仓|离场|避险|获利|兑现|外资|融资盘|"
                      r"中单|超大单|融券|ETF|做空|仓位|头寸|持仓|建议", _kw_window(ctx, i, 14))
            for i in occ
        )
        if disc_price and not pos_or_flow:
            verdict, reason = "true", "股东增减持披露价语义"
        elif pos_or_flow or not disc_price:
            verdict, reason = "false", "头寸建议/资金流描述/非披露价的增减持语境"
        else:
            verdict, reason = "ambiguous", "增减持语境无法定夺"

    elif dtype == "dragon_tiger_list":
        if any(re.search(r"龙虎榜[^。]{0,8}(?:成交|买|卖|净买|净卖|价)", w) for w in wins):
            verdict, reason = "true", "龙虎榜成交价语义"
        elif _JSON_BLOB.search(ctx):
            verdict, reason = "false", "JSON 片段数字，非龙虎榜价"
        else:
            verdict, reason = "false", "龙虎榜语境但该值非榜价"

    elif dtype == "private_placement":
        verdict, reason = "true", "定增/增发字面命中"

    return {
        "ref_id": ref.get("ref_id"), "value": ref.get("value"),
        "source": ref.get("source"), "context": ctx,
        "disclosure_type": dtype, "verdict": verdict, "subtype": subtype,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s and s.strip()]


def _extract_dates(text: str) -> List[str]:
    dates: List[str] = []
    for m in _DATE_PATTERN.finditer(text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            dates.append(f"{y}-{mo:02d}-{d:02d}")
    return dates


def _extract_price_values(sentence: str) -> List[Tuple[float, int]]:
    """Extract (value, position) price mentions from a sentence, deduplicated.

    [C1]/[C4]：命中后按 token 语境过滤伪命中（单位/日期/JSON/外币/每股财务），
    并把共享可执行价位词（含区间写法）并入抽取。position 为数字 token 起点。
    """
    found: List[Tuple[float, int]] = []
    seen_spans: List[Tuple[int, int]] = []

    def _add(value_str: str, start: int, end: int, nstart: int, nend: int) -> None:
        for s0, e0 in seen_spans:
            if start < e0 and s0 < end:
                return
        try:
            value = float(value_str)
        except (TypeError, ValueError):
            return
        if value <= 0:
            return
        if _is_false_mention(sentence, nstart, nend, value):  # [C1]
            return
        seen_spans.append((start, end))
        found.append((value, nstart))

    for m in _PER_SHARE_PATTERN.finditer(sentence):
        num = m.group(1) or m.group(2)
        ns = m.start(1) if m.group(1) else m.start(2)
        ne = m.end(1) if m.group(1) else m.end(2)
        _add(num, m.start(), m.end(), ns, ne)
    for m in _BARE_YUAN_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(), m.end(), m.start(1), m.end(1))
    for m in _PRICE_KEYWORD_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(), m.end(), m.start(1), m.end(1))

    found.sort(key=lambda item: item[1])
    return found


def _detect_disclosure_type(sentence: str) -> Optional[str]:
    """[C2] 最长匹配后过 verdict 规则：verdict=false → 不是该类披露价。"""
    best: Optional[Tuple[int, str]] = None  # (keyword length, type)
    for kw, dtype in TYPED_DISCLOSURE_KEYWORDS.items():
        if kw in sentence:
            if best is None or len(kw) > best[0]:
                best = (len(kw), dtype)
    if best is None:
        return None
    verdict = classify_typed_disclosure({"context": sentence, "disclosure_type": best[1]})
    if verdict["verdict"] == "false":
        return None
    return best[1]


def _has_coordinate_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in COORDINATE_KEYWORDS)


def _has_derived_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in DERIVED_KEYWORDS)


def _conversion_target_basis(sentence: str) -> Optional[str]:
    """Target basis declared by a conversion sentence (前复权→qfq; 不复权→raw)."""
    return _declared_basis(sentence)


def _declared_basis(window: str) -> Optional[str]:
    """Basis explicitly declared by sentence keywords (deterministic text marker,
    not model self-certification — the label comes from the declared word).
    """
    if "不复权" in window or "未复权" in window:
        return PRICE_BASIS_RAW
    if "前复权" in window or "qfq" in window.lower():
        return PRICE_BASIS_VENDOR_QFQ
    return None


def _norm_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    m = _DATE_PATTERN.search(value)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    stripped = value.strip()
    return stripped or None


# [C2] 复权转换要求：句内必须出现复权语义（前复权/不复权/qfq/因子），
# 「折合/折算」单出现只是估值算术动词，不构成口径转换。
_REAL_CONVERSION_CTX = re.compile(r"复权|qfq|前复权|不复权|因子", re.I)
_CONVERSION_VERB_PATTERN = re.compile(r"换算|折算|折合|转换")


def build_price_ref_registry(
    reports: Mapping[str, Any],
    *,
    cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the price-ref registry, gap ledger, and validation preview.

    Args:
        reports: mapping of report field name -> report text (non-string values
            are ignored).
        cutoff: run cutoff date (YYYY-MM-DD). Technical-report prices inherit it
            as as_of; conversion factor_as_of later than cutoff previews invalid.
    Returns:
        {"price_refs": [...], "price_basis_gaps": [...],
         "validation": {"status": ..., "findings": [...]}}
    """
    cutoff_norm = _norm_date(cutoff)
    refs: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []

    def _next_id() -> str:
        return f"pr-{len(refs) + 1:03d}"

    def _add_gap(kind: str, ref_id: str, report: str, detail: str) -> None:
        entry = {"kind": kind, "ref_id": ref_id, "source": report, "detail": detail}
        if entry not in gaps:
            gaps.append(entry)

    # Pass 1 — extract refs per report/sentence with deterministic basis.
    for report_name in REPORT_FIELDS:
        text = reports.get(report_name)
        if not isinstance(text, str) or not text.strip():
            continue
        is_technical = report_name in TECHNICAL_REPORT_FIELDS
        for sentence in _split_sentences(text):
            mentions = _extract_price_values(sentence)      # [C1]+[C4]
            if not mentions:
                continue
            disclosure_type = _detect_disclosure_type(sentence)  # [C2]
            derived = _has_derived_keyword(sentence)
            sentence_dates = _extract_dates(sentence)
            sentence_as_of = sentence_dates[0] if sentence_dates else None

            sentence_ref_ids: List[Tuple[str, float, str]] = []
            for value, pos in mentions:
                mention_window = sentence[max(0, pos - 15):pos]
                declared = _declared_basis(mention_window)
                ref: Dict[str, Any] = {
                    "ref_id": _next_id(),
                    "value": value,
                    "basis": PRICE_BASIS_UNSPECIFIED,
                    "source": report_name,
                    "provenance": "model_text",
                    "as_of": sentence_as_of,
                    "context": sentence[:120],
                }
                if disclosure_type is not None:
                    ref["basis"] = DISCLOSURE_TYPE_BASIS[disclosure_type]
                    ref["provenance"] = f"typed_disclosure:{disclosure_type}"
                    ref["disclosure_type"] = disclosure_type
                elif declared is not None:
                    ref["basis"] = declared
                    ref["provenance"] = f"declared_basis:{declared}"
                elif is_technical:
                    ref["basis"] = PRICE_BASIS_VENDOR_QFQ
                    ref["provenance"] = "technical_report:vendor_qfq"
                    if ref["as_of"] is None:
                        ref["as_of"] = cutoff_norm

                if derived:
                    ref["provenance"] = "derived:" + ref["provenance"]
                    # [C2] conversion 语义修正：仅当句内同时有复权语义才登记
                    # conversion；「折合/折算」单独出现只是估值/量幅算术。
                    is_real_conv = bool(
                        _REAL_CONVERSION_CTX.search(sentence)
                        and _CONVERSION_VERB_PATTERN.search(sentence)
                    )
                    if is_real_conv:
                        factor_match = _FACTOR_PATTERN.search(sentence)
                        ref["conversion"] = {
                            "factor": float(factor_match.group(1)) if factor_match else None,
                            "factor_as_of": sentence_as_of,
                        }
                        target = _conversion_target_basis(sentence)
                        if target is not None and disclosure_type is None:
                            ref["basis"] = target

                refs.append(ref)
                sentence_ref_ids.append((ref["ref_id"], value, ref["basis"]))

            # derived_from lineage: other price mentions in the same sentence.
            if derived and len(sentence_ref_ids) > 1:
                ids = [rid for rid, _v, _b in sentence_ref_ids]
                for rid in ids:
                    ref = next(r for r in refs if r["ref_id"] == rid)
                    ref["derived_from"] = [other for other in ids if other != rid]

    # Pass 2 — registry back-reference inheritance for unspecified model prices.
    qfq_values = [
        r["value"]
        for r in refs
        if r["basis"] == PRICE_BASIS_VENDOR_QFQ and not r["provenance"].startswith("derived")
    ]
    for ref in refs:
        if ref["basis"] != PRICE_BASIS_UNSPECIFIED:
            continue
        for qv in qfq_values:
            if abs(ref["value"] - qv) <= _VALUE_MATCH_TOLERANCE:
                ref["basis"] = PRICE_BASIS_VENDOR_QFQ
                ref["provenance"] = "registry_backref:vendor_qfq"
                if ref["as_of"] is None:
                    ref["as_of"] = cutoff_norm
                break

    # Pass 3 — gaps: missing basis / missing as_of.
    for ref in refs:
        if ref["basis"] == PRICE_BASIS_UNSPECIFIED:
            _add_gap(
                "missing_basis",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 无法归因 basis（模型新价不可自证）",
            )
        if ref["as_of"] is None:
            _add_gap(
                "missing_as_of",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 缺少 as_of",
            )

    # Pass 4 — basis mismatch detection.
    concrete_bases = {PRICE_BASIS_VENDOR_QFQ, PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW}

    by_report: Dict[str, List[Dict[str, Any]]] = {}
    for ref in refs:
        by_report.setdefault(ref["source"], []).append(ref)

    for report_name, report_refs in by_report.items():
        # R1: same-sentence refs with different concrete bases.
        by_sentence: Dict[str, List[Dict[str, Any]]] = {}
        for ref in report_refs:
            by_sentence.setdefault(ref["context"], []).append(ref)
        for _ctx, s_refs in by_sentence.items():
            bases = {r["basis"] for r in s_refs} & concrete_bases
            if len(bases) > 1:
                ids = [r["ref_id"] for r in s_refs]
                finding = {
                    "kind": "basis_mismatch",
                    "rule": "same_sentence_mixed_basis",
                    "source": report_name,
                    "ref_ids": ids,
                    "detail": f"同句混用 basis {sorted(bases)}: {ids}",
                }
                findings.append(finding)
                _add_gap(
                    "basis_mismatch",
                    ids[0],
                    report_name,
                    finding["detail"],
                )

        # R2: a qfq ref anchored to technical coordinates while the same report
        # also carries raw/pit_raw refs (the b188060f pattern: raw 大宗价 vs
        # qfq 现价/锚/支撑混用).
        non_qfq = [r for r in report_refs if r["basis"] in (PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW)]
        if not non_qfq:
            continue
        for ref in report_refs:
            if ref["basis"] != PRICE_BASIS_VENDOR_QFQ:
                continue
            if not _has_coordinate_keyword(ref["context"]):
                continue
            other_ids = [r["ref_id"] for r in non_qfq]
            detail = (
                f"qfq 价格 {ref['value']}({ref['ref_id']}) 与 raw/pit_raw 价格 "
                f"{other_ids} 处于同一坐标语境（现价/支撑/锚等）"
            )
            findings.append(
                {
                    "kind": "basis_mismatch",
                    "rule": "cross_basis_coordinate_reference",
                    "source": report_name,
                    "ref_ids": [ref["ref_id"], *other_ids],
                    "detail": detail,
                }
            )
            _add_gap("basis_mismatch", ref["ref_id"], report_name, detail)

    # Pass 5 — conversion validity preview.
    invalid = False
    for ref in refs:
        conv = ref.get("conversion")
        if not isinstance(conv, dict):
            continue
        factor = conv.get("factor")
        factor_as_of = _norm_date(conv.get("factor_as_of"))
        reason = None
        if factor is None:
            reason = "raw→qfq 换算缺少 factor provenance"
        elif cutoff_norm and factor_as_of and factor_as_of > cutoff_norm:
            reason = f"factor_as_of {factor_as_of} 晚于 cutoff {cutoff_norm}"
        elif cutoff_norm and factor_as_of is None:
            reason = "换算缺少 factor_as_of"
        if reason is not None:
            invalid = True
            findings.append(
                {
                    "kind": "invalid_conversion",
                    "source": ref["source"],
                    "ref_ids": [ref["ref_id"]],
                    "detail": reason,
                }
            )

    status = "invalid" if invalid else ("gaps_present" if (gaps or findings) else "ok")
    validation = {
        "status": status,
        "preview_only": True,
        "cutoff": cutoff_norm,
        "findings": findings,
    }
    return {
        "price_refs": refs,
        "price_basis_gaps": gaps,
        "validation": validation,
    }


def audit_price_ref_registry(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Run the bypass audit over a final graph state and attach side-channel fields.

    Writes ``price_refs`` / ``price_basis_gaps`` / ``price_basis_validation``
   
    into ``state`` and returns the audit payload. Never raises on malformed
    input and never mutates decision/target/stop fields.

    """
    if not isinstance(state, MutableMapping):
        return {"price_refs": [], "price_basis_gaps": [], "validation": {"status": "ok", "preview_only": True, "findings": []}}

    try:
        cutoff = state.get("trade_date")
        reports = {name: state.get(name) for name in REPORT_FIELDS}
        # Also scan nested horizon results when present (short_term/medium_term/result_data).
        for sub_key in ("short_term", "medium_term", "result_data"):
            sub = state.get(sub_key)
            if isinstance(sub, Mapping):
                for name in REPORT_FIELDS:
                    if name not in reports or reports[name] in (None, ""):
                        if isinstance(sub.get(name), str):
                            reports[name] = sub[name]

        result = build_price_ref_registry(reports, cutoff=cutoff)
    except Exception:
        # DAV-1199 🟡-1 (upgraded to mandatory): the audit is bypass-only — a
        # malformed state must never crash the pipeline. Fail-closed: the error
        # is recorded as a price_basis_gap and flagged on the validation payload
        # so downstream (price_basis_gate) treats every price as un-audited and
        # non-consumable, rather than silently letting them through.
        result = {
            "price_refs": [],
            "price_basis_gaps": [
                {
                    "kind": "audit_error",
                    "ref_id": None,
                    "source": None,
                    "detail": "price_ref 审计内部异常，全部价格视为未审计、不可消费",
                }
            ],
            "validation": {
                "status": "audit_error",
                "preview_only": True,
                "audit_error": True,
                "findings": [],
            },
        }
    state["price_refs"] = result["price_refs"]
    state["price_basis_gaps"] = result["price_basis_gaps"]
    state["price_basis_validation"] = result["validation"]
    return result
