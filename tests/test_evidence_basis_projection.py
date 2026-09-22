"""DAV-1111 B2: evidence_basis 投影——winner/action 到 verified 子事实闭环测试。

Check 10（旁路投影，不进 failed_checks）把裁决实际依据的 verified
子事实形成机读 evidence_basis：

- adopted / partial：从既有 claim_evidence_summary 确定性投影
  verified_evidence 子事实，不依赖 LLM 重复输出；
- rejected_subfact：仅消费 manager verdict 可选
  basis_from_rejected_claim_ids 指认；被指认 cid 必须属于
  rejected_claim_ids 且 summary 中 verified>0，否则只记 warning；
- source 仅允许 adopted / partial / rejected_subfact；
- 投影层绝不把 rejected 改成 partial、不改 adoption 决策、
  不回写 direction_basis。

硬边界：winner / action / adopted / partial / rejected /
consistency_check_passed 在同输入下与修前零变化；warning 绝不进入
failed_checks；无 basis_from_rejected_claim_ids 时不得凭规则猜测。
"""
from __future__ import annotations

import copy
import json

from tradingagents.agents.utils.evidence_verifier import (
    STATUS_UNSUPPORTED,
    STATUS_VERIFIED,
    compute_evidence_basis,
    extract_and_validate_manager_verdict,
    refresh_evidence_basis,
)


def _verdict_block(**overrides) -> str:
    payload = {
        "winner": "bear",
        "direction": "看空",
        "reason": "空头证据占优",
        "position_pct": 0,
        "adopted_claim_ids": [],
        "partially_adopted_claims": [],
        "rejected_claim_ids": [],
    }
    payload.update(overrides)
    return f"裁决正文：经核验空头论点占优。\n<!-- MANAGER_VERDICT: {json.dumps(payload, ensure_ascii=False)} -->"


def _claim(cid: str, speaker_key: str | None = "Bear", stance: str | None = "bearish") -> dict:
    c = {
        "claim_id": cid,
        "speaker": f"{speaker_key} Analyst" if speaker_key else "Analyst",
        # DAV-1193：adopted claim 须为 semantic 可证命题，否则触发
        # semantic_decision 硬闸，与本组测试目标无关
        "claim": "主力净流出1.2亿",
        "evidence": ["主力净流出1.2亿元"],
        "confidence": 0.8,
    }
    if speaker_key is not None:
        c["speaker_key"] = speaker_key
    if stance is not None:
        c["stance"] = stance
    return c


def _verification(cid: str, n_verified: int = 1) -> list[dict]:
    return [
        {"claim_id": cid, "status": STATUS_VERIFIED, "raw": f"主力净流出1.2亿元·凭证{i}"}
        for i in range(1, n_verified + 1)
    ]


def _run(raw, claims, verifications):
    return extract_and_validate_manager_verdict(
        raw,
        claims=claims,
        claims_verification=verifications,
    )


def _items_by_cid(result):
    return {it["claim_id"]: it for it in result["evidence_basis"]["items"]}


# ── 1. adopted 投影确定性：verified 子事实逐条可追溯 ─────────────────────


def test_adopted_projection_deterministic_and_traceable():
    claims = [_claim("INV-1", "Bear", "bearish")]
    vers = _verification("INV-1", n_verified=2)
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, vers)

    items = result["evidence_basis"]["items"]
    assert items == [{
        "claim_id": "INV-1",
        "source": "adopted",
        "verified_subfacts": ["主力净流出1.2亿元·凭证1", "主力净流出1.2亿元·凭证2"],
        "verified_count": 2,
    }]
    # 同输入重算结果逐字节一致（确定性投影）
    result2 = _run(raw, claims, vers)
    assert result2["evidence_basis"] == result["evidence_basis"]
    assert result["consistency_check_passed"] is True
    assert result["failed_checks"] == []


# ── 2. partial 投影：仅采纳 verified 子结论 ───────────────────────────────


def test_partial_projection_only_verified_subfacts():
    claims = [_claim("INV-1", "Bear", "bearish")]
    vers = [
        {"claim_id": "INV-1", "status": STATUS_VERIFIED, "raw": "INV-1 已验证证据"},
        {"claim_id": "INV-1", "status": STATUS_UNSUPPORTED, "raw": "INV-1 未验证证据"},
        {"claim_id": "INV-1", "status": STATUS_VERIFIED, "raw": "INV-1 已验证证据2"},
    ]
    raw = _verdict_block(partially_adopted_claims=["INV-1"])
    result = _run(raw, claims, vers)

    item = _items_by_cid(result)["INV-1"]
    assert item["source"] == "partial"
    # 未验证项绝不进 verified_subfacts
    assert item["verified_subfacts"] == ["INV-1 已验证证据", "INV-1 已验证证据2"]
    assert item["verified_count"] == 2
    assert "INV-1 未验证证据" not in item["verified_subfacts"]


