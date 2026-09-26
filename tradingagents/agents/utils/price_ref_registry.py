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

DAV-1246 A 部分补漏（零 LLM 抽取修正，总控裁决单独合入）：

- A1 影线长度不是价格坐标，不登记：「留下/留有/形成/引发/长达/达/超过
  （了）N 元（的）（长）上影线/下影线」与「上影线/下影线（达/长约/长/
  长度）N 元」两种写法中的数值。动词后不允许「的」——「留下的 35.36 元
  上影线供应」「跌破 835.00 元下影线低点」是真实价位坐标，不误伤。
- A2 C5 指标别名增加「牛熊线」，映射到 ``close_200_sma``。

DAV-1321 提取器精度（DAV-1312 审计：(b) 类误报占违规 90.8%）：

- N1 非价格数字守卫（``_nonprice_number_flag``，mention 与 anchor-level
  两路共用）：百分数截断（``-6.5%`` 被回溯成 ``6``，``_PRICE_KEYWORD_PATTERN``
  lookahead 同步禁止 ``.\d`` 截断）、千分位（``3,840``）、字母/连字符
  粘连 token（``SMA50``/``INV-4``/``LPR_1Y``/``2026Q1``/``0.75x``/``25BP``）、
  分数（``1/3``）、MM-DD 日期片段（``07-22``）、外币单位（``85美元/桶``）、
  指标/估值词后缀（``10 EMA``/``42倍PE``）、``N档``、``元/吨``类单位价格、
  每10股派X元股利、止损敞口差额、JSON 引号内孤立数字、
  ``excluded_evidence`` 列表整体隔离。
- N2 anchor-level 路径补齐同一套守卫（``_is_skip_level_number`` 复用同一
  helper），且 ``v <= 0`` 一律跳过（``仓位0%``/``position_pct:0``）。
- N3 typed_disclosure 改为逐值判定：句级候选 dtype 仅作候选，是否挂
  provenance 由 ``classify_typed_disclosure`` 按该值自身的 ±15 字窗口裁决；
  block_trade 要求该值落在成交/作价/溢折价/席位语义子句内，否则 false；
  private_placement 要求值窗口含定增/增发/发行价/定价/募资等发行语义；
  issuance 增加永续债/债券/国债等「非股票发行」语境否决，且 true/ambiguous
  也要求值窗口含发行语义词。
- N4 明示口径传播：``_declared_basis`` 识别字面 token（``pit_raw`` →
  pit_raw、``vendor_qfq``/``qfq`` → qfq、裸 ``raw`` → raw）；数字右侧紧邻
  「元（前复权）」式括号口径同样生效；句内口径唯一时传播到句内未声明
  ref，句内出现多个不同口径则视为混用不传播；文档级声明（「一律/所有/
  统一/本文…前复权」或「前复权…口径/为准」）传播到**同一报告字段**内
  未声明 ref——边界即字段级文本，不跨 report。
- N5 区间起点补登记：「A-B 元」中 A 原本只在带「区间」锚词时才登记，
  现对任何 ``N-M元`` 形式的裸元区间起点同样登记（仍过全部伪命中守卫）。
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

# [DAV-1321 N1] lookahead 追加 `\.\d`：「-6.5%」的回溯会把 6.5 截成 6
# （「6」后面跟 「.5%」不被原 lookahead 拦截），补上后数字 token 不允许
# 落在另一个数字的小数点之前。
_PRICE_KEYWORD_PATTERN = re.compile(
    r"(?:现价|最新价|当前价|收盘价?|收于|开盘价?|最高|最低|均价|成本价?|"
    r"目标价|止损|止盈|支撑|压力位?|阻力位?|成交价|作价|单价|定增价|"
    r"发行价|回购价|增持价|减持价|投标价|锚定?|报价|每股)"
    # [DAV-1321] (?<![\d.]) 阻止右截断（「x.5」中的「5」），lookahead 的
    # `\d|\.\d` 阻止左截断（「12.0%」回溯出「1」「-6.5%」回溯出「6」）。
    r"[^0-9%]{0,8}?(?<![\d.])(\d+(?:\.\d+)?)(?!\s*[%％倍分角]|亿|万|股|手|户|家|次|日|天|年|月|\d|\.\d)"
)

_BARE_YUAN_PATTERN = re.compile(
    # [DAV-1321] 「元」后排除每股/每吨等量词；不允许空格后裸「/」误判为每股
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*元(?!\s*/\s*股|\s*/\s*吨|[/%％]|[吨克人次])"
)

_PER_SHARE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*元\s*/\s*股|每股\s*(\d+(?:\.\d+)?)\s*元"
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

# Gate-level anchors: a value anchored to an executable word is an
# executable number (kept identical to the historical gate contract —
# no 区间 anchors). [DAV-1255] Anchor-only patterns: numbers are scanned
# inside the post-anchor window so date/period/amount fragments can be
# skipped in favour of the real price behind them (E1–E4).
_LEVEL_ANCHOR_BODY = (
    r"(?:目标价|目标位|第一目标|第二目标|下行目标|上行目标|止盈位?|止损位?|"
    r"入场价?|进场价?|买入价|卖出价|建仓价|开仓价|出场价|加仓价|减仓价)"
)
_LEVEL_PATTERN = re.compile(_LEVEL_ANCHOR_BODY)

# Registry-extraction anchors: same anchors plus explicit 区间 phrasing so
# both ends of 「X–Y 元」 ranges register a ref before accountability.
_LEVEL_PATTERN_EXTENDED = re.compile(
    _LEVEL_ANCHOR_BODY[:-1]
    + r"|入场区间|进场区间|建仓区间|加仓区间|减仓区间|买入区间|卖出区间)"
)

