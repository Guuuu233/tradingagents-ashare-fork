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

DAV-1235 precision tightening (audit of production report 4390ddfd):

- P1′ ``derived_estimate`` only fires on a valuation *formula* (D-042 revised):
  (a) a valuation multiplier/model word (PE/PB/PS/PEG/EV-EBITDA/市盈率/市净率/
  市销率/DCF/贴现/股息率, plus 「估值至/到/为/在 N 倍」 and 「N 倍估值/市盈率」)
  within 60 chars before the number in the same sentence, and (b) a computation
  link inside the number's own sub-clause and before the number (对应/折合/折算/
  隐含/测算得/×/乘以), or 「N 倍/给予…倍」 in the number's sub-clause, or a
  per-share base (EPS/每股收益/每股净资产) co-occurring with a multiplier.
  Narrative words alone (估值修复/估值中枢/估值底/测算/估算/市值/公允/安全边际)
  never trigger.
- P2′ two-tier veto: hard veto — VWMA/VWAP/MA N/均线/布林/BOLL inside the
  60-char window; bound veto — 现价/收盘/开盘/涨停/跌停/前高/前低 only when
  directly modifying the number (same sub-clause, before it, no computation
  word in between). 支撑/压力/阻力/平台/箱体/最高/最低/底线 are NOT vetoes.
- P3 difference amounts (空间/回撤/滑点/价差/差价 and 上涨/下跌/涨/跌 N 元)
  are not price coordinates and are not registered as refs at all (chosen
  over a non-coordinate role: they are not prices, so the registry — whose
  contract is price-coordinate accountability — simply does not list them;
  this also removes them from every downstream decision-driving/executable
  check without touching gate rules).
- P4 non-stock commodity/product prices (批发价/出厂价/零售价/指导价/终端价/
  散瓶/整箱/吨价 …) are not registered.
- P5′ non-price measurements — 「N 板」连板 counts and values directly
  followed by 倍 including decimal ranges (1.8~2.0倍 / 12-14 倍) — are not
  registered.
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

# [C3] derived_estimate is a semantic role, not a coordinate basis: a ref
# carrying it is a model-derived valuation estimate — it may participate in
# reasoning but is not a market quote, carries no decision-driving basis/as_of
# accountability, and may not back an executable level.
PRICE_BASIS_DERIVED_ESTIMATE = "derived_estimate"

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
# [C4] Shared executable-level vocabulary (registry extraction + gate checks)
# ---------------------------------------------------------------------------

# Anchor words shared by the registry extractor and the gate level checker.
_EXECUTABLE_ANCHOR_WORDS = (
    "目标价",
    "目标位",
    "第一目标",
    "第二目标",
    "下行目标",
    "上行目标",
    "止盈",
    "止损",
    "入场",
    "进场",
    "买入",
    "卖出",
    "建仓",
    "开仓",
    "出场",
    "加仓",
    "减仓",
)

# Gate-level pattern: a value anchored to an executable word is an executable
# number (kept identical to the historical gate contract — no 区间 anchors).
_LEVEL_PATTERN = re.compile(
    r"(?:目标价|目标位|第一目标|第二目标|下行目标|上行目标|止盈位?|止损位?|"
    r"入场价?|进场价?|买入价|卖出价|建仓价|开仓价|出场价|加仓价|减仓价)"
    r"[^0-9]{0,12}?(\d+(?:\.\d+)?)"
)

# Registry-extraction pattern: same anchors plus explicit 区间 phrasing so both
# ends of 「X–Y 元」 ranges register a ref before accountability.
_LEVEL_PATTERN_EXTENDED = re.compile(
    r"(?:目标价|目标位|第一目标|第二目标|下行目标|上行目标|止盈位?|止损位?|"
    r"入场价?|进场价?|买入价|卖出价|建仓价|开仓价|出场价|加仓价|减仓价|"
    r"入场区间|进场区间|建仓区间|加仓区间|减仓区间|买入区间|卖出区间)"
    r"[^0-9]{0,12}?(\d+(?:\.\d+)?)"
)


