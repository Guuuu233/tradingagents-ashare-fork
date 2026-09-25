"""DAV-1267 — 研究经理 E-04 违规定向返修一次（D-037 生成链改造扩展）。

把 DAV-1249「代码当场检查 + 退回同一角色改写一次」机制扩展到研究经理的
E-04 违规上。守卫与语义判定一律不放宽——本模块只做三件事：

1. 触发判定：``failed_checks`` 非空且**全部**以「E-04 守卫拦截」开头时，
   E-04 返修才允许触发（``e04_only_failures``）。
2. 返修消息：对每条 E-04 命中句列出三选一（删除 / 改写为明确不确定表述 /
   补本次运行已 verified 的 claim id），与 price_ref 清单合并进同一条
   追加消息，仍只调用一次模型。
3. 同义词逃逸检查：返修稿在原命中句附近（diff 变更区内、命中区间 ±窗口）
   新出现「已被消化 / 已反映 / 已计入 / 已兑现 / 市场已知 / 充分反映」等
   等价断言时判改写失败——零 LLM，diff 基于 ``difflib``。

开关 ``TA_E04_REVISION_ENABLED`` 未置位=关闭；关闭时调用方不产生任何
额外一致性重算与模型调用。
"""

from __future__ import annotations

import difflib
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

E04_REVISION_VERSION = "e04_revision.v1"

# 与守卫违规文案前缀一致（validate_manager_expectation_revision_consumption
# 每条 violations 均以此前缀开头）。
E04_VIOLATION_PREFIX = "E-04 守卫拦截"

# 受控开关：未设置=关闭。
E04_REVISION_ENV = "TA_E04_REVISION_ENABLED"

# R4 同义词断言词表：禁止用同义改写保留原断言。
# 「已反映/已计入」等词本身可合法出现（引用、否定、不确定表述），因此判定
# 只在 diff 变更区 + 原命中句位置窗口内进行，不做全文禁用。
E04_SYNONYM_TERMS: Tuple[str, ...] = (
    "已被消化",
    "已反映",
    "已计入",
    "已兑现",
    "市场已知",
    "充分反映",
)

# 命中句位置窗口：diff 变更块的原文区间与命中区间距离在该范围内即判逃逸。
_SYNONYM_WINDOW = 160


def e04_revision_enabled() -> bool:
    return os.environ.get(E04_REVISION_ENV, "").strip().lower() in (
        "1", "true", "on", "yes")


def e04_only_failures(failed_checks: Sequence[Any]) -> bool:
    """R1：失败项非空且全部以 E-04 前缀开头才允许触发返修。

    混入任何非 E-04 失败项（漏裁、rejected_adopt、覆盖率不足、胜方矛盾
    等）即返回 False——返修救不回，不触发。
    """
    items = [s for s in (failed_checks or []) if isinstance(s, str)]
    if not items or len(items) != len(failed_checks or []):
        return False
    return all(s.startswith(E04_VIOLATION_PREFIX) for s in items)


def locate_hit_spans(text: str, hits: Sequence[Mapping[str, Any]]
                     ) -> List[Optional[Tuple[int, int]]]:
    """把守卫记录的命中句在 ``text``（经理原稿）中定位。

    命中句取自守卫 full_text（raw + reason + plan 拼接、已剥系统文案），
    一般能在原稿中原样找到；找不到返回 None（同义词检查退化为
    「任一未定位命中存在时，变更区出现词表即逃逸」的保守口径）。
    """
    spans: List[Optional[Tuple[int, int]]] = []
    for h in hits:
        sent = str((h or {}).get("sentence") or "").strip()
        idx = text.find(sent) if sent else -1
        spans.append((idx, idx + len(sent)) if idx >= 0 else None)
    return spans