# [DAV-1255 E4] post-anchor scan window: 40 chars, same clause only.
_ANCHOR_LEVEL_WINDOW = 40
_LEVEL_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?")
_LEVEL_CLAUSE_BREAK = re.compile(r"[。；;！!？？\n\r]")

# [DAV-1255 E1] 「M月D日」 date fragments.
_DATE_MD_TAIL = re.compile(r"^\s*月\s*\d{1,2}\s*日")      # number is the M
_DATE_D_TAIL = re.compile(r"^\s*日")                    # number is the D
_DATE_MD_HEAD = re.compile(r"\d{1,2}\s*月\s*$")
# [DAV-1255 E1] ISO date span 「2026-08-06」：年份规则只盖住前两段，
# 跳过-继续语义下「06」会漏成价位，因此整个 ISO 日期段按日期字段跳过。
_ISO_DATE_SPAN = re.compile(r"20\d{2}\s*[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2}")
# [DAV-1255 E2] 「N日」后接指标周期词（EMA/SMA/MA/均线/VWMA/布林/BOLL）
# 或 OHLC 字段词（最高/最低/收盘/开盘）的数字跳过；实现上推广为所有紧跟
# 「日」的数字一律跳过——价格不会以「日」为单位（见 _is_skip_level_number）。
# [DAV-1255 E3] amount units （亿元/万元/亿/万 — 不接「股」也算金额）.
_AMOUNT_TAIL = re.compile(r"^\s*(?:亿元|万元|亿|万)")
# [DAV-1255 同族补漏] E4 窗口由 12→40 字符后新暴露的两类误读，与 E1–E3
# 同属「非价位数字」：赔率/盈亏比 「N:1」「N:M」，及独立月份碎片「N月」。
_RATIO_TAIL = re.compile(r"^\s*[:：]\s*\d")
_RATIO_HEAD = re.compile(r"\d\s*[:：]\s*$")   # 「N:」后的 M（盈亏比 1:0.3）
_BARE_MONTH_TAIL = re.compile(r"^\s*月")
_BARE_DAY_TAIL = re.compile(r"^\s*日")          # 「N日」时长/日期碎片
_ENUM_HEAD = re.compile(r"[（(]\s*$")          # 列表枚举（N）
_ENUM_TAIL = re.compile(r"^\s*[)）]")
_TIME_TAIL = re.compile(
    r"^\s*(?:个\s*)?(?:分钟|小时|交易日|天|周|个月|月|年|季度)"
)                                                # 「N分钟/小时/天/周/个交易日…」时长


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


def _is_skip_level_number(text: str, nstart: int, nend: int) -> bool:
    """[DAV-1255 E1–E3] Number inside an anchor window that is a date field
    (「M月D日」), an indicator period / OHLC field (「N日 SMA/最低…」), or an
    amount (「N亿元/万元/亿/万」) — skip and keep scanning the window."""
    tail = text[nend:]
    for d in _ISO_DATE_SPAN.finditer(text):                  # E1: ISO 日期段
        if d.start() <= nstart and nend <= d.end():
            return True
        if d.start() > nend:
            break
    if _DATE_MD_TAIL.match(tail):                            # E1: M of M月D日
        return True
    if _DATE_D_TAIL.match(tail):
        if _DATE_MD_HEAD.search(text[:nstart]):              # E1: D of M月D日
            return True
        return True                                          # E2+：任何「N日」时长
    if _ENUM_HEAD.search(text[:nstart]) and _ENUM_TAIL.match(tail):
        return True                                          # 枚举序号（N）
    if _AMOUNT_TAIL.match(tail):                             # E3
        return True
    if _RATIO_TAIL.match(tail):                              # 赔率/盈亏比 N:M
        return True
    if _RATIO_HEAD.search(text[:nstart]):                    # N:M 中的 M
        return True
    if _BARE_MONTH_TAIL.match(tail):                         # 月份碎片「N月」
        return True
    if _TIME_TAIL.match(tail):                               # 时长「N分钟/天/周…」
        return True
    # [DAV-1321 N2] 与 mention 路径共用同一套非价格数字守卫：倍数/档数
    # （「1.5倍ATR」「盘口5档」「14倍PE」）、分数（「减仓1/3」）、编号
    # （「RISK-3」）、指标周期（「SMA50」「10 EMA」「0.75x PB」）、外币
    # （「85美元/桶」）、差额语境（「止损敞口 0.65 元」）、千分位、JSON
    # 引号数字与 excluded_evidence。
    if _DIFF_HEAD.search(text[max(0, nstart - 12):nstart]) or _DIFF_TAIL.match(tail):
        return True
    if _nonprice_number_flag(text, nstart, nend) is not None:
        return True
    if _NUMBER_LEVEL_COUNT_TAIL.match(tail):                 # 「5档」「1.5 倍」
        return True
    return False


def _iter_anchor_levels(
    text: str, anchor_re: "re.Pattern[str]"
) -> List[Tuple[float, int, int]]:
    """[DAV-1255 E4] For each anchor, take the first real price within 40
    chars in the same clause; date/period/amount/false fragments are skipped
    and the scan continues. No real value → the anchor yields no level."""
    found: List[Tuple[float, int, int]] = []
    for a in anchor_re.finditer(text):
        wstart = a.end()
        wend = min(len(text), wstart + _ANCHOR_LEVEL_WINDOW)
        cb = _LEVEL_CLAUSE_BREAK.search(text, wstart, wend)
        if cb:
            wend = cb.start()
        # 窗口边界不得切断数字（「15 分钟」被切成「1」会产生伪价位）。
        while (
            wend < len(text)
            and text[wend - 1] in "0123456789."
            and text[wend] in "0123456789."
        ):
            wend += 1
        for n in _LEVEL_NUMBER_PATTERN.finditer(text, wstart, wend):
            try:
                v = float(n.group(0))
            except (TypeError, ValueError):
                continue
            if v <= 0:                                       # [DAV-1321 N2] 0/负数不是价位
                continue
            if _is_false_level(text, n.start(), n.end(), v):
                continue
            if _is_skip_level_number(text, n.start(), n.end()):
                continue
            found.append((v, n.start(), n.end()))
            break
    return found


