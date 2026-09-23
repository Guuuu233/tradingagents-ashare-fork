"""DAV-1225 逐条分类规则（全部写成代码，无人工硬编码标签）。

覆盖四类对象的分类器：
- 473 条 decision_driving unspecified refs → 六类；
- 111 条 typed-disclosure refs → true / false / ambiguous；
- 45 条 invalid_conversion refs → 四类；
- 52 条 executable-level 命中 → 五类。

所有判定输出 (category, subtype, reason, evidence)；source-backed 只认
``pool.named_field_hits`` 的字段级 provenance，纯同值记 coincidence。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from tradingagents.agents.utils.price_ref_registry import (
    _BARE_YUAN_PATTERN,
    _PER_SHARE_PATTERN,
    _PRICE_KEYWORD_PATTERN,
    TYPED_DISCLOSURE_KEYWORDS,
)

from .pool import SnapshotPool, named_field_hits  # noqa: F401  (re-export)

# ---------------------------------------------------------------------------
# 抽取式 provenance：复用 trunk 正则，标出每个 ref 值由哪条 pattern 命中
# ---------------------------------------------------------------------------

def mention_patterns(sentence: str, value: float, tol: float = 5e-3) -> List[str]:
    pats: List[str] = []
    for m in _PER_SHARE_PATTERN.finditer(sentence):
        num = m.group(1) or m.group(2)
        try:
            if abs(float(num) - value) <= tol:
                pats.append("per_share")
        except (TypeError, ValueError):
            pass
    for m in _BARE_YUAN_PATTERN.finditer(sentence):
        try:
            if abs(float(m.group(1)) - value) <= tol:
                pats.append("bare_yuan")
        except (TypeError, ValueError):
            pass
    for m in _PRICE_KEYWORD_PATTERN.finditer(sentence):
        try:
            if abs(float(m.group(1)) - value) <= tol:
                pats.append("keyword")
        except (TypeError, ValueError):
            pass
    return sorted(set(pats)) or ["unknown"]


def _match_spans(sentence: str, value: float, tol: float = 5e-3) -> List[Tuple[int, int, str]]:
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


# ---------------------------------------------------------------------------
# non-price / foreign 词表
# ---------------------------------------------------------------------------

_UNIT_TAIL = {
    # 允许 token 后被截断的剩余数字再接 %（如「50%」被回溯截出 5、LaTeX \%）
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

_AMOUNT_CTX = re.compile(
    r"市值|成交额|成交量|净利|营收|利润|余额|净流入|净流出|流入|流出|金额|"
    r"融资|融券|保证金|规模|总额|市值|资金|成交额|建仓金额|仓位|总市值|流通市值"
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

# [R3] derived 判定绑定到数字本身：同一子句、数字前 ~25 字内的估值算术
# 词才算。「假设/情景/悲观/乐观/防御/底线」等弱词不得单独触发。
_STRONG_DERIVED = re.compile(
    r"PE|PB|市盈率|市净率|折合|折算|换算|测算|估算|估值|倍|净利|利润|"
    r"市值|每股股价|公允|隐含|理论价|内在价值|DCF|贴现|安全边际|ROE"
)
# 「对应」必须落在 股价/每股/市值/估值 上才算强词（对应了获利盘在 70 元关口
# 之类的「对应」是弱词）
_DUIYING_DERIVED = re.compile(r"对应[^。]{0,8}(?:股价|每股|市值|估值|元)")
_SUBCLAUSE_SPLIT = re.compile(r"[，。；;：:、|（）()\[\]【】\n\r]+")


def _pre_token_window(sentence: str, start: int) -> str:
    left = sentence[:start]
    seg_start = 0
    for m in _SUBCLAUSE_SPLIT.finditer(left):
        seg_start = m.end()
    return sentence[seg_start:start][-25:]


def is_derived_value(sentence: str, value: float, tol: float = 5e-3) -> bool:
    """token 级 derived 判定：数字前 25 字内（同一子句）出现估值算术词。"""
    for s, e, _p in _match_spans(sentence or "", value, tol):
        window = _pre_token_window(sentence, s)
        if _STRONG_DERIVED.search(window) or _DUIYING_DERIVED.search(window):
            return True
    return False


_QUOTE_WORDS = re.compile(r"现价|收盘|报收|股价|运行于|位于|关口|平台|报价|价位")


def looks_like_actual_quote(context: str, value: float,
                            pool: "SnapshotPool", tol: float = 5e-3) -> bool:
    """[R3] 真报价保护：token 窗口含报价语义词且值匹配 pool 任一具名字段 →
    这是市场报价（coordinate），不得因同句估值算术词被判 derived。"""
    for s, e, _p in _match_spans(context or "", value, tol):
        w = context[max(0, s - 15):e + 10]
        if _QUOTE_WORDS.search(w) and pool.fields_matching(value, tol):
            return True
    return False


_DERIVED_CTX = _STRONG_DERIVED  # 兼容引用；实际判定一律走 is_derived_value


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


def classify_unspecified_ref(ref: Dict[str, Any], pool: SnapshotPool) -> Dict[str, Any]:
    """473 条 unspecified decision-driving ref 的六类分类。"""
    ctx = ref.get("context") or ""
    value = ref.get("value")
    result: Dict[str, Any] = {
        "ref_id": ref.get("ref_id"),
        "value": value,
        "source": ref.get("source"),
        "context": ctx,
        "patterns": mention_patterns(ctx, value) if isinstance(value, (int, float)) else ["unknown"],
        "named_field_hits": [],
        "coincidence_fields": [],
        "category": "ambiguous",
        "subtype": "",
        "reason": "",
    }
    if not isinstance(value, (int, float)):
        result["reason"] = "ref 无数值"
        return result

    hits = named_field_hits(ctx, value, pool)
    result["named_field_hits"] = hits
    pr = pool.price_range()
    coin = [nv.field for nv in pool.fields_matching(value)]
    result["coincidence_fields"] = coin

    spans = _match_spans(ctx, value)
    token_flags = {f for s, e, _p in spans for f in [_token_tail_flags(ctx, s, e)] if f}
    foreign = bool(_FOREIGN_CTX.search(ctx))
    derived_tok = is_derived_value(ctx, value)                 # [R3] token 级
    prov = ref.get("provenance") or ""

    # 1) token 级结构性非价格（百分比/倍数/股数/日期碎片/每股财务/金额）
    if token_flags:
        result["category"] = "non_price_false_extraction"
        result["subtype"] = sorted(token_flags)[0]
        result["reason"] = f"数字 token 紧跟非价格单位/语境：{sorted(token_flags)}"
        return result
    if _JSON_BLOB.search(ctx) and not re.search(r"元", ctx):
        result["category"] = "non_price_false_extraction"
        result["subtype"] = "json_or_prob_token"
        result["reason"] = "MANAGER_VERDICT/JSON 片段中的概率/参数数字"
        return result

    # 2) foreign quote：明确外币/境外资产语境，且该值落在境外标的价格语义上
    if foreign:
        result["category"] = "foreign_quote"
        result["subtype"] = "foreign_or_commodity"
        result["reason"] = "语境含美元/美股/大宗国际品等境外报价标记"
        return result

    # 3) source-backed：字段级 provenance（指标名/日期+OHLC/涨跌停）
    if hits:
        result["category"] = "real_coordinate_source_backed"
        result["subtype"] = "field_level_match"
        result["reason"] = f"指名字段命中 {hits}"
        return result

    # 3b) 涨跌停语义但值不匹配 frozen limit 字段 → 真坐标无来源（不是派生）
    if re.search(r"涨停|跌停", ctx) and pr and pr[0] * 0.5 <= value <= pr[1] * 1.6:
        result["category"] = "real_coordinate_source_unbacked"
        result["subtype"] = "limit_mismatch"
        result["reason"] = "涨跌停语境但值与 computed limit 不符"
        return result

    # 3c) [R3] 真报价保护：报价语义词 + pool 具名字段同值 → 真坐标，不算 derived
    if looks_like_actual_quote(ctx, value, pool):
        result["category"] = "real_coordinate_source_unbacked"
        result["subtype"] = "actual_quote_no_field_name"
        result["reason"] = "报价语义词 + pool 同值，但未指名字段（coincidence 级）"
        return result

    # 4) derived valuation estimate：估值算术词绑定到该数字（R3），
    #    或 trunk 已标 derived provenance 且同句确有复权/折算动词
    if derived_tok or (prov.startswith("derived") and _CONVERSION_VERB_RE.search(ctx)):
        result["category"] = "derived_valuation_estimate"
        result["subtype"] = "valuation_arithmetic"
        result["reason"] = "数字前 25 字内（同子句）存在估值算术词，派生估计值"
        return result

    # 5) 真 A 股坐标但无字段级来源
    pr = pr or pool.price_range()
    looks_price = "元" in ctx or any(
        kw in ctx for kw in ("支撑", "压力", "阻力", "现价", "价", "止损", "止盈", "目标", "锚")
    )
    if looks_price and pr and pr[0] * 0.5 <= value <= pr[1] * 1.6:
        result["category"] = "real_coordinate_source_unbacked"
        result["subtype"] = "coincidence" if coin else "no_pool_match"
        result["reason"] = (
            f"价格语义明确且落在样本价格区间 {pr}，但无字段级 provenance"
            + (f"；同值巧合字段 {coin}" if coin else "")
        )
        return result

    result["reason"] = "以上规则均不能定性"
    return result


# ---------------------------------------------------------------------------
# typed disclosure 复核
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

_COMPANY_NAME = re.compile(r"[\u4e00-\u9fa5]{2,7}(?:技术|科技|电子|光电|股份|集团|"
                           r"生物|医药|能源|精工|智能|流体|控制|证券|银行|保险|新材)")


def _num_windows(ctx: str, value: float, half: int = 15) -> List[str]:
    """返回每个与 value 同值数字 token 的 ±half 字窗口。"""
    wins = []
    for m in re.finditer(r"\d+(?:\.\d+)?", ctx or ""):
        try:
            if abs(float(m.group(0)) - value) <= 5e-3:
                wins.append(ctx[max(0, m.start() - half):m.end() + half])
        except ValueError:
            pass
    return wins


def classify_typed_disclosure(ref: Dict[str, Any],
                              stock_name: Optional[str] = None) -> Dict[str, Any]:
    """typed_disclosure ref → verdict true / false / ambiguous。

    [R2] 判定对象收紧为「该 ref 的值是否该类披露价」：
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
            m = re.search(r"([\u4e00-\u9fa5]{2,7})\s*(?:科创板|创业板|主板)?\s*"
                          r"(?:开启|开始)?\s*申购|([\u4e00-\u9fa5]{2,7})[^。]{0,10}发行价", ctx)
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
# invalid_conversion 复核
# ---------------------------------------------------------------------------

