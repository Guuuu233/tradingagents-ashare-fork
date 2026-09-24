"""DAV-1249 — price_ref 逐角色代码校验 + 定向返修一次（D-037 生成链改造）。

角色产出文本后立即用与 finalize 相同的口径检查其中的每个价格：
- 本次运行的 pool（C5 桥接，``state[PRICE_REF_SOURCE_KEY]``）；
- 当时已产出报告中的 vendor_qfq 值（registry back-reference）；
- derived_estimate、影线长度等既有规则。

筛出「决策驱动且 basis 为 unspecified 或缺 as_of」的价格，把清单连同
可引用价位表作为一次追加消息发回同一角色、同一模型，要求改写一次：

  a) 改用表内数值，并写出名称与日期；
  b) 改写成相对于某个表内价格的百分比；
  c) 删除该价格。

保护措施（命中即丢弃返修稿、保留原稿）：
- 返修后 VERDICT / 方向 / 交易动作 / 概率签名与返修前不一致；
- 返修稿比原稿短 30% 以上。

返修每角色只做一次，不循环；下游角色读到的是最终采用的文本。
判定不新建任何规则——全部复用 ``price_ref_registry`` /
``price_basis_gate`` 的现有函数。``result_data.price_ref_revision``
逐角色记录触发、清单与采用结果；``price_ref_revision_version``
只用于追溯。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Tuple

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ,
)
from tradingagents.agents.utils.debate_utils import extract_tagged_json
from tradingagents.agents.utils.price_ref_registry import (
    PRICE_REF_SOURCE_KEY,
    REPORT_FIELDS,
    build_market_data_pool,
    build_price_ref_registry,
    named_field_hits,
    _pool_from_state,
)
from tradingagents.graph.signal_processing import _extract_decision_keyword

logger = logging.getLogger(__name__)

PRICE_REF_REVISION_VERSION = "price_ref_revision.v1"

# state 上承载逐角色返修记录的键：{role_key: record}。
PRICE_REF_REVISION_STATE_KEY = "price_ref_revision"

# 纳入逐角色检查的角色 -> 其产出文本写入的 report 字段。
ROLE_REPORT_FIELDS: Mapping[str, str] = {
    "fundamentals": "fundamentals_report",
    "news": "news_report",
    "macro": "macro_report",
    "smart_money": "smart_money_report",
    "social": "sentiment_report",
    "research_manager": "investment_plan",
    "trader": "trader_investment_plan",
    "risk_manager": "final_trade_decision",
}

# 返修稿长度保护：短于原稿 70% 即丢弃。
_MIN_REVISION_LEN_RATIO = 0.7

# DAV-1249 v2 开关：仅在 harness/受控环境显式置位生效；生产默认值由总控
# 上线前另行裁定（未置位=关闭，保持合入前行为）。
PRICE_REF_REVISION_ENV = "TA_PRICE_REF_REVISION_ENABLED"

# 受控试验返修稿全文落盘目录（仅 harness 置位；生产 result_data 只存签名哈希）。
PRICE_REF_REVISION_DUMP_ENV = "TA_PRICE_REF_REVISION_DUMP_DIR"


def revision_enabled() -> bool:
    return os.environ.get(PRICE_REF_REVISION_ENV, "").strip().lower() in (
        "1", "true", "on", "yes")


# V1 签名白名单：只比对结论字段——方向/动作/赢家/状态/概率。
# 理由文本与机读块内价格字段不参与比对，允许按三选一规则修改。
_CONCLUSION_KEYS = (
    "direction", "trade_action", "winner",
    "analysis_status", "confirmation_state", "probability",
)


def _project_conclusion(block: Any) -> Any:
    """机读块 JSON 只保留结论字段；非 Mapping（未解析/字符串）原样保留。"""
    if not isinstance(block, Mapping):
        return block
    return {k: block.get(k) for k in _CONCLUSION_KEYS if k in block}


# ---------------------------------------------------------------------------
# R1 — 逐角色检查（与 finalize/gate 同一口径）
# ---------------------------------------------------------------------------


def check_role_price_refs(
    state: Mapping[str, Any],
    report_field: str,
    text: str,
) -> List[Dict[str, Any]]:
    """检查 ``text``（``report_field`` 角色的产出）中的每个价格。

    口径与 finalize 完全一致：registry 覆盖「当前 state 中已产出的全部
    报告 + 本角色新文本」，pool 取 ``state[PRICE_REF_SOURCE_KEY]``（C5
    桥接），back-reference 只能指向当时已产出报告中的 vendor_qfq 值。

    返回问题价格清单；每个元素 ``{"value", "kind", "sentence", "ref_id",
    "basis", "as_of"}``。``kind`` ∈ ``unspecified_basis`` /
    ``missing_as_of``——即 gate Rule 1 的两条违规。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    reports = {name: state.get(name) for name in REPORT_FIELDS}
    reports[report_field] = text
    pool = _pool_from_state(state)
    cutoff = state.get("trade_date") if isinstance(state.get("trade_date"), str) else None
    try:
        result = build_price_ref_registry(reports, cutoff=cutoff, pool=pool)
    except Exception:
        # fail-open for the revision path: 检查失败不返修，最终 gate 仍兜底。
        logger.exception("[price_ref_revision] registry build failed for %s", report_field)
        return []

    # 延迟 import 避免环：gate 依赖 registry，本模块复用 gate 的判定函数。
    from tradingagents.agents.utils.price_basis_gate import _is_decision_driving

    problems: List[Dict[str, Any]] = []
    for ref in result.get("price_refs") or []:
        if not isinstance(ref, Mapping) or ref.get("source") != report_field:
            continue
        if not _is_decision_driving(ref):
            continue
        basis = ref.get("basis")
        if basis == PRICE_BASIS_UNSPECIFIED:
            kind = "unspecified_basis"
        elif ref.get("as_of") is None and not (basis == PRICE_BASIS_VENDOR_QFQ and cutoff):
            kind = "missing_as_of"
        else:
            continue
        problems.append(
            {
                "ref_id": ref.get("ref_id"),
                "value": ref.get("value"),
                "basis": basis,
                "as_of": ref.get("as_of"),
                "kind": kind,
                "sentence": ref.get("sentence") or ref.get("context") or "",
            }
        )
    return problems


