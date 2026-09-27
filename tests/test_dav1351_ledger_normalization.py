"""DAV-1351 — 研究经理一致性硬门三类账本归类确定性纠正回归（零 LLM）。

纠正只改账本归类，不新增采纳、不降门：

① reject_in_partial：partially_adopted 中的 semantic_decision=reject 论点，
   证据消费物全为 verified 子事实（verified>0）且裁决 reason/正文/dispute_map
   不含其未证命题锚点 → 确定性改挂 rejected + basis_from_rejected_claim_ids
   （rejected_subfact 通道），审计记录 ledger_normalized:reject_to_subfact:<cid>。
   锚点被消费（最长公共子串≥3 字）时照拦。

② coverage：全额采纳论点存在未核实原子，全部 unsupported 原子逐字出现于
   经理自填 excluded_evidence（含子串形态）→ 确定性改挂 partially_adopted，
   审计记录 ledger_normalized:adopt_to_partial:<cid>。任一未剔除原子 → 照拦。

③ winner_conflict 锚定：正文/机读胜负矛盾只匹配显式裁决语
   （综合裁决…X胜 / 裁定 X 胜 / winner=X），分歧表角色词不触发。

生产回归样例（DAV-1311 语料）：
- f4f0cac6:st INV-5 应纠正；28f6a1e0:mt INV-10（reason 含「双顶假突破」）仍拦；
- 1312d27a:st INV-1/INV-3 应纠正；fb53ab62 INV-3（'18亿'未剔除）仍拦；
- d93a03ef:st（「空头胜出维度」标题误命中）应放行。
"""
from __future__ import annotations

from tradingagents.agents.utils.evidence_verifier import (
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    extract_and_validate_manager_verdict,
)

# ── INV-2 型 fixture：legacy 证据全绿但 semantic_decision=reject ──────────
# （与 test_semantic_gate_b2._inv2_claims_and_ver 同构）
INV2_CLAIM = "食安利空已定价超6天，Q1扣非增20%与高股息构筑估值底"
INV2_VERIFIED = [
    "2026Q1扣非净利润同比增长20.24%",
    "高股息分红方案，账面现金56.97亿元",
]


def _inv2_claims_and_ver():
    claims = [{
        "claim_id": "INV-2", "speaker": "Bull", "speaker_key": "Bull",
        "stance": "bullish", "claim": INV2_CLAIM,
        "evidence": INV2_VERIFIED,
    }]
    ver = [
        {"claim_id": "INV-2", "raw": INV2_VERIFIED[0], "status": STATUS_VERIFIED},
        {"claim_id": "INV-2", "raw": INV2_VERIFIED[1], "status": STATUS_VERIFIED},
    ]
    return claims, ver


# ── INV-5 型 fixture：混合证据（2 verified + 1 unsupported → partial）──────
def _inv5_claims_and_ver():
    claims = [{
        "claim_id": "INV-5", "speaker": "Bull", "speaker_key": "Bull",
        "stance": "bullish",
        "claim": "主力净流入5.2亿元与情绪看多占比65%",
        "evidence": ["主力净流入5.2亿元", "情绪报告看多占比65%", "某机构私下调研看好翻倍"],
    }]
    ver = [
        {"claim_id": "INV-5", "raw": "主力净流入5.2亿元", "status": STATUS_VERIFIED},
        {"claim_id": "INV-5", "raw": "情绪报告看多占比65%", "status": STATUS_VERIFIED},
        {"claim_id": "INV-5", "raw": "某机构私下调研看好翻倍", "status": STATUS_UNSUPPORTED},
    ]
    return claims, ver


# ═════════════════════════ ① reject→rejected_subfact ════════════════════════

def test_dav1351_reject_in_partial_normalized_to_subfact_channel():
    """reject 论点误挂 partial 且只消费 verified 子事实 → 确定性改挂 rejected。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文：多空证据互搏，维持观望。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "证据分歧维持观望", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-2"], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert "INV-2" not in v["partially_adopted_claims"]
    assert "INV-2" in v["rejected_claim_ids"]
    assert "INV-2" in v["basis_from_rejected_claim_ids"]
    assert "ledger_normalized:reject_to_subfact:INV-2" in v["ledger_normalizations"]
    # rejected_subfact 通道投影 verified 子事实
    items = {i["claim_id"]: i for i in v["evidence_basis"]["items"]}
    assert items["INV-2"]["source"] == "rejected_subfact"
    assert items["INV-2"]["verified_count"] == 2
    # 归类纠正后不再因 reject_in_partial 拦截
    assert not any("INV-2" in e and "semantic_decision=reject" in e
                   for e in v["failed_checks"])
    assert v["consistency_check_passed"] is True


def test_dav1351_reject_in_partial_anchor_consumed_still_fails():
    """未证命题锚点出现在裁决文本 → 不纠正、照拦（28f6a1e0 INV-10 形态）。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "食安利空已定价超6天，利空出尽观望", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-2"], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert "INV-2" in v["partially_adopted_claims"]
    assert "INV-2" not in v["basis_from_rejected_claim_ids"]
    assert not v["ledger_normalizations"]
    assert any("semantic_decision=reject" in e and "INV-2" in e
               for e in v["failed_checks"])


def test_dav1351_reject_anchor_in_dispute_map_still_fails():
    """未证命题锚点出现在 dispute_map 字段 → 同样照拦。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "观望", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-2"], "rejected_claim_ids": [], "dispute_map": [{"data_point": "扣非与股息构筑估值底是否成立", "bull_interpretation": "成立", "bear_interpretation": "不成立", "evidence_decision": "Q1扣非增20%与高股息构筑估值底未获验证", "winner": "tie"}]} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert "INV-2" in v["partially_adopted_claims"]
    assert not v["ledger_normalizations"]


def test_dav1351_reject_in_partial_zero_verified_still_fails():
    """reject_no_supported_subset（verified=0）无子事实可消费 → 照拦。"""
    claims = [{
        "claim_id": "INV-9", "speaker": "Bull", "speaker_key": "Bull",
        "stance": "bullish", "claim": "某项未经证实的远期利好",
        "evidence": ["未经证实的传闻一"],
    }]
    ver = [{"claim_id": "INV-9", "raw": "未经证实的传闻一", "status": STATUS_UNSUPPORTED}]
    raw = """裁决正文。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "观望", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-9"], "rejected_claim_ids": []} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert v["consistency_check_passed"] is False
    assert "INV-9" in v["partially_adopted_claims"]
    assert not v["ledger_normalizations"]


def test_dav1351_reject_already_in_basis_from_rejected_dedup():
    """21e993fd 形态：partial 与 basis_from_rejected 双列 → 纠正后单挂不重复。"""
    claims, ver = _inv2_claims_and_ver()
    raw = """裁决正文：观望。
<!-- MANAGER_VERDICT: {"winner": "tie", "direction": "中性", "reason": "证据分歧观望", "position_pct": 0, "adopted_claim_ids": [], "partially_adopted_claims": ["INV-2"], "rejected_claim_ids": [], "basis_from_rejected_claim_ids": ["INV-2"]} -->"""
    v = extract_and_validate_manager_verdict(raw, claims_verification=ver, claims=claims)
    assert "INV-2" not in v["partially_adopted_claims"]
    assert v["basis_from_rejected_claim_ids"].count("INV-2") == 1
    assert "INV-2" in v["rejected_claim_ids"]
    assert "ledger_normalized:reject_to_subfact:INV-2" in v["ledger_normalizations"]