_REAL_CONV = re.compile(r"复权|qfq|前复权|不复权|因子")
_CONV_VERB = re.compile(r"换算|折算|折合|转换")
_VAL_ARITH = re.compile(r"每股|股价|市值|PE|PB|倍|净利|估值|测算")
_VOL_ATR = re.compile(r"ATR|量|波幅|成交量|换手")
_CONVERSION_VERB_RE = re.compile(r"换算|折算|折合|复权|除权")


def classify_invalid_conversion(ref: Dict[str, Any]) -> Dict[str, Any]:
    ctx = ref.get("context") or ""
    has_real = bool(_REAL_CONV.search(ctx)) and bool(_CONV_VERB.search(ctx))
    has_val = bool(_CONV_VERB.search(ctx)) and bool(_VAL_ARITH.search(ctx))
    has_vol = bool(_CONV_VERB.search(ctx)) and bool(_VOL_ATR.search(ctx))
    if has_real:
        cat, reason = "real_raw_qfq_conversion", "含复权/qfq + 换算动词的真转换句式"
    elif has_val:
        cat, reason = "valuation_arithmetic", "折合/折算用于估值算术（每股/市值/PE），非复权转换"
    elif has_vol:
        cat, reason = "volume_or_atr_arithmetic", "折合/折算用于量/ATR 算术"
    elif _CONV_VERB.search(ctx):
        cat, reason = "ambiguous", "有折算动词但无复权语义、无明确算术对象"
    else:
        cat, reason = "ambiguous", "conversion 标记来源不明"
    return {
        "ref_id": ref.get("ref_id"), "value": ref.get("value"),
        "source": ref.get("source"), "context": ctx,
        "category": cat, "reason": reason,
    }