def extract_executable_levels(text: str) -> List[Tuple[float, int, int]]:
    """Shared executable-level extractor. Returns (value, num_start, num_end)
    with false hits (list ordinals / percents / counts / dates) removed and
    [DAV-1255] date/period/amount fragments skipped in favour of the real
    price inside the same anchor window."""
    if not isinstance(text, str):
        return []
    return _iter_anchor_levels(text, _LEVEL_PATTERN)


# ---------------------------------------------------------------------------
# [C1] Extraction guards — non-price / foreign mention filters
# ---------------------------------------------------------------------------

_UNIT_TAIL = {
    # e.g. 「50%」回溯截出的 5、LaTeX \%
    "percent": re.compile(r"^\s*\d*\\?[%％]"),
    # 「1.8-2.0倍」「1.8~2.0倍」「12-14 倍」区间倍数（P5′：允许小数区间）；
    # [DAV-1321] 补 en-dash – 与全角 ～（「1.05–1.15倍」此前漏判）。
    "multiple": re.compile(r"^\s*[-~～—–]?\s*\d*(?:\.\d+)?\s*倍"),
    "shares": re.compile(r"^\s*(?:亿|万)?\s*(?:股|手|户|份)"),
    # [P5] 「N 板」连板数（「最高4板」），非价格计量；(?!块) 防「板块」误伤
    "board_count": re.compile(r"^\s*(?:连)?板(?!块)"),
    # 时间/日期碎片：含范围写法「1-2周」「3 个月」「10 日 EMA」
    "time": re.compile(
        r"^\s*[-~—–]?\s*[\d,]*\s*(?:个)?\s*"
        r"(?:分钟|小时|交易日|日|天|周|个月|月|年|季度|期|次|条|家|人|档|位|倍)"
    ),
    "ratio": re.compile(r"^\s*[:：]"),
}

# [DAV-1321 N1] 非价格数字守卫——mention 路径（_token_tail_flags）与
# anchor-level 路径（_is_skip_level_number）共用同一套判定。
_NUMBER_DECIMAL_TAIL = re.compile(r"^\.\d")             # 「6.5%」截出的「6」
_NUMBER_THOUSANDS_TAIL = re.compile(r"^,\d{3}\b")       # 「3,840」截出的「3」
_NUMBER_THOUSANDS_HEAD = re.compile(r"\d,\s*$")         # 「4,480」截出的「480」
_NUMBER_LETTER_HEAD = re.compile(r"[A-Za-z][\-_]*$")    # SMA50 / INV-4 / E-04
_NUMBER_LETTER_TAIL = re.compile(r"^[A-Za-z_]")         # 2026Q1 / LPR_1Y / 0.75x / 25BP
_NUMBER_FRACTION_TAIL = re.compile(r"^\s*/\s*\d{1,3}(?![\d.,])")
# 「1/3」的 1；「141/156.35 元」后跟小数 → 价位列表，不命中
_NUMBER_FRACTION_HEAD = re.compile(r"(?<![\d.,])\d{1,3}\s*/\s*$")
# 「1/3」的 3；「156.35/160」一侧带小数点/千分位 → 不命中
_NUMBER_CURRENCY_TAIL = re.compile(
    r"^\s*(?:美元|港元|欧元|日元|英镑|美分|USD|usd|HKD)"
)                                                        # 「85美元/桶」
_NUMBER_INDICATOR_TAIL = re.compile(
    r"^\s*(?:VWMA|VWAP|EMA|SMA|WMA|DMA|BOLL|ATR|RSI|MACD|MA|PE|PB|PS|PEG|BPs?|bps|基点)\b",
    re.I,
)                                                        # 「10 EMA」「5 PE」
_NUMBER_MD_TAIL = re.compile(r"^-(\d{1,2})(?![\d.])")   # 「07-22」的 07
_NUMBER_MD_HEAD = re.compile(r"(?<![\d.])(\d{1,2})-\s*$")  # 「07-22」的 22
_NUMBER_PERUNIT_TAIL = re.compile(
    r"^\s*元\s*[/／]\s*(?:股|吨|克|公斤|升|瓶|箱|平方米?|平米|人|次|份|例|"
    r"件|条|张|个|度|瓦|[Ww]|[Kk][Ww][Hh]|[Gg]|[Gg][Bb]|年|天|日|月|周|小时|客|户|头|只|艘|架|辆|台)"
)    # 「5400元/年」「9766.67元/吨」「0.05元/W」；「87.47 元 / VWMA」分隔符不命中
_NUMBER_DIVIDEND_HEAD = re.compile(
    r"(?:每\s*\d+\s*股|每\s*股|\d+\s*股)\s*派\s*(?:现金|红利|息|发现金|含税)?\s*$"
)                                                        # 「每10股派5元」
_NUMBER_STOCK_CODE_TAIL = re.compile(
    r"^\s*\.\s*(?:SZ|SH|BJ|HK|of|OF)\b"
)                                                        # 「001258.SZ」证券代码
_NUMBER_QUOTE_HEAD = re.compile(r"[\"'“”‘’]\s*$")
_NUMBER_QUOTE_TAIL = re.compile(r"^\s*[\"'“”‘’]")
# 财务金额语境词（值本身带「元」但量词是利润/金额，不是股价坐标）
_NUMBER_FIN_AMOUNT_WORD = (
    r"毛利|净利|归母净利|利润|盈利|营收|收入|成本|费用|利息|汇兑|"
    r"节税|亏损|分红|派息|薪资|造价|成交额|交易额|货值"
)
_NUMBER_FIN_AMOUNT_HEAD = re.compile(
    rf"(?:{_NUMBER_FIN_AMOUNT_WORD})"
    r"\s*(?:约|达|为|有|近|超|超过|不足|增厚|节约|摊薄|锁定|位于)?\s*$"
)    # 注意不接 \w*——「成交额放量且两次冲锋 40 元」「成本位于千元上方或 900
    # 元中枢」中量词与数字之间隔了叙述词，不能整段吞掉