def find_synonym_escapes(
    original: str,
    revised: str,
    hit_spans: Sequence[Optional[Tuple[int, int]]],
    terms: Sequence[str] = E04_SYNONYM_TERMS,
) -> List[Dict[str, Any]]:
    """R4 新增同义词断言检查：返修稿在原命中句位置附近出现词表表述
    → 改写失败。

    判定口径（全部确定性，零 LLM）：
    - ``difflib.SequenceMatcher`` 求原稿→返修稿变更区（replace/insert）；
    - 变更区新文本命中词表，且其**原文区间**与任一已定位命中句的
      ±``_SYNONYM_WINDOW`` 窗口相交 → ``near_hit`` 逃逸；
    - 任一命中句未能在原稿定位时，变更区命中词表一律按逃逸处理
      （``unresolved_hit``，保守方向：宁可丢稿不留断言）；
    - 变更区出现原稿全文从未出现过的词表项，同样视为逃逸
      （``novel_term``，覆盖把断言搬离原位的改写）。

    返回逃逸清单（空=通过），每项 ``{"term","rule","revised_excerpt"}``。
    """
    original = original or ""
    revised = revised or ""
    if original == revised or not terms:
        return []

    resolved = [s for s in hit_spans if s]
    has_unresolved = len(resolved) < len(list(hit_spans))

    sm = difflib.SequenceMatcher(a=original, b=revised, autojunk=False)
    changes = [(a0, a1, b0, b1) for tag, a0, a1, b0, b1 in sm.get_opcodes()
               if tag in ("replace", "insert")]
    if not changes:
        return []

    # 词表项可能横跨「保留段 + 变更块」边界（如「市场」保留 +「已知」新插），
    # 因此按返修稿中的命中出现是否触及变更块判定，而不是只看变更块内部。
    escapes: List[Dict[str, Any]] = []
    seen: set = set()
    for term in terms:
        idx = revised.find(term)
        while idx >= 0:
            t0, t1 = idx, idx + len(term)
            idx = revised.find(term, idx + 1)
            for a0, a1, b0, b1 in changes:
                if not (t0 < b1 and b0 < t1):
                    continue  # 该出现未触及变更块 → 原有表述，不算
                near = any(
                    a0 <= he + _SYNONYM_WINDOW and hs - _SYNONYM_WINDOW <= a1
                    for hs, he in resolved)
                if near:
                    rule = "near_hit"
                elif has_unresolved:
                    rule = "unresolved_hit"
                elif term not in original:
                    rule = "novel_term"
                else:
                    break  # 原有词表项的远处重排，不判逃逸
                key = (term, rule, t0)
                if key not in seen:
                    seen.add(key)
                    ctx = revised[max(0, t0 - 30):t1 + 30].replace("\n", " ")
                    escapes.append({"term": term, "rule": rule,
                                    "revised_excerpt": ctx[:120]})
                break
    return escapes


def build_e04_revision_message(hits: Sequence[Mapping[str, Any]],
                               verified_claim_ids: Sequence[str],
                               price_section: str = "") -> str:
    """构造 E-04 返修要求文本；``price_section`` 非空时拼在其后，合成
    同一条追加消息（R2：价格问题与 E-04 违规合并为一次调用）。

    R3：对每条命中句三选一——删除 / 改写为明确不确定表述 / 补本次运行
    已 verified 的 claim id。禁止同义词替换保留原断言；结论、方向、
    动作、概率不得改变。
    """
    lines: List[str] = []
    if hits:
        lines += [
            "你刚才的报告中，以下句子违反 E-04 纪律（把未确证的预期/定价"
            "状态当作事实断言，且无有效旧基线或可回溯证据）：",
            "",
        ]
        for i, h in enumerate(hits, 1):
            sent = str((h or {}).get("sentence") or "").strip()
            viol = str((h or {}).get("violation") or "").strip()
            lines.append(f"{i}. 原句：「{sent}」")
            if viol:
                lines.append(f"   违规：{viol}")
        claim_ids = [str(c) for c in (verified_claim_ids or []) if c]
        lines += [
            "",
            "请对以上每条命中句三选一处理：",
            "a) 删除该句；",
            "b) 改写为明确的不确定表述（例如「已定价状态为 unknown」或"
            "「若……则……」条件句）；",
            "c) 补上可回溯的证据来源——只能引用本次运行中已 verified 的 "
            "claim id：" + ("、".join(claim_ids) if claim_ids else "（无）") + "。",
            "",
            "严禁同义词替换保留原断言（例如把「已定价」改成「已被消化」"
            "「已反映」「已计入」「已兑现」「市场已知」「充分反映」），"
            "此类改写会被判为失败并整篇丢弃。",
        ]
    if price_section:
        if lines:
            lines += ["", "——以下为同一份报告中的价格归因问题，一并处理——", ""]
        lines.append(price_section)
    if lines:
        lines += [
            "",
            "严格要求：结论、方向、交易动作、概率均不得改变；除上述问题"
            "外其余内容不得改动。请输出修改后的报告全文。",
        ]
    return "\n".join(lines)