# ---------------------------------------------------------------------------
# 可引用价位表（移植自参考分支 agent/2/33c391069d44 的 build_price_ref_table；
# 逐行经 named_field_hits 桥接验证后才入表——只进返修消息，不进系统提示词）
# ---------------------------------------------------------------------------


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


def _render_price_ref_table(source: Optional[Mapping[str, Any]],
                            state: Mapping[str, Any]) -> str:
    """渲染返修消息中的价位表；pool 不可用返回降级文案。"""
    rows: Optional[List[Dict[str, Any]]] = None
    if isinstance(source, Mapping):
        inst = state.get("instrument_context")
        symbol = (
            source.get("symbol")
            or (inst.get("symbol") if isinstance(inst, Mapping) else None)
            or state.get("company_of_interest")
            or ""
        )
        trade_date = source.get("trade_date") or state.get("trade_date") or ""
        try:
            rows = build_price_ref_table(source, symbol, trade_date)
        except Exception:
            rows = None
    if not rows:
        return "本次无可引用价位表。"
    return "\n".join(f"- {r['context']}" for r in rows)


# ---------------------------------------------------------------------------
# R2 — 定向返修一次
# ---------------------------------------------------------------------------


def build_revision_message(problems: List[Dict[str, Any]],
                           table_text: str) -> str:
    """构造发回同一角色的一次性追加消息。"""
    lines = ["你刚才的报告中，以下价格无法归因到本次运行的行情来源"
             "（basis 无法归因或缺少日期）：", ""]
    for i, p in enumerate(problems, 1):
        sentence = (p.get("sentence") or "").strip()
        lines.append(f"{i}. 价格 {p.get('value')} —— 原句：「{sentence}」")
    lines += [
        "",
        "【本次可引用价位表】（来自本次运行行情数据，引用时原样抄写数值并写出名称与日期）：",
        table_text,
        "",
        "请对清单中的每个价格三选一处理：",
        "a) 改用表内数值，并写出该价位的名称与日期；",
        "b) 改写成相对于某个表内价格的百分比；",
        "c) 删除该价格。",
        "",
        "严格要求：结论、方向、交易动作、概率均不得改变；除上述价格外其余"
        "内容不得改动。请输出修改后的报告全文。",
    ]
    return "\n".join(lines)


