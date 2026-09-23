"""DAV-1225 what-if 分层实现（W1–W4）。

本文件是 trunk ``price_ref_registry.build_price_ref_registry`` 与
``price_basis_gate.evaluate_price_basis_gate`` 的**显式代码补丁副本**——
逐行复制 trunk 逻辑，所有改动用 ``[W1]``/``[W2]``/``[W3]``/``[W4]`` 注释标出，
可逐段对照审查「每层只做了声明的事」。不改 trunk、零 LLM、零外部请求。

分层定义（D-034）：
- W0 = trunk 原样（run_all 直接调 trunk 函数，不经过本文件）。
- W1 = 消除已确认 non-price/foreign/typed-disclosure 伪命中 + 修 conversion
  语义（「折合/折算」本身不再构成复权转换，需 复权/qfq/因子 语义同现）。
- W2 = W1 + derived_estimate 语义角色：derived 可参与推理，但不是坐标——
  不算 concrete basis、不触发 decision-driving 问责、也不能充当 executable。
- W3 = W2 + shared executable parser：gate 的价位词并入 registry 抽取，
  可执行价位先登记再问责；同时修复列表序号/百分比/股数伪价位。
- W4 = W3 + strict source-backed pool→registry bridge：仅当 context 指名
  frozen 字段（指标名/日期+OHLC/涨跌停）且值匹配时才桥接为 vendor_qfq；
  纯同值只记 coincidence，不桥接。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from tradingagents.dataflows.providers.cn_akshare_provider import (
    PRICE_BASIS_PIT_RAW,
    PRICE_BASIS_RAW,
    PRICE_BASIS_UNSPECIFIED,
    PRICE_BASIS_VENDOR_QFQ)
from tradingagents.agents.utils.price_ref_registry import (
    COORDINATE_KEYWORDS,
    DERIVED_KEYWORDS,
    DISCLOSURE_TYPE_BASIS,
    MODEL_REPORT_FIELDS,
    REPORT_FIELDS,
    TECHNICAL_REPORT_FIELDS,
    TYPED_DISCLOSURE_KEYWORDS,
    _BARE_YUAN_PATTERN,
    _DATE_PATTERN,
    _FACTOR_PATTERN,
    _PER_SHARE_PATTERN,
    _PRICE_KEYWORD_PATTERN,
    _SENTENCE_SPLIT_PATTERN,
    _declared_basis,
    _has_coordinate_keyword,
    _has_derived_keyword,
    _norm_date,
    _split_sentences,
)
from tradingagents.agents.utils.price_basis_gate import (
    DECISION_REPORT_FIELDS,
    NON_COMPARABLE_MARKERS,
    _LEVEL_PATTERN,
    _has_non_comparable_label,
    _is_decision_driving,
    _is_real_conversion,
    _VALUE_MATCH_TOLERANCE,
)

from .pool import SnapshotPool, named_field_hits
from . import classify as C

PRICE_BASIS_DERIVED_ESTIMATE = "derived_estimate"  # [W2] 新语义角色（非 basis 坐标）

# [W1] 复权转换要求：句内必须出现复权语义（前复权/不复权/qfq/因子），
# 「折合/折算」单出现只是算术动词，不构成转换。
_REAL_CONVERSION_CTX = re.compile(r"复权|qfq|前复权|不复权|因子", re.I)
_CONVERSION_VERBS = re.compile(r"换算|折算|折合|转换")

# [W3] gate 价位词并入 registry 抽取（shared executable parser）：
# 把 _LEVEL_PATTERN 的锚词加入价格关键词，使可执行价位先登记 ref 再问责。
_EXECUTABLE_ANCHOR_WORDS = (
    "目标价", "目标位", "第一目标", "第二目标", "下行目标", "上行目标",
    "止盈", "止损", "入场", "进场", "买入价", "卖出价", "建仓价",
    "开仓价", "出场价", "加仓价", "减仓价",
)
_LEVEL_PATTERN_W3 = re.compile(
    r"(?:目标价|目标位|第一目标|第二目标|下行目标|上行目标|止盈位?|止损位?|"
    r"入场价?|进场价?|买入价|卖出价|建仓价|开仓价|出场价|加仓价|减仓价|"
    r"入场区间|进场区间|建仓区间|加仓区间|减仓区间|买入区间|卖出区间)"
    r"[^0-9]{0,12}?(\d+(?:\.\d+)?)"
)


# ---------------------------------------------------------------------------
# [W1] 抽取级伪命中过滤（non-price / foreign），与 classify.py 同一套规则
# ---------------------------------------------------------------------------

def _is_false_mention(sentence: str, start: int, end: int, value: float) -> Optional[str]:
    """返回伪命中子类名；不是伪命中返回 None。"""
    flag = C._token_tail_flags(sentence, start, end)
    if flag:
        return flag
    if C._JSON_BLOB.search(sentence) and "元" not in sentence:
        return "json_or_prob_token"
    if C._FOREIGN_CTX.search(sentence):
        return "foreign_or_commodity"
    return None


def _extract_price_values_w1(sentence: str) -> List[Tuple[float, int]]:
    """trunk _extract_price_values 的 W1 版：命中后按 token 语境过滤伪命中。"""
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
        if _is_false_mention(sentence, nstart, nend, value):  # [W1]
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


def _extract_price_values_w3(sentence: str) -> List[Tuple[float, int]]:
    """W3 版：W1 过滤 + gate 价位词并入抽取（shared executable parser）。"""
    found = _extract_price_values_w1(sentence)
    seen = [(p, p) for _v, p in found]
    for m in _LEVEL_PATTERN_W3.finditer(sentence):
        try:
            value = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        nstart, nend = m.start(1), m.end(1)
        if _is_false_mention(sentence, nstart, nend, value):  # [W3] 同套伪命中过滤
            continue
        if _is_false_level(sentence, nstart, nend, value):    # [W3] 列表序号等
            continue
        if any(abs(v - value) <= _VALUE_MATCH_TOLERANCE for v, _p in found):
            continue
        found.append((value, nstart))
    found.sort(key=lambda item: item[1])
    return found


# ---------------------------------------------------------------------------
# [W1] typed disclosure 复核驱动的类型判定
# ---------------------------------------------------------------------------

def _detect_disclosure_type_w1(sentence: str) -> Optional[str]:
    """trunk 最长匹配后，再过 classify_typed_disclosure 的 false-context 规则；
    verdict=false → 返回 None（不是披露）；true/ambiguous → 保留原标签。"""
    best: Optional[Tuple[int, str]] = None
    for kw, dtype in TYPED_DISCLOSURE_KEYWORDS.items():
        if kw in sentence:
            if best is None or len(kw) > best[0]:
                best = (len(kw), dtype)
    if best is None:
        return None
    verdict = C.classify_typed_disclosure({"context": sentence, "disclosure_type": best[1]})
    if verdict["verdict"] == "false":
        return None
    return best[1]


# ---------------------------------------------------------------------------
# [W3] gate 价位伪命中修复
# ---------------------------------------------------------------------------

def _is_false_level(text: str, nstart: int, nend: int, value: float) -> bool:
    """可执行价位命中的伪命中：列表序号「N. 」、百分比、股数/金额、日期年份。"""
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


def _extract_executable_levels_w3(text: str) -> List[Tuple[float, int, int]]:
    """W3 版价位抽取：返回 (value, num_start, num_end) 并剔除伪命中。"""
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
# build_price_ref_registry —— trunk 逐行副本 + [W1]/[W2]/[W4] 补丁
# ---------------------------------------------------------------------------

def build_price_ref_registry_w(
    reports: Mapping[str, Any],
    *,
    cutoff: Optional[str] = None,
    layer: int = 1,
    pool: Optional[SnapshotPool] = None,
) -> Dict[str, Any]:
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
            if layer >= 3:                                   # [W3] shared executable parser
                mentions = _extract_price_values_w3(sentence)
            elif layer >= 1:                                 # [W1] 伪命中过滤
                mentions = _extract_price_values_w1(sentence)
            else:                                            # W0 路径不经过本函数
                mentions = []
            if not mentions:
                continue
            if layer >= 1:                                   # [W1] typed-disclosure 复核
                disclosure_type = _detect_disclosure_type_w1(sentence)
            else:
                disclosure_type = None
            derived = _has_derived_keyword(sentence)
            sentence_dates = []
            for m in _DATE_PATTERN.finditer(sentence):
                y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
                if 1 <= mo <= 12 and 1 <= d <= 31:
                    sentence_dates.append(f"{y}-{mo:02d}-{d:02d}")
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
                    # [W1] conversion 语义修正：仅当句内同时有复权语义才登记
                    # conversion；「折合/折算」单独出现只是估值/量幅算术。
                    if layer >= 1:
                        is_real_conv = bool(
                            _REAL_CONVERSION_CTX.search(sentence)
                            and _CONVERSION_VERBS.search(sentence)
                        )
                    else:
                        is_real_conv = True
                    if is_real_conv:
                        factor_match = _FACTOR_PATTERN.search(sentence)
                        ref["conversion"] = {
                            "factor": float(factor_match.group(1)) if factor_match else None,
                            "factor_as_of": sentence_as_of,
                        }
                        target = _declared_basis(sentence)
                        if target is not None and disclosure_type is None:
                            ref["basis"] = target

                refs.append(ref)
                sentence_ref_ids.append((ref["ref_id"], value, ref["basis"]))

            if derived and len(sentence_ref_ids) > 1:
                ids = [rid for rid, _v, _b in sentence_ref_ids]
                for rid in ids:
                    ref = next(r for r in refs if r["ref_id"] == rid)
                    ref["derived_from"] = [other for other in ids if other != rid]

    # [W2] derived_estimate 语义角色：derived 估值锚不再是坐标 basis，
    # 可参与推理但不冒充 vendor_qfq coordinate/executable。
    if layer >= 2:
        for ref in refs:
            prov = ref.get("provenance") or ""
            # [R3] derived 判定绑定到数字本身：同一子句、数字前 25 字内出现
            # 估值算术词才算；「假设/情景/悲观/乐观/防御/底线」弱词不单独触发。
            # 真报价保护：报价语义词 + pool 具名字段同值 → 真坐标，不降格。
            ctx_v = ref.get("context") or ""
            is_derived = (
                C.is_derived_value(ctx_v, ref.get("value"))
                and not (
                    pool is not None
                    and C.looks_like_actual_quote(ctx_v, ref.get("value"), pool)
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

    # [W4] strict source-backed pool→registry bridge：仅字段级 provenance。
    bridge_hits: List[Dict[str, Any]] = []
    if layer >= 4 and pool is not None:
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
        # [W2] derived_estimate 不是市场报价，不要求 as_of。
        if ref["as_of"] is None and not (
            layer >= 2 and ref["basis"] == PRICE_BASIS_DERIVED_ESTIMATE
        ):
            _add_gap(
                "missing_as_of",
                ref["ref_id"],
                ref["source"],
                f"价格 {ref['value']} 缺少 as_of",
            )

    # Pass 4 — basis mismatch detection.
    concrete_bases = {PRICE_BASIS_VENDOR_QFQ, PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW}
    # [W2] derived_estimate 不属 concrete basis，天然不参与 R1/R2 混用判定。

    by_report: Dict[str, List[Dict[str, Any]]] = {}
    for ref in refs:
        by_report.setdefault(ref["source"], []).append(ref)

    for report_name, report_refs in by_report.items():
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
                _add_gap("basis_mismatch", ids[0], report_name, finding["detail"])

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
    out = {
        "price_refs": refs,
        "price_basis_gaps": gaps,
        "validation": validation,
    }
    if layer >= 4:
        out["pool_bridge"] = bridge_hits
    return out


# ---------------------------------------------------------------------------
# evaluate_price_basis_gate —— trunk 逐行副本 + [W2]/[W3] 补丁
# ---------------------------------------------------------------------------

def _legal_execution_ref_w(ref: Mapping[str, Any], invalid_ref_ids: Set[str]) -> bool:
    if ref.get("basis") != PRICE_BASIS_VENDOR_QFQ:
        return False
    if ref.get("ref_id") in invalid_ref_ids:
        return False
    return True


def _is_decision_driving_w(ref: Mapping[str, Any], layer: int) -> bool:
    # [W2] derived_estimate 不是坐标价格，不作 decision-driving 问责。
    if layer >= 2 and ref.get("basis") == PRICE_BASIS_DERIVED_ESTIMATE:
        return False
    return _is_decision_driving(ref)


def evaluate_price_basis_gate_w(state: Mapping[str, Any], *, layer: int = 1) -> Dict[str, Any]:
    refs = list(state.get("price_refs") or [])
    validation = state.get("price_basis_validation") or {}
    findings = list(validation.get("findings") or []) if isinstance(validation, Mapping) else []

    ref_by_id = {r.get("ref_id"): r for r in refs if isinstance(r, Mapping)}
    invalid_ref_ids: Set[str] = set()
    for f in findings:
        if f.get("kind") == "invalid_conversion":
            invalid_ref_ids.update(f.get("ref_ids") or [])
    invalid_ref_ids = {
        rid
        for rid in invalid_ref_ids
        if rid in ref_by_id and _is_real_conversion(ref_by_id[rid])
    }

    violations: List[Dict[str, Any]] = []
    allowed_dual_display: List[Dict[str, Any]] = []
    seen_violation_keys: Set[tuple] = set()

    def _violate(kind: str, detail: str, ref_ids: Optional[List[str]] = None,
                 source: Optional[str] = None) -> None:
        key = (kind, tuple(ref_ids or []), detail)
        if key in seen_violation_keys:
            return
        seen_violation_keys.add(key)
        violations.append(
            {"kind": kind, "ref_ids": ref_ids or [], "source": source, "detail": detail}
        )

    if isinstance(validation, Mapping) and validation.get("audit_error"):
        _violate("audit_unavailable",
                 "price_ref 审计未能完成，无 basis 证据，fail-close", [], None)

    cutoff = state.get("trade_date") if isinstance(state.get("trade_date"), str) else None
    decision_driving = [
        r for r in refs
        if isinstance(r, Mapping) and _is_decision_driving_w(r, layer)   # [W2]
    ]
    for ref in decision_driving:
        if ref.get("basis") == PRICE_BASIS_UNSPECIFIED:
            _violate(
                "decision_driving_unspecified_basis",
                f"决策驱动价格 {ref.get('value')}({ref.get('ref_id')}) basis 无法归因，禁止消费",
                [ref.get("ref_id")],
                ref.get("source"),
            )
        inherits_cutoff = ref.get("basis") == PRICE_BASIS_VENDOR_QFQ and cutoff
        if ref.get("as_of") is None and not inherits_cutoff:
            _violate(
                "decision_driving_missing_as_of",
                f"决策驱动价格 {ref.get('value')}({ref.get('ref_id')}) 缺少 as_of",
                [ref.get("ref_id")],
                ref.get("source"),
            )

    for f in findings:
        kind = f.get("kind")
        ref_ids = list(f.get("ref_ids") or [])
        if kind == "invalid_conversion":
            involved = [
                ref_by_id[rid]
                for rid in ref_ids
                if rid in ref_by_id and _is_real_conversion(ref_by_id[rid])
            ]
            if any(_is_decision_driving_w(r, layer) for r in involved):   # [W2]
                _violate(
                    "invalid_conversion",
                    f.get("detail") or "换算缺少 PIT-safe provenance",
                    ref_ids,
                    f.get("source"),
                )
        elif kind == "basis_mismatch":
            contexts = [
                str(ref_by_id[rid].get("context") or "")
                for rid in ref_ids
                if rid in ref_by_id
            ]
            if contexts and all(_has_non_comparable_label(c) for c in contexts):
                allowed_dual_display.append(
                    {
                        "kind": "labeled_dual_display",
                        "ref_ids": ref_ids,
                        "source": f.get("source"),
                        "detail": f.get("detail"),
                    }
                )
            else:
                _violate(
                    "cross_basis_coordinate_mix",
                    f.get("detail") or "跨 basis 价格混入同一坐标语境",
                    ref_ids,
                    f.get("source"),
                )

    # Rule 2b
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        if ref.get("basis") not in (PRICE_BASIS_RAW, PRICE_BASIS_PIT_RAW):
            continue
        context = ref.get("context") or ""
        if not any(kw in context for kw in COORDINATE_KEYWORDS):
            continue
        if _has_non_comparable_label(context):
            allowed_dual_display.append(
                {
                    "kind": "labeled_dual_display",
                    "ref_ids": [ref.get("ref_id")],
                    "source": ref.get("source"),
                    "detail": f"{ref.get('basis')} 价格 {ref.get('value')} 双列展示（已标不可直接比较）",
                }
            )
            continue
        if _is_real_conversion(ref) and ref.get("ref_id") not in invalid_ref_ids:
            continue
        _violate(
            "cross_basis_coordinate_mix",
            f"{ref.get('basis')} 价格 {ref.get('value')}({ref.get('ref_id')}) 直接进入坐标语境，禁止跨坐标混用",
            [ref.get("ref_id")],
            ref.get("source"),
        )

    # Rule 3 — executable levels
    for field in DECISION_REPORT_FIELDS:
        text = state.get(field)
        if not isinstance(text, str):
            continue
        if layer >= 3:                                     # [W3] 伪价位过滤
            level_hits = _extract_executable_levels_w3(text)
            level_values = [v for v, _s, _e in level_hits]
        else:
            level_values = []
            for m in _LEVEL_PATTERN.finditer(text):
                try:
                    level_values.append(float(m.group(1)))
                except (TypeError, ValueError):
                    continue
        for value in level_values:
            candidates = [
                r
                for r in refs
                if isinstance(r, Mapping)
                and r.get("source") == field
                and isinstance(r.get("value"), (int, float))
                and abs(r["value"] - value) <= _VALUE_MATCH_TOLERANCE
            ]
            if not candidates:
                _violate(
                    "unbacked_executable_level",
                    f"可执行价位 {value}（{field}）未登记任何 price_ref，不得生成可执行数值",
                    [],
                    field,
                )
                continue
            if not any(_legal_execution_ref_w(r, invalid_ref_ids) for r in candidates):
                ids = [r.get("ref_id") for r in candidates]
                bases = sorted({r.get("basis") for r in candidates})
                _violate(
                    "executable_level_wrong_basis",
                    f"可执行价位 {value}（{field}）basis={bases}，非合法 qfq/converted 坐标",
                    ids,
                    field,
                )

    status = "blocked" if violations else "pass"
    return {
        "contract_version": "price_ref.v1",
        "status": status,
        "violations": violations,
        "allowed_dual_display": allowed_dual_display,
        "decision_driving_ref_count": len(decision_driving),
        "price_basis_version": (
            "price_basis.vendor_qfq" if status == "pass" else "price_basis.unspecified"
        ),
    }


def run_layer(state: Mapping[str, Any], layer: int,
              pool: Optional[SnapshotPool] = None,
              keep_refs: bool = False) -> Dict[str, Any]:
    """对一份 state（含报告字段+trade_date）跑 W1–W4 某层的 what-if gate。"""
    reports = {name: state.get(name) for name in REPORT_FIELDS}
    out = build_price_ref_registry_w(
        reports, cutoff=state.get("trade_date"), layer=layer, pool=pool
    )
    shadow = dict(state)
    shadow["price_refs"] = out["price_refs"]
    shadow["price_basis_gaps"] = out["price_basis_gaps"]
    shadow["price_basis_validation"] = out["validation"]
    gate = evaluate_price_basis_gate_w(shadow, layer=layer)
    gate["n_refs"] = len(out["price_refs"])
    if "pool_bridge" in out:
        gate["pool_bridge"] = out["pool_bridge"]
    if keep_refs:
        gate["_refs"] = out["price_refs"]
    return gate