_NUMBER_FIN_AMOUNT_RANGE = re.compile(
    rf"(?:{_NUMBER_FIN_AMOUNT_WORD})"
    rf"(?:[\d.,\-–~—\s，、/]|约|达|为|有|近|超|超过|不足|增厚|节约|摊薄|"
    rf"锁定|位于|于|至|到|亿|万|元|区间)*$"
)    # 「毛利约1000-2000元」量词区间：关键词到 token 之间只允许金额片段字符
_NUMBER_INDEX_TAIL = re.compile(r"^\s*点(?![位击])")     # 「收于 4668.23 点」
_NUMBER_UNIT_CTX = re.compile(r"每升|每吨|每克|每公斤|每平米|每瓶|每箱|每桶")
_NUMBER_LEVEL_COUNT_TAIL = re.compile(r"^\s*(?:档|板(?!块)|倍)")
_EXCLUDED_EVIDENCE_SPAN = re.compile(
    r"excluded_evidence[\"']?\s*[:：]\s*\[[^\]]*\]", re.I
)


def _nonprice_number_flag(text: str, start: int, end: int) -> Optional[str]:
    """[DAV-1321 N1] token 级非价格数字判定，返回子类名或 None。

    与 _token_tail_flags 互补：这里覆盖「token 形态本身证明不是股价坐标」
    的情形（截断/千分位/编号粘连/分数/日期片段/外币单位/指标后缀/档数/
    单位价格/股利/引号内孤立数字/excluded_evidence 列表），供 mention 与
    anchor-level 两条抽取路径共用。"""
    tail = text[end:end + 12]
    head = text[max(0, start - 12):start]
    tok = text[start:end]
    if _NUMBER_DECIMAL_TAIL.match(tail):
        return "truncated_decimal"
    if _NUMBER_THOUSANDS_TAIL.match(tail) or _NUMBER_THOUSANDS_HEAD.search(head):
        return "thousands_sep"
    if _NUMBER_LETTER_HEAD.search(head):
        return "identifier_or_indicator"
    if _NUMBER_LETTER_TAIL.match(tail):
        return "identifier_suffix"
    if _NUMBER_INDICATOR_TAIL.match(tail):
        return "indicator_or_multiple"
    # 分数「1/3」仅限整数 token——「6.63/6.68 元」这类斜杠价位列表的小数
    # 两侧都是真价位，不得误判为分数。
    if "." not in tok and (
        _NUMBER_FRACTION_TAIL.match(tail) or _NUMBER_FRACTION_HEAD.search(head)
    ):
        return "fraction"
    # MM-DD 日期片段：两侧均为 1~12/1~31 的整数短写法（「07-22」）。
    # 含小数点的区间（45.50-47.50）不命中；「8-9元」之类单边一位数也不命中
    # （要求被判定侧 token 无小数点且 ≤12，另一侧 ≤31）。
    if "." not in tok:
        try:
            tv = int(tok)
        except ValueError:
            tv = 0
        m = _NUMBER_MD_TAIL.match(tail)
        if m and 1 <= tv <= 12 and 1 <= int(m.group(1)) <= 31:
            return "date_fragment_mmdd"
        m = _NUMBER_MD_HEAD.search(head)
        if m and 1 <= int(m.group(1)) <= 12 and 1 <= tv <= 31:
            return "date_fragment_mmdd"
    if _NUMBER_CURRENCY_TAIL.match(tail):
        return "foreign_currency"
    if _NUMBER_PERUNIT_TAIL.match(tail):
        return "per_unit_rate"
    if _NUMBER_DIVIDEND_HEAD.search(head):
        return "dividend_per_share"
    # JSON 引号内孤立数字（"…目标价", "7", "0.8"）：两侧紧贴引号且
    # 不带「元」——引号字符串中部的真实价位（"52.00元"）不误伤。
    if _NUMBER_QUOTE_HEAD.search(head) and _NUMBER_QUOTE_TAIL.match(tail):
        return "json_or_prob_token"
    # [DAV-1321 N1] 金额性量词与下标单位：盈利/毛利/汇兑/利息等财务额
    # （「增厚单车毛利约1000-2000元」）、「收于 4668.23 点」指数点位、
    # 「每升/每吨/每克」单位价格语境。
    if _NUMBER_FIN_AMOUNT_HEAD.search(head) or _NUMBER_FIN_AMOUNT_RANGE.search(head):
        return "fin_amount"
    if _NUMBER_STOCK_CODE_TAIL.match(tail):
        return "stock_code"
    if _NUMBER_INDEX_TAIL.match(tail):
        return "index_points"
    if _NUMBER_UNIT_CTX.search(text[max(0, start - 12):end + 12]):
        return "per_unit_ctx"
    # MANAGER_VERDICT excluded_evidence 列表：被拒证据不算决策驱动，
    # 其中的数字（含「42-50元」估值区间）不登记为 price_ref。
    for sp in _EXCLUDED_EVIDENCE_SPAN.finditer(text):
        if sp.start() <= start and end <= sp.end():
            return "excluded_evidence"
        if sp.start() > end:
            break
    return None