def conclusion_signature(text: str) -> Dict[str, Any]:
    """返修保护签名（v2）：VERDICT / MANAGER_VERDICT / RISK_JUDGE 机读块
    投影到结论白名单字段 + 规则抽取的交易动作。理由文本与价格字段
    不参与比对——机读块内价格允许按三选一规则修改。"""
    text = text or ""
    return {
        "verdict": _project_conclusion(extract_tagged_json(text, "VERDICT")),
        "manager_verdict": _project_conclusion(
            extract_tagged_json(text, "MANAGER_VERDICT")),
        "risk_judge": _project_conclusion(extract_tagged_json(text, "RISK_JUDGE")),
        "decision": _extract_decision_keyword(text),
    }


def _normalize_messages(orig: Any) -> List[Any]:
    """把角色原始调用上下文归一化为消息序列：
    - str（裸 prompt 调用，如 research_manager/risk_manager）→ [HumanMessage]
    - dict（{"role":..,"content":..}，如 trader）→ 对应 Message 类型
    - BaseMessage → 原样
    """
    if orig is None:
        return []
    if isinstance(orig, str):
        return [HumanMessage(content=orig)] if orig.strip() else []
    if isinstance(orig, BaseMessage):
        return [orig]
    out: List[Any] = []
    for m in orig if isinstance(orig, (list, tuple)) else [orig]:
        if isinstance(m, BaseMessage):
            out.append(m)
        elif isinstance(m, Mapping):
            role = str(m.get("role") or "user").lower()
            content = m.get("content") or ""
            if role == "system":
                out.append(SystemMessage(content=content))
            elif role in ("assistant", "ai"):
                out.append(AIMessage(content=content))
            else:
                out.append(HumanMessage(content=content))
        elif isinstance(m, str):
            out.append(HumanMessage(content=m))
    return out


async def _invoke_revision_llm(llm: Any, original_text: str,
                               message: str,
                               orig_messages: Any = None) -> Optional[str]:
    """返修请求追加在该角色原始调用的完整消息序列之后（V2）：
    原系统提示与输入上下文原样保留（含用户自定义提示词注入），
    末尾追加 AIMessage(原稿) + HumanMessage(返修要求)。"""
    messages = _normalize_messages(orig_messages) + [
        AIMessage(content=original_text),
        HumanMessage(content=message),
    ]
    try:
        if hasattr(llm, "ainvoke"):
            res = await llm.ainvoke(messages)
        else:
            res = await asyncio.to_thread(llm.invoke, messages)
        content = getattr(res, "content", res)
        return content if isinstance(content, str) and content.strip() else None
    except Exception as exc:
        logger.warning("[price_ref_revision] revision call failed: %r", exc)
        return None


def _base_record(role_key: str, report_field: str,
                 problems: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "version": PRICE_REF_REVISION_VERSION,
        "role": role_key,
        "report_field": report_field,
        "triggered": bool(problems),
        "revision_attempted": False,
        "problems": problems,
        "problem_count": len(problems),
        "adopted": None,
        "discard_reason": None,
        "original_len": None,
        "revised_len": None,
        "post_revision_problem_count": None,
        "new_problem_values": [],
    }


def _safe_check(check: Callable[[str], bool], text: str) -> bool:
    """确定性检查执行器：返回 True=通过；检查自身异常按不通过处理
    （宁可丢弃返修稿，不让未过检查的文字下行）。"""
    try:
        return bool(check(text))
    except Exception:
        logger.exception("[price_ref_revision] deterministic check raised")
        return False