# ── 3. rejected_subfact 正例：rejected claim 的 verified 子事实可重建 ────


def test_rejected_subfact_positive_rebuilds_basis():
    claims = [
        _claim("INV-1", "Bear", "bearish"),
        _claim("INV-2", "Bull", "bullish"),
    ]
    # INV-2 混合证据：verified 1 + unsupported 1 → coverage 50% < 67% → reject，
    # 但仍留有 1 条 verified 子事实可被经理指认。
    vers = _verification("INV-1") + [
        {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证子事实"},
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(
        adopted_claim_ids=["INV-1"],
        rejected_claim_ids=["INV-2"],
        basis_from_rejected_claim_ids=["INV-2"],
    )
    result = _run(raw, claims, vers)

    items = _items_by_cid(result)
    assert items["INV-2"]["source"] == "rejected_subfact"
    assert items["INV-2"]["verified_subfacts"] == ["INV-2 已验证子事实"]
    assert items["INV-2"]["verified_count"] == 1
    assert items["INV-1"]["source"] == "adopted"
    # 投影层绝不改写账本：INV-2 仍是 rejected，未升级为 partial
    assert result["rejected_claim_ids"] == ["INV-2"]
    assert result["partially_adopted_claims"] == []
    assert result["adopted_claim_ids"] == ["INV-1"]
    # 合法指认零告警
    assert not any(str(w).startswith("evidence_basis_") for w in result["warnings"])
    assert result["consistency_check_passed"] is True


# ── 4. 非法 cid：不在 rejected_claim_ids → 只 warning，不 fail-close ──────


def test_rejected_subfact_cid_not_in_rejected_only_warning():
    claims = [_claim("INV-1", "Bear", "bearish"), _claim("INV-2", "Bull", "bullish")]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(
        adopted_claim_ids=["INV-1"],
        rejected_claim_ids=[],
        basis_from_rejected_claim_ids=["INV-2"],
    )
    result = _run(raw, claims, vers)

    assert any("evidence_basis_invalid_rejected_cid" in w for w in result["warnings"])
    assert "INV-2" not in _items_by_cid(result)
    # warning-only：不污染 failed_checks / consistency_check_passed
    assert result["failed_checks"] == []
    assert result["consistency_check_passed"] is True


def test_rejected_subfact_cid_in_adopted_only_warning():
    """指认的 cid 实际在 adopted 账本中 → 非法指认，只 warning。"""
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(
        adopted_claim_ids=["INV-1"],
        basis_from_rejected_claim_ids=["INV-1"],
    )
    result = _run(raw, claims, _verification("INV-1"))
    assert any("evidence_basis_invalid_rejected_cid" in w for w in result["warnings"])
    # adopted 投影仍正常存在（source=adopted，去重后不重复出现）
    items = [it for it in result["evidence_basis"]["items"] if it["claim_id"] == "INV-1"]
    assert len(items) == 1
    assert items[0]["source"] == "adopted"


# ── 5. rejected 但 verified=0 → 只 warning ────────────────────────────────


def test_rejected_subfact_no_verified_only_warning():
    claims = [_claim("INV-1", "Bear", "bearish"), _claim("INV-2", "Bull", "bullish")]
    vers = _verification("INV-1") + [
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(
        adopted_claim_ids=["INV-1"],
        rejected_claim_ids=["INV-2"],
        basis_from_rejected_claim_ids=["INV-2"],
    )
    result = _run(raw, claims, vers)

    assert any("evidence_basis_rejected_no_verified" in w for w in result["warnings"])
    assert "INV-2" not in _items_by_cid(result)
    assert result["consistency_check_passed"] is True


def test_rejected_subfact_cid_unknown_to_summary_only_warning():
    """指认的 rejected cid 不在 claim_evidence_summary 中（无 verified 子事实）→ warning。"""
    raw = _verdict_block(
        rejected_claim_ids=["GHOST-9"],
        basis_from_rejected_claim_ids=["GHOST-9"],
    )
    # claims=None → 不跑 Check 6 的存在性校验，只测投影层行为
    result = extract_and_validate_manager_verdict(raw)
    assert any("evidence_basis_rejected_no_verified" in w for w in result["warnings"])
    assert result["evidence_basis"]["items"] == []


# ── 6. 无 basis_from_rejected_claim_ids：不得凭规则猜测 ───────────────────


def test_no_basis_field_never_guesses_rejected_subfact():
    """rejected claim 即使有 verified 子事实，缺少经理指认时绝不进 basis。"""
    claims = [_claim("INV-1", "Bear", "bearish"), _claim("INV-2", "Bull", "bullish")]
    vers = _verification("INV-1") + [
        {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证子事实"},
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(adopted_claim_ids=["INV-1"], rejected_claim_ids=["INV-2"])
    result = _run(raw, claims, vers)

    items = _items_by_cid(result)
    assert "INV-2" not in items
    assert result["basis_from_rejected_claim_ids"] == []
    assert not any(str(w).startswith("evidence_basis_") for w in result["warnings"])


# ── 7. 旧 MANAGER_VERDICT 无新字段仍可解析（向后兼容）─────────────────────


def test_legacy_verdict_without_new_fields_parses():
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    claims = [_claim("INV-1", "Bear", "bearish")]
    result = _run(raw, claims, _verification("INV-1"))
    assert result["basis_from_rejected_claim_ids"] == []
    assert result["evidence_basis"]["items"][0]["source"] == "adopted"
    assert result["consistency_check_passed"] is True


# ── 8. 零变化硬边界：同输入下裁决字段不受投影影响 ──────────────────────────


def test_evidence_basis_does_not_mutate_verdict_fields():
    claims = [_claim("INV-1", "Bear", "bearish"), _claim("INV-2", "Bull", "bullish")]
    vers = _verification("INV-1") + [
        {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证子事实"},
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(
        adopted_claim_ids=["INV-1"],
        rejected_claim_ids=["INV-2"],
        basis_from_rejected_claim_ids=["INV-2"],
    )
    result = _run(raw, claims, vers)

    assert result["winner"] == "bear"
    assert result["adopted_claim_ids"] == ["INV-1"]
    assert result["rejected_claim_ids"] == ["INV-2"]
    assert result["partially_adopted_claims"] == []
    assert result["consistency_check_passed"] is True
    assert result["failed_checks"] == []


# ── 9. evidence_basis 不得回写 direction_basis ────────────────────────────


def test_rejected_subfact_does_not_relodger_direction_basis():
    """bear winner + 无同向 adopted/partial，但经 rejected_subfact 重建出
    verified 子事实依据时，direction_basis 仍必须是 unledgered——
    两个字段回答不同问题，不得静默改写历史账本状态。"""
    claims = [_claim("INV-2", "Bear", "bearish")]
    vers = [
        {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证子事实"},
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(
        rejected_claim_ids=["INV-2"],
        basis_from_rejected_claim_ids=["INV-2"],
    )
    result = _run(raw, claims, vers)

    assert result["direction_basis"]["status"] == "unledgered"
    assert any("direction_basis_unledgered" in w for w in result["warnings"])
    items = _items_by_cid(result)
    assert items["INV-2"]["source"] == "rejected_subfact"


# ── 10. bull/bear 对称 fixture ────────────────────────────────────────────


def test_bull_winner_symmetric_projection():
    """逻辑不写死 bear：bull winner 下 adopted/partial/rejected_subfact 同样投影。"""
    payload = {
        "winner": "bull",
        "direction": "看多",
        "reason": "多头证据占优",
        "position_pct": 30,
        "entry": "10.0",
        "stop_loss": "9.0",
        "adopted_claim_ids": ["INV-1"],
        "partially_adopted_claims": ["INV-2"],
        "rejected_claim_ids": ["INV-3"],
        "basis_from_rejected_claim_ids": ["INV-3"],
    }
    raw = f"裁决正文：多头胜。\n<!-- MANAGER_VERDICT: {json.dumps(payload, ensure_ascii=False)} -->"
    claims = [
        _claim("INV-1", "Bull", "bullish"),
        _claim("INV-2", "Bull", "bullish"),
        _claim("INV-3", "Bear", "bearish"),
    ]
    vers = (
        _verification("INV-1")
        + [
            {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证证据"},
            {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
            {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证证据2"},
        ]
        + [
            {"claim_id": "INV-3", "status": STATUS_VERIFIED, "raw": "INV-3 已验证子事实"},
            {"claim_id": "INV-3", "status": STATUS_UNSUPPORTED, "raw": "INV-3 未验证项"},
        ]
    )
    result = _run(raw, claims, vers)

    items = _items_by_cid(result)
    assert items["INV-1"]["source"] == "adopted"
    assert items["INV-2"]["source"] == "partial"
    assert items["INV-3"]["source"] == "rejected_subfact"
    assert items["INV-3"]["verified_subfacts"] == ["INV-3 已验证子事实"]
    assert result["winner"] == "bull"


# ── 11. tie winner：投影层照常投影账本（不受方向门控）─────────────────────


def test_tie_winner_still_projects_ledger():
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(winner="tie", direction="中性", adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, _verification("INV-1"))
    assert _items_by_cid(result)["INV-1"]["source"] == "adopted"


# ── 12. refresh：guard 裁剪后账本重算 ────────────────────────────────────


def _run_guard(verdict, claims):
    from tradingagents.agents.managers.research_manager import (
        apply_manager_double_count_guard,
    )

    metrics, verdict, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={
            "double_count_guard_applied": True,
            "double_count_guard_audit": {"excluded_claim_ids": ["INV-2"]},
        },
        expectation_revisions={},
        claims=claims,
        manager_verdict=verdict,
    )
    return verdict


def test_refresh_evidence_basis_after_guard_trim():
    """guard 把 adopted INV-2 剥离后，refresh 必须从 evidence_basis 移除该 item。"""
    claims = [
        _claim("INV-1", "Bear", "bearish"),
        _claim("INV-2", "Bear", "bearish"),
    ]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(adopted_claim_ids=["INV-1", "INV-2"])
    verdict = _run(raw, claims, vers)
    assert "INV-2" in _items_by_cid(verdict)

    verdict = _run_guard(verdict, claims)
    assert verdict["adopted_claim_ids"] == ["INV-1"]

    refresh_evidence_basis(verdict)
    items = _items_by_cid(verdict)
    assert "INV-2" not in items
    assert items["INV-1"]["source"] == "adopted"


def test_refresh_evidence_basis_idempotent_and_field_safe():
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    verdict = _run(raw, claims, _verification("INV-1"))
    snapshot = copy.deepcopy(verdict)

    refresh_evidence_basis(verdict)
    refresh_evidence_basis(verdict)
    assert verdict == snapshot
    eb_w = [w for w in verdict["warnings"] if str(w).startswith("evidence_basis_")]
    assert len(eb_w) == 0


def test_refresh_evidence_basis_rejected_subfact_survives():
    """refresh 后 rejected_subfact 指认不丢失（防虚标规则同样生效）。"""
    claims = [_claim("INV-2", "Bear", "bearish")]
    vers = [
        {"claim_id": "INV-2", "status": STATUS_VERIFIED, "raw": "INV-2 已验证子事实"},
        {"claim_id": "INV-2", "status": STATUS_UNSUPPORTED, "raw": "INV-2 未验证项"},
    ]
    raw = _verdict_block(
        rejected_claim_ids=["INV-2"],
        basis_from_rejected_claim_ids=["INV-2", "GHOST-1"],
    )
    verdict = _run(raw, claims, vers)
    refresh_evidence_basis(verdict)
    items = _items_by_cid(verdict)
    assert items["INV-2"]["source"] == "rejected_subfact"
    assert "GHOST-1" not in items
    eb_w = [w for w in verdict["warnings"] if str(w).startswith("evidence_basis_")]
    assert len(eb_w) == 1
    assert "invalid_rejected_cid" in eb_w[0]


# ── 13. 纯函数级：compute_evidence_basis 边界 ─────────────────────────────


def test_compute_evidence_basis_empty_ledger():
    eb, warns = compute_evidence_basis(
        adopted_claim_ids=[],
        partially_adopted_claims=[],
        rejected_claim_ids=[],
        basis_from_rejected_claim_ids=["INV-1"],
        claim_evidence_summary={},
    )
    assert eb == {"items": []}
    assert any("invalid_rejected_cid" in w for w in warns)


def test_compute_evidence_basis_dedup_and_source_enum():
    """同一 cid 重复指认只出现一次；source 枚举值受限。"""
    summary = {
        "INV-1": {
            "counts": {"verified": 1},
            "verified_evidence": ["INV-1 证据"],
        }
    }
    eb, warns = compute_evidence_basis(
        adopted_claim_ids=["INV-1", "INV-1"],
        partially_adopted_claims=["INV-1"],
        rejected_claim_ids=["INV-1"],
        basis_from_rejected_claim_ids=["INV-1"],
        claim_evidence_summary=summary,
    )
    cids = [it["claim_id"] for it in eb["items"]]
    assert cids == ["INV-1"]
    assert all(it["source"] in {"adopted", "partial", "rejected_subfact"} for it in eb["items"])
    # INV-1 在 rejected 中且 verified>0，但已被 adopted 投影占用（seen 去重），
    # rejected_subfact 指认因 cid∈rejected 且 verified>0 而合法 → 零告警。
    assert warns == []