def _is_false_level(text: str, nstart: int, nend: int, value: float) -> bool:
    """Executable-level hit that is not a price: Markdown list ordinal 「N. 」,
    percentage, share/amount count, or a date/year fragment."""
    tail = text[nend:nend + 8]
    if re.match(r"^\.\s", tail):                      # Markdown「2. 」序号
        return True
    if re.match(r"^\s*[%％]", tail):                  # 百分比
        return True
    if re.match(r"^\s*(?:亿|万)?\s*(?:股|手|户|份)", tail):
        return True
    if re.search(r"20\d{2}\s*[-/年.]", text[max(0, nstart - 6):nend + 6]):
        return True
    return False


def extract_executable_levels(text: str) -> List[Tuple[float, int, int]]:
    """Shared executable-level extractor. Returns (value, num_start, num_end)
    with false hits (list ordinals / percents / counts / dates) removed."""
    values: List[Tuple[float, int, int]] = []
    if not isinstance(text, str):
        return values
    for m in _LEVEL_PATTERN.finditer(text):
        try:
            v = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if _is_false_level(text, m.start(1), m.end(1), v):
            continue
        values.append((v, m.start(1), m.end(1)))
    return values


# ---------------------------------------------------------------------------
# [C1] Extraction guards — non-price / foreign mention filters
# ---------------------------------------------------------------------------

_UNIT_TAIL = {
    # e.g. 「50%」回溯截出的 5、LaTeX \%
    "percent": re.compile(r"^\s*\d*\\?[%％]"),
    # 「1.8-2.0倍」「1.8~2.0倍」「12-14 倍」区间倍数（P5′：允许小数区间）
    "multiple": re.compile(r"^\s*[-~—]?\s*\d*(?:\.\d+)?\s*倍"),
    "shares": re.compile(r"^\s*(?:亿|万)?\s*(?:股|手|户|份)"),
    # [P5] 「N 板」连板数（「最高4板」），非价格计量；(?!块) 防「板块」误伤
    "board_count": re.compile(r"^\s*(?:连)?板(?!块)"),
    # 时间/日期碎片：含范围写法「1-2周」「3 个月」「10 日 EMA」
    "time": re.compile(
        r"^\s*[-~—]?\s*\d*\s*(?:个)?\s*"
        r"(?:分钟|小时|交易日|日|天|周|个月|月|年|季度|期|次|条|家|人|档|位)"
    ),
    "ratio": re.compile(r"^\s*[:：]"),
}

# [P3] 差额语境：数字是「空间/回撤/滑点/价差/差价/幅度」的量值，或「涨/跌 N 元」
# 的变动量——不是价格坐标，不登记。粒子白名单刻意不含 至/到/破/穿：
# 「跌至 45 元」「跌破 2000 元」仍是坐标价。
_DIFF_HEAD = re.compile(
    r"(?:空间|回撤|滑点|价差|差价|幅度)\s*(?:约|达|为|有|近|超|超过|不足)?\s*$"
    r"|(?<![停板])(?:上涨|下跌|涨|跌)\s*(?:了|达|约|近|超|超过|不足|幅|逾)?\s*$"
)
_DIFF_TAIL = re.compile(r"^\s*元\s*(?:滑点|价差|差价)")

# [P4] 非本股价格：商品/产品价格词出现在数值邻近语境 → 不登记为本股价格坐标。
_NON_STOCK_PRICE_CTX = re.compile(
    r"批发参考价|批发价|出厂价|零售价|指导价|终端价|散瓶|整箱|吨价|公斤价|克价"
)

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
    if _UNIT_TAIL["board_count"].match(tail):               # [P5]
        return "board_count"
    if _DIFF_HEAD.search(head) or _DIFF_TAIL.match(tail):  # [P3]
        return "price_difference"
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
    # [P4] 数值 ±40/±12 字窗口内的商品/产品价格词 → 非本股价格坐标
    if _NON_STOCK_PRICE_CTX.search(sentence[max(0, start - 40):end + 12]):
        return "non_stock_price"
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
# [C3] derived_estimate — token 级估值算术绑定
# ---------------------------------------------------------------------------