# [P3] 差额语境：数字是「空间/回撤/滑点/价差/差价/幅度」的量值，或「涨/跌 N 元」
# 的变动量——不是价格坐标，不登记。粒子白名单刻意不含 至/到/破/穿：
# 「跌至 45 元」「跌破 2000 元」仍是坐标价。
_DIFF_HEAD = re.compile(
    r"(?:空间|回撤|滑点|价差|差价|幅度|敞口)\s*(?:约|达|为|有|近|超|超过|不足)?\s*$"
    r"|(?<![停板])(?:上涨|下跌|涨|跌)\s*(?:了|达|约|近|超|超过|不足|幅|逾)?\s*$"
)
_DIFF_TAIL = re.compile(r"^\s*元\s*(?:滑点|价差|差价)")

# [P4] 非本股价格：商品/产品价格词出现在数值邻近语境 → 不登记为本股价格坐标。
_NON_STOCK_PRICE_CTX = re.compile(
    r"批发参考价|批发价|出厂价|零售价|指导价|终端价|散瓶|整箱|吨价|公斤价|克价"
)

# [DAV-1246 A1] 影线长度不是价格坐标，不登记。
# 形态一：「留下/留有/形成/引发/长达/达/超过（了）N 元（的）（长）上影线/下影线」——
# 动词必须直接接数字（「留下的 35.36 元上影线供应」中 35.36 是价位，不算长度，
# 故动词后不允许「的」）。形态二：「上影线/下影线（达/长约/长/长度）N 元」。
_SHADOW_LEN_HEAD = re.compile(
    r"(?:留下|留有|形成|引发|长达|达|超过)\s*了?\s*$"
)
_SHADOW_LEN_TAIL = re.compile(
    r"^\s*元\s*的?\s*长?\s*(?:上影线|下影线)"
)
_SHADOW_LEN_HEAD2 = re.compile(
    r"(?:上影线|下影线)\s*(?:长约|长度约|长度|达|长)\s*$"
)