def _dump_revised_text(role_key: str, sha: str, text: str) -> None:
    """受控试验专用：把返修稿全文写到隔离目录（环境变量置位才生效）。"""
    dump_dir = os.environ.get(PRICE_REF_REVISION_DUMP_ENV)
    if not dump_dir:
        return
    try:
        d = Path(dump_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{role_key}_{sha[:16]}.txt").write_text(text, encoding="utf-8")
    except Exception:
        logger.exception("[price_ref_revision] dump revised text failed")


async def maybe_revise_role_report(
    state: MutableMapping[str, Any],
    *,
    role_key: str,
    report_field: str,
    text: str,
    llm: Any,
    orig_messages: Any = None,
    deterministic_check: Optional[Callable[[str], bool]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """R1+R2+R3：检查 -> （有问题时）返修一次 -> 保护 -> 记录。

    ``orig_messages``：该角色原始调用的完整消息序列/裸 prompt（V2），
    返修请求追加在其后；缺省时只发 [原稿, 返修要求] 的降级序列。
    ``deterministic_check``：该角色的确定性检查（V3），签名
    ``(text) -> bool``，返修稿必须通过才被采用。

    返回 ``(最终采用文本, 记录)``。记录由调用方并入
    ``state[PRICE_REF_REVISION_STATE_KEY][role_key]``。
    每角色最多一次：state 中已有该角色的 attempted 记录时直接跳过返修
    （仍返回记录占位为 None，调用方不得覆盖既有记录）。
    """
    if not revision_enabled():
        return text, {}
    existing = state.get(PRICE_REF_REVISION_STATE_KEY)
    if isinstance(existing, Mapping):
        prior = existing.get(role_key)
        if isinstance(prior, Mapping) and prior.get("revision_attempted"):
            return text, {}

    problems = check_role_price_refs(state, report_field, text)
    rec = _base_record(role_key, report_field, problems)
    rec["original_len"] = len(text or "")
    if not problems:
        return text, rec

    rec["revision_attempted"] = True
    table_text = _render_price_ref_table(state.get(PRICE_REF_SOURCE_KEY), state)
    message = build_revision_message(problems, table_text)
    revised = await _invoke_revision_llm(llm, text, message,
                                         orig_messages=orig_messages)

    if revised is None:
        rec["adopted"] = "original"
        rec["discard_reason"] = "revision_call_failed"
        return text, rec

    rec["revised_len"] = len(revised)
    # V4：记录双侧签名与返修稿哈希；受控试验下全文另存隔离目录。
    orig_sig = conclusion_signature(text)
    rev_sig = conclusion_signature(revised)
    rec["orig_signature"] = orig_sig
    rec["revised_signature"] = rev_sig
    rec["revised_sha256"] = hashlib.sha256(revised.encode("utf-8")).hexdigest()
    _dump_revised_text(role_key, rec["revised_sha256"], revised)

    if orig_sig != rev_sig:
        rec["adopted"] = "original"
        rec["discard_reason"] = "conclusion_changed"
        return text, rec

    if len(revised) < _MIN_REVISION_LEN_RATIO * len(text or ""):
        rec["adopted"] = "original"
        rec["discard_reason"] = "excessive_truncation"
        return text, rec

    # V3 第三道保护：角色级确定性检查（如研究经理一致性硬门、输出退化
    # 检查）。返修稿必须通过同一检查才被采用；原稿通过而返修稿不通过
    # → consistency_regression；原稿也未过 → revised_check_failed。
    if deterministic_check is not None:
        orig_ok = _safe_check(deterministic_check, text)
        rev_ok = _safe_check(deterministic_check, revised)
        rec["orig_check_passed"] = orig_ok
        rec["revised_check_passed"] = rev_ok
        if not rev_ok:
            rec["adopted"] = "original"
            rec["discard_reason"] = (
                "consistency_regression" if orig_ok else "revised_check_failed"
            )
            return text, rec

    rec["adopted"] = "revised"
    # 返修后复检：记录残余问题与返修引入的新问题价格（供返修漏斗统计）。
    post = check_role_price_refs(state, report_field, revised)
    rec["post_revision_problem_count"] = len(post)
    orig_values = {p.get("value") for p in problems}
    rec["new_problem_values"] = sorted(
        {p.get("value") for p in post if p.get("value") not in orig_values},
        key=lambda v: (v is None, v),
    )
    return revised, rec