_SUBCLAUSE_SPLIT = re.compile(r"[，。；;：:、|（）()\[\]【】\n\r]+")

# [DAV-1235 P1′] (a) 估值乘数/估值模型词——只认公式词，叙事词不算：
# 「估值修复/估值中枢/估值底/测算/估算/市值/公允/安全边际」均不在表中。
# 窗口：数值之前 60 字内、同一句（调用方传入的 context 已是句子级切分）。
_DERIVED_MODEL_WORDS = re.compile(
    r"(?<![A-Za-z0-9])(?:PE|PB|PS|PEG|DCF)(?![A-Za-z0-9])"
    r"|EV/EBITDA|市盈率|市净率|市销率|贴现|股息率"
    # 两种乘数写法：「估值（至/到/为/在）N 倍」「N 倍估值 / N 倍市盈率」
    r"|估值\s*(?:至|到|为|在)\s*\d+(?:\.\d+)?\s*倍"
    r"|\d+(?:\.\d+)?\s*倍\s*(?:估值|市盈率)"
)
# (b) 计算关联词：必须在数值所在子句内、数值之前（D-042 二次修正，
# 删除「紧邻前一子句」——技术颈线/现价会借用上一子句的「对应」误豁免）。
_DERIVED_CALC_WORDS = re.compile(r"对应|折合|折算|隐含|测算得|×|乘以")
# (b2) 数值所在子句内的乘数写法
_DERIVED_CLAUSE_MULT = re.compile(r"\d+(?:\.\d+)?\s*倍|给予[^，。；]{0,10}倍")
# (b3) 每股基数（与乘数同子句出现时等同 (b)）
_DERIVED_PER_SHARE_BASE = re.compile(r"EPS|每股收益|每股净资产", re.I)
_DERIVED_MULTIPLIER = re.compile(r"倍|×|乘以")

# [DAV-1235 P2′] 硬否决：技术指标词出现在数值前 60 字窗口内一律否决。
_DERIVED_HARD_VETO = re.compile(r"VWMA|VWAP|MA\s*\d+|均线|布林|BOLL", re.I)
# [P2′] 绑定否决：行情坐标词仅当直接修饰该数值时否决——出现在数值所在子句、
# 数值之前，且与该数值之间没有计算关联词。
_DERIVED_BOUND_VETO = re.compile(r"现价|收盘|开盘|涨停|跌停|前高|前低")


def _subclause_bounds(sentence: str, start: int, end: int) -> Tuple[int, int]:
    """返回包含 token [start,end) 的子句边界（_SUBCLAUSE_SPLIT 切分）。"""
    left = 0
    for m in _SUBCLAUSE_SPLIT.finditer(sentence[:start]):
        left = m.end()
    right = len(sentence)
    m = _SUBCLAUSE_SPLIT.search(sentence, end)
    if m:
        right = m.start()
    return left, right