_PERSHARE_FIN = re.compile(
    r"每股净资产|每股收益|每股派|每股股利|每股现金|每股盈余|"
    r"净资产收益|每股未分配|每股公积金|每股经营|(?<![A-Za-z])BPS(?![A-Za-z])"
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
    # 真实股价不会被 亿/万 直接修饰。[DAV-1321] 允许千分位逗号（「4,480亿」）。
    if re.match(r"^\s*[-~—–]?\s*[\d,]*\s*(?:亿|万)", tail):
        return "amount_or_marketcap"
    # [DAV-1246 A1] 影线长度：动词 + N 元（的）（长）影线，或 影线（达/长约）N 元。
    if _SHADOW_LEN_TAIL.match(tail) and _SHADOW_LEN_HEAD.search(head):
        return "shadow_length"
    if _SHADOW_LEN_HEAD2.search(head):
        return "shadow_length"
    # [DAV-1321 N1] 通用非价格数字守卫（截断/千分位/编号/分数/日期/
    # 外币/指标后缀/单位价格/股利/JSON 引号/excluded_evidence）。
    return _nonprice_number_flag(sentence, start, end)


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
    # [DAV-1321 N5] 外币/商品语境改为 token ±12 字窗口：原整句判定把
    # 「若美债收益率…工行前复权股价跌破 8.00 元」整句本股价位全部误删。
    if _FOREIGN_CTX.search(sentence[max(0, start - 12):end + 12]):
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


_DISCLOSURE_KW = re.compile(
    r"大宗|龙虎榜|增持|减持|回购|定增|增发|发行|发股|配售|申购|募资|"
    r"成交|作价|均价|折价|溢价|席位|解禁|上市日|IPO|预案价|对价"
)

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


def _value_is_quote_side(wins: List[str]) -> bool:
    """[DAV-1321 N3] 该值本身是行情报价而非披露价：值紧邻 现价/收盘价/
    当前价/市价/当前 之左（「按当前48.03元股价折算」「高于现价（79.92元）」）。
    只用于 pit_raw 族（回购/发行/定增/增减持）——block_trade/龙虎榜的
    「成交价/收盘价」并列正是披露本体，不适用。"""
    for w in wins:
        if len(w) <= 15:
            continue
        if re.search(r"(?:现价|收盘价|当前价|市价|当前)\s*[（(]?\s*[\d.]*$",
                     w[:len(w) - 15]):
            return True
    return False


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
        # [DAV-1321 N3 修复] 原写法把正则组误写成全角（），| 变成顶层
        # 分支导致「成交/买入/价格」裸词随处命中；改回 (?:…)。
        real = any(re.search(r"大宗(?:交易|平价|成交|折价|溢价|席位|接盘|买入)"
                             r"|(?:交易|平价|折价|溢价|席位|接盘)[^。]{0,10}大宗", w)
                   for w in wins)
        # [DAV-1321 N3] 披露子句语义：该值位于成交/作价/溢折价/席位/对价
        # 描述窗口内才是披露价；「大宗折价盘锁死向 35.00 元进攻」中的 35.00
        # 只是坐标位，不误挂 raw。坐标语境词（支撑/阻力/跟踪/冲击等）出现在
        # 值窗口时否决——「跟踪76.3—76.8元支撑」「向130元上方冲击」不是披露价。
        clause = any(
            re.search(r"成交|作价|万元|亿元|席位|vs\s|VS\s|溢价|折价", w)
            for w in wins
        )
        coord_veto = any(
            re.search(r"支撑|阻力|关口|平台|冲击|反抽|收复|跟踪|止损|止盈|目标|"
                      r"锁死|进攻|考验|下探|上看|挑战|触及|逼近|修复", w)
            for w in wins
        )
        if (real or clause) and not coord_veto:
            verdict, reason = "true", "数值紧邻大宗交易/平价/成交语义"
        elif goods or any(_GOODS_CTX.search(w) for w in wins):
            verdict, reason = "false", "大宗为商品/材料/成本语境，非披露价"
        elif _REAL_BLOCK.search(ctx) or "大宗" in ctx:
            # 句内有大宗语义但该值不在披露子句 → 该值不是披露价
            verdict, reason = "false", "句内有大宗语境但该值非披露价"
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
            if re.search(r"票据|中票|中期票据|永续债|债券|国债|总股本|股本|成本|已完成|已发行|RWA|信贷", ctx):
                verdict, reason = "false", "发行属票据/债券/股本/成本语境，非股票发行披露价"
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
        # [DAV-1321 N3 修复] 同上，全角（）组误写修复为 (?:…)。
        price_like = any(re.search(r"回购(?:价|金额|均价|上限|下限|价格|股份|注销|方案|"
                                   r"拟|计划|公告)|(?:以|按|不超过)[^。]{0,6}元[^。]{0,4}回购", w)
                         for w in wins)
        coord = any(re.search(r"支撑|站稳|安全垫|关口|一线", w) for w in wins)
        if bad:
            verdict, reason = "false", "逆回购/央行/国债回购语境"
        elif _value_is_quote_side(wins):
            verdict, reason = "false", "该值为现价/收盘报价侧，非回购披露价"
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
        # [DAV-1321 N3] 逐值判定：值窗口必须含发行/定价语义（定增/增发/发行/
        # 定价/配售/募资/认购/缴款/摊薄/私募/解禁）。「按当前48.03元股价折算」
        # 与定增同句但不是定增价 → false；「增发对定价 5.11 元」→ true。
        price_like = any(
            re.search(r"定增|增发|发行价|定价|配售|募资|认购|缴款|摊薄|私募|解禁",
                      w)
            for w in wins
        )
        if _value_is_quote_side(wins):
            verdict, reason = "false", "该值为现价/收盘报价侧，非定增发行价"
        elif price_like:
            verdict, reason = "true", "定增/增发发行定价语义贴近该值"
        elif wins:
            verdict, reason = "false", "该值不在定增/增发发行定价子句内"
        else:
            verdict, reason = "ambiguous", "值不可定位，定增语境无法定夺"

    # [DAV-1321 N3] ambiguous 收紧：披露关键词须落在该值自身 ±15 字
    # 窗口内，否则按 false 处理——「大宗减持概率低」句中的 EMA 值、
    # 「永续债发行」句中的 RWA 数字不再因同句关键词被误挂 provenance。
    # verdict=true（值已在披露子句内）不受影响：大宗成交价 39.60 元照常 true。
    if verdict == "ambiguous" and wins:
        # 关键词用跨 dtype 同族披露词集合：发行→发股/发行价、大宗→成交价/折价
        # 等，防止「重组发股底价 5.30→5.11 元」「折价 7.33%（1218.61 元）」
        # 这类真披露值因 dtype 词恰不在窗口而漏挂。
        kw_hit = any(_DISCLOSURE_KW.search(w) for w in wins)
        if not kw_hit:
            verdict, reason = "false", "披露关键词不在该值窗口内"

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
    (re.compile(r"(?:200\s*日?\s*(?:SMA|均线|MA)|SMA\s*[-_]?\s*200|MA\s*200|年线|牛熊线)", re.I), "close_200_sma"),
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


# [DAV-1321 N5] 「A-B 元」区间起点补登记：原本只有带「区间」锚词的区间
# 两端才进 refs，裸写区间（「反抽401.00-404.36元」「46.00-46.50元」）的起点
# 会漏登记导致 unbacked_executable_level。起点仍须过全部伪命中守卫。
_RANGE_START_PATTERN = re.compile(
    r"(?<![\d.,])(\d+(?:\.\d+)?)\s*[-~—–](?=\s*\d+(?:\.\d+)?\s*元)"
)


def _extract_price_values(
    sentence: str, *, include_range_start: bool = False
) -> List[Tuple[float, int, int]]:
    """Extract (value, num_start, num_end) price mentions from a sentence.

    [C1]/[C4]：命中后按 token 语境过滤伪命中（单位/日期/JSON/外币/每股财务），
    并把共享可执行价位词（含区间写法）并入抽取。position 为数字 token 起点；
    [DAV-1321] 返回值追加 num_end，供口径右侧括号（「N 元（前复权）」）判定。
    ``include_range_start``：[DAV-1321 N5] 裸元区间起点（「A-B 元」的 A）
    仅在句内/文档内有口径可传递或技术报告自带口径时才登记——否则登记的
    只是无法归因的模型自拟值，反而制造新的 missing_basis。"""
    found: List[Tuple[float, int, int]] = []
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
        found.append((value, nstart, nend))

    for m in _PER_SHARE_PATTERN.finditer(sentence):
        num = m.group(1) or m.group(2)
        ns = m.start(1) if m.group(1) else m.start(2)
        ne = m.end(1) if m.group(1) else m.end(2)
        _add(num, m.start(), m.end(), ns, ne)
    for m in _BARE_YUAN_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(), m.end(), m.start(1), m.end(1))
    for m in _PRICE_KEYWORD_PATTERN.finditer(sentence):
        _add(m.group(1), m.start(), m.end(), m.start(1), m.end(1))

    # [DAV-1321 N5] 裸元区间起点（「A-B 元」中的 A），受调用方开关控制。
    if include_range_start:
        for m in _RANGE_START_PATTERN.finditer(sentence):
            _add(m.group(1), m.start(1), m.end(1), m.start(1), m.end(1))

    # [C4] shared executable parser：gate 价位词并入 registry 抽取。
    # [DAV-1255] 同一锚点窗口扫描：日期/周期/金额碎片跳过后继续取真实价位。
    for value, nstart, nend in _iter_anchor_levels(
        sentence, _LEVEL_PATTERN_EXTENDED
    ):
        if value <= 0:
            continue
        if _is_false_mention(sentence, nstart, nend, value):
            continue
        if _is_false_level(sentence, nstart, nend, value):
            continue
        if any(abs(v - value) <= _VALUE_MATCH_TOLERANCE for v, _p, _e in found):
            continue
        found.append((value, nstart, nend))

    found.sort(key=lambda item: item[1])
    return found