# ---------------------------------------------------------------------------
# executable level 复核
# ---------------------------------------------------------------------------

def classify_executable_level(value: float, field: str, text: str,
                              match_start: int, match_end: int,
                              pool: Optional[SnapshotPool]) -> Dict[str, Any]:
    """52 条 executable-level 命中分类。"""
    ctx = text[max(0, match_start - 60):match_end + 30]
    tail = text[match_end:match_end + 12]
    category, subtype, reason = "other_false_positive", "", ""

    if _UNIT_TAIL["percent"].match(tail) or re.search(r"[%％]", text[match_end:match_end + 3]):
        category, subtype, reason = "percentage", "pct_tail", "命中值紧跟 %"
    elif re.match(r"^\.\s", tail) or re.search(rf"(?:^|\n)\s*{re.escape(str(int(value)))}\.\s", text[:match_end + 4][-80:]):
        category, subtype, reason = "markdown_list_number", "list_item", "Markdown 列表序号（N. ）"
    elif _UNIT_TAIL["shares"].match(tail) or re.search(r"(?:亿|万)\s*(?:股|元)|股$", tail[:4]):
        category, subtype, reason = "shares_amount_date", "shares_or_amount", "命中值为股数/金额"
    elif _DATE_TOKEN.search(text[max(0, match_start - 6):match_end + 6]) or (1900 <= value <= 2100 and float(value).is_integer()):
        category, subtype, reason = "shares_amount_date", "date_year", "命中值为日期/年份成分"
    else:
        pr = pool.price_range() if pool else None
        if pr and pr[0] * 0.5 <= value <= pr[1] * 1.6:
            category, subtype, reason = "real_executable_price", "in_price_range", \
                f"命中值落在样本价格区间 {pr} 且语境为价位词"
        else:
            category, subtype, reason = "other_false_positive", "unclassified", \
                "价位词命中但非明确价格"
    return {
        "value": value, "field": field, "category": category,
        "subtype": subtype, "reason": reason, "context": ctx.strip(),
    }