def is_derived_value(sentence: str, value: float, tol: float = _VALUE_MATCH_TOLERANCE) -> bool:
    """[C3/P1′] token 级 derived 判定（D-042 修正规格）：
    (a) 估值乘数/模型词出现在数值前 60 字内、同一句；
    (b) 满足任一——计算关联词（对应/折合/折算/隐含/测算得/×/乘以）在数值
        所在子句内且位于数值之前；「N 倍/给予…倍」在数值子句；每股基数与
        乘数同子句；
    [P2′] 技术指标词在 60 字窗口内硬否决；行情坐标词直接修饰该数值时绑定
    否决。叙事词不单独触发。"""
    for s, e, _p in _match_spans(sentence or "", value, tol):
        pre60 = sentence[max(0, s - 60):s]
        if _DERIVED_HARD_VETO.search(pre60):
            continue                                        # [P2′] 硬否决
        cs, ce = _subclause_bounds(sentence, s, e)
        clause = sentence[cs:ce]
        # [P2′] 绑定否决：坐标词在数值前的本子句内，且与数值间无关联词
        bound = False
        for vm in _DERIVED_BOUND_VETO.finditer(clause):
            if vm.end() <= s - cs:
                between = clause[vm.end():s - cs]
                if not _DERIVED_CALC_WORDS.search(between):
                    bound = True
                    break
        if bound:
            continue
        if not _DERIVED_MODEL_WORDS.search(pre60):
            continue                                        # 缺 (a)
        has_calc = bool(_DERIVED_CALC_WORDS.search(clause[:s - cs]))
        has_mult = bool(_DERIVED_CLAUSE_MULT.search(clause))
        has_pershare = bool(
            _DERIVED_PER_SHARE_BASE.search(clause)
            and _DERIVED_MULTIPLIER.search(clause)
        )
        if has_calc or has_mult or has_pershare:
            return True                                     # (a)+(b)
    return False


_QUOTE_WORDS = re.compile(r"现价|收盘|报收|股价|运行于|位于|关口|平台|报价|价位")


def looks_like_actual_quote(context: str, value: float,
                            pool: Optional["MarketDataPool"],
                            tol: float = _VALUE_MATCH_TOLERANCE) -> bool:
    """[C3] 真报价保护：token 窗口含报价语义词且值匹配本运行具名字段 →
    这是市场报价（coordinate），不得因同句估值算术词被判 derived。"""
    if pool is None:
        return False
    for s, e, _p in _match_spans(context or "", value, tol):
        w = context[max(0, s - 15):e + 10]
        if _QUOTE_WORDS.search(w) and pool.fields_matching(value, tol):
            return True
    return False


# ---------------------------------------------------------------------------
# [C5] MarketDataPool — 本次运行行情/指标的字段级 provenance
# ---------------------------------------------------------------------------

# 运行 state 上承载桥接源数据的键。生产路径由 finalize 前的最小调用链
# 写入（collected pool 的 stock_data / indicators）；重算/测试可直接写入同一
# 结构。数据必须是 as-of trade_date 的本次运行数据——不得访问外部或未来数据。
PRICE_REF_SOURCE_KEY = "price_ref_source"

REQUIRED_OHLC_COLUMNS = ("date", "open", "high", "low", "close")


class MarketDataPoolError(RuntimeError):
    """fail-closed：stock_data 缺必需列或结构不可用 → 不建池（不桥接）。"""


@dataclass
class NamedValue:
    field: str          # 具名字段，如 stock_data.2026-08-14.close
    value: float
    basis_hint: str = "vendor_qfq"   # stock_data 头部声明的 price_basis
    as_of: Optional[str] = None      # bar 日期或 trade_date（指标）


@dataclass
class MarketDataPool:
    symbol: str
    trade_date: str
    stock_data_header: List[str] = field(default_factory=list)
    bars: List[Dict[str, Any]] = field(default_factory=list)
    indicators: Dict[str, float] = field(default_factory=dict)
    named_values: List[NamedValue] = field(default_factory=list)
    stock_data_basis: Optional[str] = None

    def fields_matching(self, value: float, tol: float = _VALUE_MATCH_TOLERANCE) -> List[NamedValue]:
        return [nv for nv in self.named_values if abs(nv.value - value) <= tol]

    def bar_by_date(self, date: str) -> Optional[Dict[str, Any]]:
        for b in self.bars:
            if b.get("date") == date:
                return b
        return None

    def price_range(self) -> Optional[Tuple[float, float]]:
        lows = [b["low"] for b in self.bars if isinstance(b.get("low"), (int, float))]
        highs = [b["high"] for b in self.bars if isinstance(b.get("high"), (int, float))]
        if not lows or not highs:
            return None
        return min(lows), max(highs)