def _detect_disclosure_type(sentence: str) -> Optional[str]:
    """[C2] 句级候选 dtype：最长匹配的关键词类型。仅作候选——是否挂到
    某个 ref 由调用方按该 ref 的值再过 classify_typed_disclosure 决定
    （DAV-1321 N3：typed_disclosure 是逐值判定，不再句级连坐）。"""
    best: Optional[Tuple[int, str]] = None  # (keyword length, type)
    for kw, dtype in TYPED_DISCLOSURE_KEYWORDS.items():
        if kw in sentence:
            if best is None or len(kw) > best[0]:
                best = (len(kw), dtype)
    if best is None:
        return None
    return best[1]


# pit_raw 族披露类型：价格为当时成交/发行披露，值若是现价/收盘报价侧
# 数字则一律非披露价（block_trade/龙虎榜的成交价并列是披露本体，不适用）。
_PIT_DTYPE_QUOTE_GUARD = frozenset(
    ("repurchase", "issuance", "private_placement",
     "shareholder_increase", "shareholder_decrease")
)


def _value_disclosure_type(sentence: str, dtype: str, value: float) -> Optional[str]:
    """[DAV-1321 N3] 按该值自身的 ±15 字窗口判定它是否该类披露价。"""
    verdict = classify_typed_disclosure(
        {"context": sentence, "disclosure_type": dtype, "value": value}
    )
    if verdict["verdict"] == "false":
        return None
    if dtype in _PIT_DTYPE_QUOTE_GUARD and _value_is_quote_side(
        _num_windows(sentence, value)
    ):
        return None
    return dtype


def _has_coordinate_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in COORDINATE_KEYWORDS)


def _has_derived_keyword(sentence: str) -> bool:
    return any(kw in sentence for kw in DERIVED_KEYWORDS)


def _conversion_target_basis(sentence: str) -> Optional[str]:
    """Target basis declared by a conversion sentence (前复权→qfq; 不复权→raw)."""
    return _declared_basis(sentence)


def _basis_token_basis(token: str) -> Optional[str]:
    """[DAV-1321 N4] 口径 token → canonical basis。后复权无对应 canonical
    basis，不映射；字面 ``raw`` 只在词边界内识别（避免 pit_raw 内部误中）。"""
    t = (token or "").lower()
    if t in ("不复权", "未复权"):
        return PRICE_BASIS_RAW
    if t == "pit_raw":
        return PRICE_BASIS_PIT_RAW
    if t in ("前复权", "vendor_qfq", "qfq"):
        return PRICE_BASIS_VENDOR_QFQ
    return None


def _declared_basis(window: str) -> Optional[str]:
    """Basis explicitly declared by sentence keywords (deterministic text marker,
    not model self-certification — the label comes from the declared word).

    [DAV-1321 N4] 追加字面 basis token：``pit_raw`` → pit_raw、
    ``vendor_qfq``/``qfq`` → qfq、词边界内裸 ``raw`` → raw。
    """
    if "不复权" in window or "未复权" in window:
        return PRICE_BASIS_RAW
    low = window.lower()
    if "pit_raw" in low:
        return PRICE_BASIS_PIT_RAW
    if "前复权" in window or "vendor_qfq" in low or "qfq" in low:
        return PRICE_BASIS_VENDOR_QFQ
    if re.search(r"(?<![\w])raw(?![\w])", low):
        return PRICE_BASIS_RAW
    return None


# [DAV-1321 N4] 数字右侧紧邻括号口径：「48.50 元（前复权）」「94.00 元（前复权）」。
_DECLARED_TAIL_RE = re.compile(
    r"^\s*元?\s*[（(]\s*(前复权|不复权|未复权|vendor_qfq|pit_raw|qfq)", re.I
)

# [DAV-1321 N4] 文档级口径声明：仅认「一律/一概/全部/所有/统一/以下/
# 本文/本报告/本计划/本方案 …前复权」这类全文档 scope 词引导的声明。
# 「回购均价（pit_raw 坐标）」这类单值标注不是文档声明，不匹配；
# pit_raw/raw 是披露/原始口径，不会成为「全文一律」的坐标声明对象，
# 故文档级 token 只收前复权/不复权/未复权/vendor_qfq/qfq。
_DOC_DECL_RE = re.compile(
    r"(?:一律|一概|全部|所有|统一|以下|本文|本报告|本计划|本方案)"
    r"[^。\n]{0,15}?"
    r"(前复权|不复权|未复权|vendor_qfq|qfq)",
    re.I,
)

_BASIS_TOKEN_RE = re.compile(r"不复权|未复权|前复权|vendor_qfq|pit_raw|qfq", re.I)


