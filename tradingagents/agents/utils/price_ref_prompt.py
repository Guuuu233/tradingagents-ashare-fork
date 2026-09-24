"""DAV-1246 B 部分 — 可引用价位表 + 价格书写规则（price_ref 提示词 v1）。

生成侧价格来源标注：在各角色构造提示词时，用本次运行 data_collector 的
pool（``stock_data`` 与 ``indicators``，同 ``build_market_data_pool`` 口径）
生成「可引用价位表」，连同一段共享价格书写规则注入系统提示。

契约原则不变：模型不能自证口径——表中的每一行名称/日期/数值必须能被现有
C5 桥接（``named_field_hits``）识别，不能桥接的行不入表。pool 不可用时
不注入表，改用降级文案（不得给出任何支撑/压力/目标/止损/入场价位）。

注入由图入口在收集完 pool 后一次性完成：``attach_price_ref_prompt_block``
把 ``{"zh": ..., "en": ...}`` 写入 ``state[PRICE_REF_PROMPT_STATE_KEY]``，
各角色节点用 ``price_ref_prompt_suffix(state, config)`` 取对应语言文本
追加到系统提示。``result_data.price_ref_prompt_version`` 仅用于追溯。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, MutableMapping, Optional

from tradingagents.agents.utils.price_ref_registry import (
    build_market_data_pool,
    named_field_hits,
)

PRICE_REF_PROMPT_VERSION = "price_ref_prompt.v1"

# state 上承载价位表提示块的键（{"zh": str, "en": str}）。
PRICE_REF_PROMPT_STATE_KEY = "price_ref_prompt_block"

# 注入角色（B1 规定清单）。market / volume_price 不注入：两者的报告建立在
# vendor qfq 行情通道上，契约本就按 technical report 处理，冻结语料违规
# 统计中也不含这两个来源，注入只会增加提示词噪音。
PRICE_REF_PROMPT_ROLES = (
    "fundamentals",
    "news",
    "macro",
    "smart_money",
    "social",
    "research_manager",
    "trader",
    "aggressive_debator",
    "conservative_debator",
    "neutral_debator",
    "risk_manager",
)

_NO_TABLE_ZH = "本次无可引用价位表，不得给出任何支撑、压力、目标、止损、入场价位。"
_NO_TABLE_EN = (
    "No reference price table is available for this run; do not state any "
    "support, resistance, target, stop-loss or entry price."
)

_RULES_ZH = """\
【价格书写规则】
1. 引用行情或指标价位时，只能从价位表原样抄写数值，不得取整，不得用「附近」「一线」代替，并写出表中的名称和日期。例：{examples}。
2. 支撑、压力、防线、目标、止盈、止损、入场、加减仓等价位，只能用价位表中的数值。需要区间时，两端都必须是表中数值。
3. 估值推算写在同一分句内，采用固定句式：「按 N 倍 PE（或 PB 等）对应股价 X 元」，或「每股收益 Y 元 × N 倍 = X 元」。
4. 表外价位（整数关口、心理价位、自拟区间）不得作为上述任何价位出现。
5. 差额写成百分比，或写成「价差 N 元」。
6. 自行标注「前复权 / vendor_qfq」不构成来源证明，校验器只认价位表中的数值和名称。"""

_RULES_EN = """\
[Price-writing rules]
1. When quoting a market or indicator price, copy the value verbatim from the price table — no rounding, no "near"/"around" wording — and state the table's name and date. Example: {examples}.
2. Support, resistance, floor, target, take-profit, stop-loss, entry and position-sizing prices may only use values in the table; both ends of any range must be table values.
3. A valuation estimate must stay inside one clause using the fixed forms: "at N× PE (or PB etc.) the price is X" or "EPS Y × N = X".
4. Off-table prices (round-number levels, psychological levels, self-invented ranges) must not appear as any of the prices above.
5. Write differences as percentages, or as "price gap N".
6. Self-labeling "qfq / vendor_qfq" is not provenance — the validator only accepts values and names in the price table."""


def _fmt_num(value: float) -> str:
    """保留 pool 解析出的原始小数位（float 的最短精确表示）。"""
    return str(value)


def _md(date_str: str) -> str:
    """'2026-08-14' -> '8月14日'。"""
    try:
        _y, m, d = date_str.split("-")
        return f"{int(m)}月{int(d)}日"
    except Exception:
        return date_str


def build_price_ref_table(source: Mapping[str, Any], symbol: str,
                          trade_date: str) -> Optional[List[Dict[str, Any]]]:
    """从 pool 源数据生成可引用价位表行。

    每行 ``{"label", "date", "value", "context"}``；``context`` 即表行文本，
    逐行经 ``named_field_hits`` 验证可桥接后才保留。pool 不可用返回 None。
    """
    try:
        pool = build_market_data_pool(source, symbol, trade_date)
    except Exception:
        return None

    candidates: List[Dict[str, Any]] = []
    bars = pool.bars
    if not bars:
        return None

    last = bars[-1]
    last_label = {"open": "开盘", "high": "最高", "low": "最低", "close": "收盘"}
    for f, word in last_label.items():
        if isinstance(last.get(f), (int, float)):
            candidates.append({
                "label": f"{_md(last['date'])} {word}",
                "date": last["date"],
                "value": last[f],
            })

    for b in bars[-5:]:
        for f, word in (("high", "最高"), ("low", "最低")):
            if isinstance(b.get(f), (int, float)):
                candidates.append({
                    "label": f"{_md(b['date'])} {word}",
                    "date": b["date"],
                    "value": b[f],
                })

    indicator_labels = (
        ("close_10_ema", "10 日 EMA"),
        ("close_50_sma", "50 日 SMA"),
        ("close_200_sma", "200 日 SMA"),
        ("vwma", "VWMA"),
        ("boll_ub", "布林上轨"),
        ("boll", "布林中轨"),
        ("boll_lb", "布林下轨"),
    )
    for key, label in indicator_labels:
        v = pool.indicators.get(key)
        if isinstance(v, (int, float)):
            candidates.append({"label": label, "date": trade_date, "value": v})

    for nv in pool.named_values:
        if nv.field.startswith("derived.limit_up@"):
            candidates.append({
                "label": "涨停价", "date": nv.as_of or last["date"], "value": nv.value,
            })
        elif nv.field.startswith("derived.limit_down@"):
            candidates.append({
                "label": "跌停价", "date": nv.as_of or last["date"], "value": nv.value,
            })

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for c in candidates:
        # 「最新交易日 OHLC」与「最近 5 日高低」在末根 bar 上语义重叠，去重。
        key = (c["label"], c["date"])
        if key in seen:
            continue
        seen.add(key)
        context = f"{c['label']} {_fmt_num(c['value'])} 元"
        if named_field_hits(context, c["value"], pool):
            rows.append({**c, "context": context})
    return rows


def build_price_ref_prompt_block(source: Optional[Mapping[str, Any]],
                                 symbol: str,
                                 trade_date: str) -> Dict[str, str]:
    """生成注入系统提示的 zh/en 提示块（含降级文案路径）。"""
    rows = (
        build_price_ref_table(source, symbol, trade_date)
        if isinstance(source, Mapping)
        else None
    )
    if not rows:
        return {
            "zh": f"【可引用价位表】\n{_NO_TABLE_ZH}\n\n{_RULES_ZH.format(examples='—')}",
            "en": f"[Reference price table]\n{_NO_TABLE_EN}\n\n{_RULES_EN.format(examples='—')}",
        }

    table_lines = "\n".join(f"- {r['context']}" for r in rows)
    # 规则例句直接引用表行原文，保证示例中的名称/数值一定在表内。
    ind_row = next(
        (r for r in rows if r["label"] in
         ("10 日 EMA", "50 日 SMA", "200 日 SMA", "VWMA",
          "布林上轨", "布林中轨", "布林下轨")),
        None,
    )
    low_row = next((r for r in rows if r["label"].endswith("最低")), None)
    ex_rows = [r for r in (ind_row, low_row) if r is not None] or rows[:1]
    ex_zh = "、".join(f"「{r['context']}」" for r in ex_rows[:2])
    ex_en = ", ".join(f"\"{r['context']}\"" for r in ex_rows[:2])
    rules_zh = _RULES_ZH.format(examples=ex_zh)
    rules_en = _RULES_EN.format(examples=ex_en)
    return {
        "zh": (
            "【可引用价位表】\n"
            "以下价位来自本次运行行情数据，引用时原样抄写数值并写出名称与日期：\n"
            f"{table_lines}\n\n{rules_zh}"
        ),
        "en": (
            "[Reference price table]\n"
            "The prices below come from this run's market data; quote values "
            "verbatim with their name and date:\n"
            f"{table_lines}\n\n{rules_en}"
        ),
    }


def attach_price_ref_prompt_block(
    state: MutableMapping[str, Any],
    source: Optional[Mapping[str, Any]],
    *,
    symbol: Optional[str] = None,
    trade_date: Optional[str] = None,
) -> bool:
    """把价位表提示块挂到 state；pool 不可用时挂降级文案。不抛异常。"""
    if not isinstance(state, MutableMapping):
        return False
    inst = state.get("instrument_context")
    sym = (
        symbol
        or (inst.get("symbol") if isinstance(inst, Mapping) else None)
        or state.get("company_of_interest")
        or ""
    )
    td = trade_date or state.get("trade_date") or ""
    try:
        state[PRICE_REF_PROMPT_STATE_KEY] = build_price_ref_prompt_block(
            source, sym, td
        )
    except Exception:
        state[PRICE_REF_PROMPT_STATE_KEY] = {
            "zh": f"【可引用价位表】\n{_NO_TABLE_ZH}",
            "en": f"[Reference price table]\n{_NO_TABLE_EN}",
        }
    return True


def price_ref_prompt_suffix(state: Optional[Mapping[str, Any]],
                            config: Optional[Mapping[str, Any]] = None) -> str:
    """返回追加到系统提示末尾的价位表提示块（含前导空行）；无块返回 ''。"""
    if not isinstance(state, Mapping):
        return ""
    block = state.get(PRICE_REF_PROMPT_STATE_KEY)
    if not isinstance(block, Mapping):
        return ""
    from tradingagents.prompts.catalog import _resolve_language

    lang = _resolve_language(config)
    text = block.get(lang) or block.get("zh") or ""
    if not isinstance(text, str) or not text.strip():
        return ""
    return "\n\n" + text