_LIMIT_RATIO_BY_PREFIX = (
    (("300", "301", "688", "689"), 0.20),   # 创业板/科创板 ±20%
    (("8", "4", "92"), 0.30),               # 北交所 ±30%（防御性）
)
_DEFAULT_LIMIT_RATIO = 0.10                 # 主板 ±10%


def _limit_ratio(symbol: str) -> float:
    code = str(symbol or "").split(".")[0]
    for prefixes, ratio in _LIMIT_RATIO_BY_PREFIX:
        if code.startswith(prefixes):
            return ratio
    return _DEFAULT_LIMIT_RATIO


def parse_stock_data_text(text: Any, symbol: str) -> Tuple[List[str], List[Dict[str, Any]], Optional[str]]:
    """按 header 名称解析 stock_data CSV。返回 (header, bars, price_basis)。

    fail-closed：缺 date/open/high/low/close 任一列 → MarketDataPoolError。
    """
    if not isinstance(text, str):
        raise MarketDataPoolError(f"{symbol}: stock_data 不是文本（{type(text).__name__}）")
    lines = text.splitlines()
    basis = None
    csv_lines: List[str] = []
    for ln in lines:
        if ln.startswith("#"):
            m = re.search(r"price_basis:\s*(\S+)", ln)
            if m:
                basis = m.group(1)
            continue
        if ln.strip():
            csv_lines.append(ln)
    if not csv_lines:
        raise MarketDataPoolError(f"{symbol}: stock_data 无 CSV 行")
    reader = csv.DictReader(io.StringIO("\n".join(csv_lines)))
    header = list(reader.fieldnames or [])
    missing = [c for c in REQUIRED_OHLC_COLUMNS if c not in header]
    if missing:
        raise MarketDataPoolError(
            f"{symbol}: stock_data 缺必需列 {missing}（实际表头 {header}），fail-closed"
        )
    bars: List[Dict[str, Any]] = []
    for row in reader:
        bar: Dict[str, Any] = {"date": (row.get("date") or "").strip()}
        ok = True
        for c in ("open", "high", "low", "close"):
            raw = (row.get(c) or "").strip()
            try:
                bar[c] = float(raw)
            except ValueError:
                ok = False
                bar[c] = None
        vol = (row.get("volume") or "").strip() if "volume" in header else ""
        try:
            bar["volume"] = float(vol) if vol else None
        except ValueError:
            bar["volume"] = None
        if ok and bar["date"]:
            bars.append(bar)
    if not bars:
        raise MarketDataPoolError(f"{symbol}: stock_data 解析后无有效 OHLC 行")
    return header, bars, basis


def build_market_data_pool(source: Mapping[str, Any], symbol: str,
                           trade_date: str) -> MarketDataPool:
    """从 collected/state 源数据建池：stock_data CSV + indicators dict。

    ``source`` 需要 ``stock_data``（CSV 文本）与 ``indicators``（数值 dict），
    可选 ``price_basis``。fail-closed：stock_data 不可用 → MarketDataPoolError。
    """
    pool = MarketDataPool(symbol=symbol, trade_date=trade_date)
    header, bars, basis = parse_stock_data_text(source.get("stock_data"), symbol)
    pool.stock_data_header = header
    pool.bars = bars
    pool.stock_data_basis = basis or "vendor_qfq"

    for b in bars:
        d = b["date"]
        for f in ("open", "high", "low", "close"):
            if isinstance(b.get(f), (int, float)):
                pool.named_values.append(
                    NamedValue(field=f"stock_data.{d}.{f}", value=b[f],
                               basis_hint=pool.stock_data_basis, as_of=d)
                )
    # 末根 bar 的 latest 别名
    last = bars[-1]
    for f in ("open", "high", "low", "close"):
        if isinstance(last.get(f), (int, float)):
            pool.named_values.append(
                NamedValue(field=f"stock_data.latest.{f}", value=last[f],
                           basis_hint=pool.stock_data_basis, as_of=last["date"])
            )
    # 涨跌停价：由末根 bar 收盘 × 板块幅度计算的具名派生字段（字段级 provenance）。
    ratio = _limit_ratio(symbol)
    prev_close = last.get("close")
    if isinstance(prev_close, (int, float)):
        pool.named_values.append(
            NamedValue(field=f"derived.limit_up@{last['date']}",
                       value=round(prev_close * (1 + ratio) + 1e-9, 2),
                       basis_hint=pool.stock_data_basis, as_of=last["date"])
        )
        pool.named_values.append(
            NamedValue(field=f"derived.limit_down@{last['date']}",
                       value=round(prev_close * (1 - ratio) + 1e-9, 2),
                       basis_hint=pool.stock_data_basis, as_of=last["date"])
        )

    ind = source.get("indicators")
    if isinstance(ind, dict):
        for k, v in ind.items():
            if isinstance(v, (int, float)):
                pool.indicators[k] = float(v)
                pool.named_values.append(
                    NamedValue(field=f"indicators.{k}", value=float(v),
                               basis_hint=pool.stock_data_basis, as_of=trade_date)
                )
    return pool