# 值绑定判定：口径词紧贴一个数字（「未复权价 84.03 元」「前复权价 8.09」）
# 时，它只声明该值的坐标，不构成句/段级传播源——否则会把句内另一口径的
# 现价也传染成同 basis，抹掉 same_sentence_mixed_basis 应捕捉的混用。
_BASIS_VALUE_BOUND_RE = re.compile(
    r"^\s*(?:价格|价|坐标|位)?\s*(?:为|约|[:：])?\s*\d"
)


def _free_basis_tokens(text: str) -> List[str]:
    """只保留非值绑定的口径 token（声明语义，不紧跟数字）。"""
    toks: List[str] = []
    for m in _BASIS_TOKEN_RE.finditer(text or ""):
        tail = (text or "")[m.end():m.end() + 12]
        if _BASIS_VALUE_BOUND_RE.match(tail):
            continue
        toks.append(m.group(0))
    return toks


def _single_declared_basis(text: str) -> Optional[str]:
    """[DAV-1321 N4] 文本内所有自由口径 token 收敛到唯一 basis 才返回；
    多种口径并存（如坐标隔离声明句）→ None，不传播。"""
    bases = {
        _basis_token_basis(t)
        for t in _free_basis_tokens(text)
    }
    bases.discard(None)
    if len(bases) == 1:
        return next(iter(bases))
    return None


def _doc_declared_basis(text: str) -> Optional[str]:
    bases = {
        _basis_token_basis(m.group(1))
        for m in _DOC_DECL_RE.finditer(text or "")
        # 排除值绑定 token（「未复权价 84.03」）
        if not _BASIS_VALUE_BOUND_RE.match((text or "")[m.end():m.end() + 12])
    }
    bases.discard(None)
    if len(bases) == 1:
        return next(iter(bases))
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


def _is_conversion_sentence(ctx: str) -> bool:
    """真转换句：复权语义 + 换算动词同现。该句内的 raw/qfq 并存是
    「换算自 raw=X 得 qfq=Y」的合法对照，不是坐标混用。"""
    return bool(
        _REAL_CONVERSION_CTX.search(ctx or "")
        and _CONVERSION_VERB_PATTERN.search(ctx or "")
    )


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
        # [DAV-1321 N4] 文档级口径声明（边界=本报告字段文本）。
        doc_basis = _doc_declared_basis(text)
        for sentence in _split_sentences(text):
            # [DAV-1321 N4] 句级口径：句内只出现一种口径声明时，传播到
            # 句内未单独声明的 ref；多种口径并存（坐标隔离声明）不传播。
            sentence_basis = _single_declared_basis(sentence)
            mentions = _extract_price_values(                 # [C1]+[C4]+[N5]
                sentence,
                include_range_start=bool(
                    is_technical or sentence_basis or doc_basis
                ),
            )
            if not mentions:
                continue
            disclosure_type = _detect_disclosure_type(sentence)  # [C2] 候选
            derived = _has_derived_keyword(sentence)
            sentence_dates = _extract_dates(sentence)
            sentence_as_of = sentence_dates[0] if sentence_dates else None

            sentence_ref_ids: List[Tuple[str, float, str]] = []
            for value, pos, pend in mentions:
                mention_window = sentence[max(0, pos - 15):pos]
                declared = _declared_basis(mention_window)
                declared_scope = ""
                if declared is None:
                    # 「48.50 元（前复权）」式紧邻括号口径。
                    tm = _DECLARED_TAIL_RE.match(sentence[pend:pend + 14])
                    if tm:
                        declared = _basis_token_basis(tm.group(1))
                        declared_scope = "tail"
                if declared is None:
                    declared = sentence_basis
                    declared_scope = "sentence" if declared else ""
                if declared is None:
                    declared = doc_basis
                    declared_scope = "doc" if declared else ""
                # [DAV-1321 N3] typed_disclosure 逐值判定：dtype 是句级候选，
                # 该 ref 的值过 verdict 规则，false → 不挂 provenance。
                dtype = (
                    _value_disclosure_type(sentence, disclosure_type, value)
                    if disclosure_type is not None
                    else None
                )
                ref: Dict[str, Any] = {
                    "ref_id": _next_id(),
                    "value": value,
                    "basis": PRICE_BASIS_UNSPECIFIED,
                    "source": report_name,
                    "provenance": "model_text",
                    "as_of": sentence_as_of,
                    "context": sentence[:120],
                    # DAV-1249: 返修/审计需要完整原句（context 截断 120 字
                    # 可能丢失价格本身）；仅追溯用，不参与判定。
                    "sentence": sentence,
                }
                if dtype is not None:
                    ref["basis"] = DISCLOSURE_TYPE_BASIS[dtype]
                    ref["provenance"] = f"typed_disclosure:{dtype}"
                    ref["disclosure_type"] = dtype
                elif declared is not None:
                    ref["basis"] = declared
                    ref["provenance"] = (
                        f"declared_basis:{declared}"
                        if not declared_scope
                        else f"declared_basis:{declared_scope}:{declared}"
                    )
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
                        if target is not None and dtype is None:
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
            # [DAV-1321] 真转换句是契约明示的合法双坐标展示
            if len(bases) > 1 and not _is_conversion_sentence(_ctx):
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
        non_qfq = [
            r for r in report_refs
            if r["basis"] in (PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW)
            and not _is_conversion_sentence(r.get("context") or "")
        ]
        if not non_qfq:
            continue
        for ref in report_refs:
            if ref["basis"] != PRICE_BASIS_VENDOR_QFQ:
                continue
            if _is_conversion_sentence(ref.get("context") or ""):
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