_INDICATOR_ALIASES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:10\s*日?\s*EMA|EMA\s*[-_]?\s*10|十\s*日\s*EMA)", re.I), "close_10_ema"),
    (re.compile(r"(?:50\s*日?\s*(?:SMA|均线|MA)|SMA\s*[-_]?\s*50|MA\s*50)", re.I), "close_50_sma"),
    (re.compile(r"(?:200\s*日?\s*(?:SMA|均线|MA)|SMA\s*[-_]?\s*200|MA\s*200|年线)", re.I), "close_200_sma"),
    (re.compile(r"VWMA|成交加权价|成交量加权", re.I), "vwma"),
    (re.compile(r"布林(?:带)?上轨|BOLL\s*上轨|boll_ub", re.I), "boll_ub"),
    (re.compile(r"布林(?:带)?下轨|BOLL\s*下轨|boll_lb", re.I), "boll_lb"),
    (re.compile(r"布林(?:带)?中轨|BOLL\s*中轨|BOLL\b|布林(?:带)?(?!上|下)", re.I), "boll"),
    (re.compile(r"\bATR\b|真实波幅", re.I), "atr"),
    (re.compile(r"\bRSI\b", re.I), "rsi"),
    (re.compile(r"\bMACD\b", re.I), "macd"),
]

_MD_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")

_OHLC_WORDS = {
    "open": ("开盘", "开于", "开盘价"),
    "close": ("收盘", "收于", "收盘价", "现价报收"),
    "high": ("最高", "高点", "上探", "冲高至"),
    "low": ("最低", "低点", "下探", "回踩"),
}


def named_field_hits(context: str, value: float, pool: MarketDataPool,
                     tol: float = _VALUE_MATCH_TOLERANCE) -> List[str]:
    """返回 context 明确指名且值匹配的 pool 字段清单（字段级 provenance）。

    [C5] 规则：
    - context 明确 EMA10/VWMA/SMA/BOLL/ATR 等指标名且值匹配对应指标；
    - 或明确日期 + OHLC 语义词匹配对应 bar 字段；
    - 或明确涨跌停语义匹配 computed limit 字段。
    其它同值一律不计入（调用方记 coincidence，不得桥接）。
    """
    hits: List[str] = []
    ctx = context or ""

    for pat, key in _INDICATOR_ALIASES:
        if pat.search(ctx) and key in pool.indicators:
            if abs(pool.indicators[key] - value) <= tol:
                hits.append(f"indicators.{key}")

    # 日期 + OHLC 语义
    dates = [f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
             for m in _DATE_PATTERN.finditer(ctx)]
    year = pool.trade_date[:4] if pool.trade_date else "2026"
    for m in _MD_DATE_RE.finditer(ctx):
        dates.append(f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    for d in dates:
        bar = pool.bar_by_date(d)
        if not bar:
            continue
        for f, words in _OHLC_WORDS.items():
            if any(w in ctx for w in words) and isinstance(bar.get(f), (int, float)):
                if abs(bar[f] - value) <= tol:
                    hits.append(f"stock_data.{d}.{f}")

    # 涨跌停语义
    if re.search(r"涨停", ctx):
        for nv in pool.fields_matching(value, tol):
            if nv.field.startswith("derived.limit_up"):
                hits.append(nv.field)
    if re.search(r"跌停", ctx):
        for nv in pool.fields_matching(value, tol):
            if nv.field.startswith("derived.limit_down"):
                hits.append(nv.field)
    return sorted(set(hits))


def attach_price_ref_source(state: MutableMapping[str, Any],
                            source: Optional[Mapping[str, Any]],
                            *,
                            symbol: Optional[str] = None,
                            trade_date: Optional[str] = None) -> bool:
    """[C5] 把本次运行的行情/指标源挂到 state 的 ``PRICE_REF_SOURCE_KEY`` 上。

    ``source`` 为 collected pool（或同构 Mapping），取 ``stock_data`` /
    ``indicators`` / ``price_basis`` 三个字段；symbol / trade_date 优先取
    显式参数，其次 source，再次 state 的 instrument_context / trade_date。
    源不可用时不写键并返回 False；本函数不抛异常。
    """
    if not isinstance(state, MutableMapping) or not isinstance(source, Mapping):
        return False
    stock_data = source.get("stock_data")
    indicators = source.get("indicators")
    if not isinstance(stock_data, str) or not stock_data.strip():
        return False
    if not isinstance(indicators, Mapping):
        indicators = {}
    inst = state.get("instrument_context")
    sym = (
        symbol
        or source.get("symbol")
        or (inst.get("symbol") if isinstance(inst, Mapping) else None)
        or state.get("company_of_interest")
        or ""
    )
    td = trade_date or source.get("trade_date") or state.get("trade_date") or ""
    state[PRICE_REF_SOURCE_KEY] = {
        "stock_data": str(stock_data),
        "indicators": dict(indicators),
        "price_basis": source.get("price_basis"),
        "symbol": sym,
        "trade_date": td,
    }
    return True


def _pool_from_state(state: Mapping[str, Any]) -> Optional[MarketDataPool]:
    """从 state 上的 ``PRICE_REF_SOURCE_KEY`` 建池；不可用/解析失败 → None。"""
    source = state.get(PRICE_REF_SOURCE_KEY)
    if not isinstance(source, Mapping):
        return None
    inst = state.get("instrument_context")
    symbol = (
        source.get("symbol")
        or (inst.get("symbol") if isinstance(inst, Mapping) else None)
        or state.get("company_of_interest")
        or ""
    )
    trade_date = source.get("trade_date") or state.get("trade_date") or ""
    try:
        return build_market_data_pool(source, symbol, trade_date)
    except Exception:
        return None


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

    # [C4] shared executable parser：gate 价位词并入 registry 抽取。
    for m in _LEVEL_PATTERN_EXTENDED.finditer(sentence):
        try:
            value = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        nstart, nend = m.start(1), m.end(1)
        if _is_false_mention(sentence, nstart, nend, value):
            continue
        if _is_false_level(sentence, nstart, nend, value):
            continue
        if any(abs(v - value) <= _VALUE_MATCH_TOLERANCE for v, _p in found):
            continue
        found.append((value, nstart))

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
    pool: Optional[MarketDataPool] = None,
) -> Dict[str, Any]:
    """Build the price-ref registry, gap ledger, and validation preview.

    Args:
        reports: mapping of report field name -> report text (non-string values
            are ignored).
        cutoff: run cutoff date (YYYY-MM-DD). Technical-report prices inherit it
            as as_of; conversion factor_as_of later than cutoff previews invalid.
        pool: optional :class:`MarketDataPool` built from THIS run's as-of
            market/indicator data (C5 source-backed bridge + C3 quote guard).
            ``None`` disables both — no external data is ever fetched here.

    Returns:
        {"price_refs": [...], "price_basis_gaps": [...],
         "validation": {"status": ..., "findings": [...]},
         "pool_bridge": [...]}
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

    # [C3] derived_estimate 语义角色：derived 估值锚不再是坐标 basis，
    # 可参与推理但不冒充 vendor_qfq coordinate/executable。
    for ref in refs:
        prov = ref.get("provenance") or ""
        # derived 判定绑定到数字本身（DAV-1235 D-042：P1′ 模型词限数值前
        # 60 字同一句 + 计算关联三条任一；P2′ 指标词硬否决、坐标词绑定否决）。
        # 真报价保护：报价语义词 + pool 具名字段同值 → 真坐标，不降格。
        ctx_v = ref.get("context") or ""
        is_derived = (
            is_derived_value(ctx_v, ref.get("value"))
            and not (
                pool is not None
                and looks_like_actual_quote(ctx_v, ref.get("value"), pool)
            )
        )
        # 只把「无 concrete basis」的派生值改写为 derived_estimate；
        # 已声明 qfq/raw/pit_raw 的 ref（如『前复权目标价』）保持原 basis，
        # conversion 记录也保留——合法转换仍是合法 executable。
        if is_derived and ref.get("disclosure_type") is None \
                and ref["basis"] == PRICE_BASIS_UNSPECIFIED:
            ref["basis"] = PRICE_BASIS_DERIVED_ESTIMATE
            ref["provenance"] = "role:derived_estimate|" + prov

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

    # [C5] strict source-backed pool→registry bridge：仅字段级 provenance。
    bridge_hits: List[Dict[str, Any]] = []
    if pool is not None:
        for ref in refs:
            if ref["basis"] != PRICE_BASIS_UNSPECIFIED:
                continue
            hits = named_field_hits(ref.get("context") or "", ref.get("value"), pool)
            if hits:
                ref["basis"] = PRICE_BASIS_VENDOR_QFQ
                ref["provenance"] = "pool_bridge:" + hits[0]
                ref["bridge_fields"] = hits
                if ref["as_of"] is None:
                    ref["as_of"] = cutoff_norm
                bridge_hits.append(
                    {"ref_id": ref["ref_id"], "value": ref["value"], "fields": hits}
                )

    # Pass 3 — gaps: missing basis / missing as_of.
    for ref in refs:
        if ref["basis"] == PRICE_BASIS_UNSPECIFIED:
            _add_gap(
                "missing_basis",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 无法归因 basis（模型新价不可自证）",
            )
        # [C3] derived_estimate 不是市场报价，不要求 as_of。
        if ref["as_of"] is None and ref["basis"] != PRICE_BASIS_DERIVED_ESTIMATE:
            _add_gap(
                "missing_as_of",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 缺少 as_of",
            )

    # Pass 4 — basis mismatch detection.
    # [C3] derived_estimate 不属 concrete basis，天然不参与 R1/R2 混用判定。
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
        "pool_bridge": bridge_hits,
    }


def audit_price_ref_registry(state: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Run the bypass audit over a final graph state and attach side-channel fields.

    Writes ``price_refs`` / ``price_basis_gaps`` / ``price_basis_validation``
    (plus ``price_ref_pool_bridge`` when the [C5] source pool is available)
    into ``state`` and returns the audit payload. Never raises on malformed
    input and never mutates decision/target/stop fields.

    [C5] 桥接数据只读本次运行 state 上 ``PRICE_REF_SOURCE_KEY`` 的行情/指标
    （由 finalize 前的调用链从 collected pool 挂入）；缺失或解析失败时按
    无池处理（不桥接），不访问任何外部数据。
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

        pool = _pool_from_state(state)
        result = build_price_ref_registry(reports, cutoff=cutoff, pool=pool)
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
    if result.get("pool_bridge"):
        state["price_ref_pool_bridge"] = result["pool_bridge"]
    return result
