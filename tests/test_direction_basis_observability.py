"""DAV-1111 B1: direction_basis warning-only 同向 claim 账本可观测性测试。

Check 9（旁路告警，不进 failed_checks）对 winner 方向与
adopted/partial claim 账本做确定性同向分类：

- ledgered：≥1 个同向 adopted claim；
- partial_only：无同向 adopted，但 ≥1 个同向 partial；
- unledgered：adopted+partial 中无同向 claim（含账本全空）；
- unknown：claim speaker/stance 无法可靠判定；
- not_applicable：tie/abstain winner 不做方向告警。

硬边界：winner / adopted / partial / rejected / consistency_check_passed
在同输入下与修前零变化；warning 绝不进入 failed_checks。
"""
from __future__ import annotations

import json

from tradingagents.agents.utils.evidence_verifier import (
    STATUS_VERIFIED,
    compute_direction_basis,
    extract_and_validate_manager_verdict,
    refresh_direction_basis,
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
        # DAV-1193：claim 文本须为可证实质命题（数值在 verified 语料可命中），
        # 否则 adopted 会触发 semantic_decision 硬闸，与本组测试目标无关
        "claim": "主力净流出1.2亿",
        "evidence": ["主力净流出1.2亿元"],
        "confidence": 0.8,
    }
    if speaker_key is not None:
        c["speaker_key"] = speaker_key
    if stance is not None:
        c["stance"] = stance
    return c


def _verification(cid: str) -> list[dict]:
    return [{"claim_id": cid, "status": STATUS_VERIFIED, "raw": "主力净流出1.2亿元"}]


def _run(raw, claims, verifications):
    return extract_and_validate_manager_verdict(
        raw,
        claims=claims,
        claims_verification=verifications,
    )


# ── 1. bear winner + 账本全空 → unledgered ────────────────────────────────


def test_bear_winner_empty_ledger_unledgered():
    raw = _verdict_block(rejected_claim_ids=["INV-1"])
    result = _run(raw, [_claim("INV-1", "Bull", "bullish")], _verification("INV-1"))
    assert result["direction_basis"]["status"] == "unledgered"
    assert result["direction_basis"]["same_direction_claims"] == []
    assert any("direction_basis_unledgered" in w for w in result["warnings"])
    # warning-only：不污染 failed_checks / consistency_check_passed
    assert result["failed_checks"] == []
    assert result["consistency_check_passed"] is True
    assert result["winner"] == "bear"


# ── 2. bear winner + 只有 Bull adopted/partial → unledgered ───────────────


def test_bear_winner_only_bull_claims_unledgered():
    claims = [_claim("INV-1", "Bull", "bullish"), _claim("INV-2", "Bull", "bullish")]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(adopted_claim_ids=["INV-1"], partially_adopted_claims=["INV-2"])
    result = _run(raw, claims, vers)
    assert result["direction_basis"]["status"] == "unledgered"
    assert any("direction_basis_unledgered" in w for w in result["warnings"])
    assert result["consistency_check_passed"] is True


# ── 3. bear winner + 仅 Bear partial → partial_only ───────────────────────


def test_bear_winner_bear_partial_only():
    claims = [_claim("INV-1", "Bull", "bullish"), _claim("INV-2", "Bear", "bearish")]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(adopted_claim_ids=["INV-1"], partially_adopted_claims=["INV-2"])
    result = _run(raw, claims, vers)
    assert result["direction_basis"]["status"] == "partial_only"
    assert result["direction_basis"]["same_direction_claims"] == [
        {"claim_id": "INV-2", "source": "partial"}
    ]
    assert any("direction_basis_partial_only" in w for w in result["warnings"])
    assert not any("unledgered" in w for w in result["warnings"])


# ── 4. bear winner + Bear adopted → ledgered ──────────────────────────────


def test_bear_winner_bear_adopted_ledgered():
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, _verification("INV-1"))
    assert result["direction_basis"]["status"] == "ledgered"
    assert result["direction_basis"]["same_direction_claims"] == [
        {"claim_id": "INV-1", "source": "adopted"}
    ]
    assert result["warnings"] == []
    assert result["consistency_check_passed"] is True


# ── 5. bull 对称：ledgered 与 unledgered ──────────────────────────────────


def test_bull_winner_bull_adopted_ledgered():
    claims = [_claim("INV-1", "Bull", "bullish")]
    raw = _verdict_block(
        winner="bull",
        direction="看多",
        reason="多头证据占优",
        position_pct=50,
        entry="20.0",
        target="25.0",
        stop_loss="19.0",
        adopted_claim_ids=["INV-1"],
    )
    result = _run(raw, claims, _verification("INV-1"))
    assert result["direction_basis"]["status"] == "ledgered"
    assert result["warnings"] == []


def test_bull_winner_only_bear_claims_unledgered():
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(
        winner="bull",
        direction="看多",
        reason="多头证据占优",
        position_pct=50,
        entry="20.0",
        target="25.0",
        stop_loss="19.0",
        adopted_claim_ids=["INV-1"],
    )
    result = _run(raw, claims, _verification("INV-1"))
    assert result["direction_basis"]["status"] == "unledgered"
    assert any("direction_basis_unledgered" in w for w in result["warnings"])
    assert result["consistency_check_passed"] is True


# ── 6. tie / abstain 不误报方向告警 ───────────────────────────────────────


def test_tie_winner_no_direction_warning():
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(winner="tie", direction="中性", reason="势均力敌", adopted_claim_ids=[])
    result = _run(raw, claims, _verification("INV-1"))
    assert result["winner"] == "tie"
    assert result["direction_basis"]["status"] == "not_applicable"
    assert result["warnings"] == []


# ── 7. speaker_key 缺失降级 stance；仍不可判 → unknown ────────────────────


def test_speaker_key_missing_falls_back_to_stance():
    claims = [_claim("INV-1", speaker_key=None, stance="bearish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, _verification("INV-1"))
    assert result["direction_basis"]["status"] == "ledgered"
    assert result["warnings"] == []


def test_side_undetermined_unknown():
    claims = [_claim("INV-1", speaker_key=None, stance=None)]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, _verification("INV-1"))
    assert result["direction_basis"]["status"] == "unknown"
    assert result["direction_basis"]["undetermined_claim_ids"] == ["INV-1"]
    assert any("direction_basis_unknown" in w for w in result["warnings"])
    assert not any("unledgered" in w for w in result["warnings"])
    # unknown 不误报 unledgered，且不进 failed_checks
    assert result["consistency_check_passed"] is True


# ── 8. B2 钉样形态：bull winner + Bull adopted 保持 ledgered 负例 ──────────


def test_b2_fixture_shape_bull_adopted_ledgered():
    """钉样 9e2dd38b 形态：bull winner + adopted[INV-1]=Bull → ledgered，零告警。"""
    claims = [_claim("INV-1", "Bull", "bullish"), _claim("INV-6", "Bear", "bearish")]
    vers = _verification("INV-1") + _verification("INV-6")
    raw = _verdict_block(
        winner="bull",
        direction="看多",
        reason="多头主线成立",
        position_pct=55,
        entry="30.0",
        target="36.0",
        stop_loss="28.5",
        adopted_claim_ids=["INV-1"],
        rejected_claim_ids=["INV-6"],
    )
    result = _run(raw, claims, vers)
    assert result["direction_basis"]["status"] == "ledgered"
    assert result["direction_basis"]["same_direction_claims"] == [
        {"claim_id": "INV-1", "source": "adopted"}
    ]
    assert result["warnings"] == []
    assert result["consistency_check_passed"] is True


# ── 边界：winner/账本字段不被 direction_basis 计算改变 ────────────────────


def test_ledger_fields_unchanged_by_observability():
    claims = [_claim("INV-1", "Bull", "bullish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"], rejected_claim_ids=[])
    result = _run(raw, claims, _verification("INV-1"))
    assert result["winner"] == "bear"
    assert result["adopted_claim_ids"] == ["INV-1"]
    assert result["partially_adopted_claims"] == []
    assert result["rejected_claim_ids"] == []
    assert "direction_basis_unledgered" not in result["failed_checks"]


# ── 返修（DAV-1189 打回项）：double_count_guard 裁剪后必须重算刷新 ────────


def test_double_count_guard_strip_refreshes_direction_basis():
    """唯一同向 adopted 被 guard 剥离后，刷新必须把 ledgered 改写为 unledgered。"""
    from tradingagents.agents.analysts.news_analyst import (
        DOUBLE_COUNT_ACCOUNTED_FOR,
        EVENT_TYPE_FUNDAMENTAL,
        STATUS_AVAILABLE,
        make_default_expectation_revision,
    )
    from tradingagents.agents.managers.research_manager import (
        apply_manager_double_count_guard,
    )

    # INV-1 先登记事件 ev1（未被采纳）；INV-2 同事件 Bear adopted → guard 剥离 INV-2
    claims = [
        {**_claim("INV-1", "Bear", "bearish"), "event_id": "ev1",
         "claim_text": "公司业绩预告大幅下滑"},
        {**_claim("INV-2", "Bear", "bearish"), "event_id": "ev1",
         "claim_text": "新闻报道业绩预告大幅下滑"},
    ]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(adopted_claim_ids=["INV-2"])
    result = _run(raw, claims, vers)
    # 裁剪前：INV-2 同向 adopted → ledgered
    assert result["direction_basis"]["status"] == "ledgered"
    assert result["warnings"] == []

    fund_er = make_default_expectation_revision(
        event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE
    )
    fund_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": True,
    }
    _, verdict, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2},
        expectation_revisions={"fundamentals": fund_er},
        claims=claims,
        manager_verdict=result,
    )
    # guard 已把唯一同向 adopted INV-2 剥离
    assert verdict["adopted_claim_ids"] == []

    # 未刷新前若直接落库将自相矛盾；刷新后必须变为 unledgered 且告警重写
    refresh_direction_basis(verdict, claims=claims)
    assert verdict["direction_basis"]["status"] == "unledgered"
    assert verdict["direction_basis"]["same_direction_claims"] == []
    assert any("direction_basis_unledgered" in w for w in verdict["warnings"])
    # 无残留 ledgered 时代告警
    assert len([w for w in verdict["warnings"] if str(w).startswith("direction_basis_")]) == 1
    # failed_checks 依旧不被污染
    assert "direction_basis_unledgered" not in verdict["failed_checks"]


def _accounted_er():
    from tradingagents.agents.analysts.news_analyst import (
        DOUBLE_COUNT_ACCOUNTED_FOR,
        EVENT_TYPE_FUNDAMENTAL,
        STATUS_AVAILABLE,
        make_default_expectation_revision,
    )

    fund_er = make_default_expectation_revision(
        event_type=EVENT_TYPE_FUNDAMENTAL, status=STATUS_AVAILABLE
    )
    fund_er["double_count_guard"] = {
        "status": DOUBLE_COUNT_ACCOUNTED_FOR,
        "prevent_double_voting": True,
    }
    return {"fundamentals": fund_er}


def _event_claim(cid, speaker_key, stance, event_id=None, text=None):
    c = _claim(cid, speaker_key, stance)
    if event_id:
        c["event_id"] = event_id
    if text:
        c["claim_text"] = text
    return c


def _run_guard(verdict, claims):
    from tradingagents.agents.managers.research_manager import (
        apply_manager_double_count_guard,
    )

    _, verdict, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={"independent_cluster_count": 2},
        expectation_revisions=_accounted_er(),
        claims=claims,
        manager_verdict=verdict,
    )
    return verdict


# b) adopted 被剥但仍有同向 partial → 终态 partial_only


def test_guard_strip_adopted_partial_remains_partial_only():
    claims = [
        _event_claim("INV-1", "Bear", "bearish", event_id="ev1", text="业绩预告大幅下滑"),
        _event_claim("INV-2", "Bear", "bearish", event_id="ev1", text="新闻报道业绩预告大幅下滑"),
        _claim("INV-3", "Bear", "bearish"),
    ]
    vers = _verification("INV-1") + _verification("INV-2") + _verification("INV-3")
    raw = _verdict_block(adopted_claim_ids=["INV-2"], partially_adopted_claims=["INV-3"])
    verdict = _run(raw, claims, vers)
    assert verdict["direction_basis"]["status"] == "ledgered"

    verdict = _run_guard(verdict, claims)
    assert verdict["adopted_claim_ids"] == []
    assert verdict["partially_adopted_claims"] == ["INV-3"]

    refresh_direction_basis(verdict, claims=claims)
    assert verdict["direction_basis"]["status"] == "partial_only"
    assert verdict["direction_basis"]["same_direction_claims"] == [
        {"claim_id": "INV-3", "source": "partial"}
    ]
    assert any("direction_basis_partial_only" in w for w in verdict["warnings"])
    assert len([w for w in verdict["warnings"] if str(w).startswith("direction_basis_")]) == 1


# c) 被剥的是对侧 claim → direction_basis 不变


def test_guard_strip_opposite_side_direction_basis_unchanged():
    claims = [
        _event_claim("INV-1", "Bull", "bullish", event_id="ev1", text="业绩预告大幅增长"),
        _event_claim("INV-2", "Bull", "bullish", event_id="ev1", text="新闻报道业绩预告大幅增长"),
        _claim("INV-3", "Bear", "bearish"),
    ]
    vers = _verification("INV-1") + _verification("INV-2") + _verification("INV-3")
    raw = _verdict_block(adopted_claim_ids=["INV-2", "INV-3"])
    verdict = _run(raw, claims, vers)
    assert verdict["direction_basis"]["status"] == "ledgered"
    before_db = dict(verdict["direction_basis"])
    before_warnings = list(verdict["warnings"])

    verdict = _run_guard(verdict, claims)
    assert verdict["adopted_claim_ids"] == ["INV-3"]  # 剥掉的是对侧 Bull INV-2

    refresh_direction_basis(verdict, claims=claims)
    assert verdict["direction_basis"] == before_db
    assert verdict["warnings"] == before_warnings
    assert verdict["direction_basis"]["same_direction_claims"] == [
        {"claim_id": "INV-3", "source": "adopted"}
    ]


# d) guard no-op → 返修前后逐字段一致


def test_guard_noop_verdict_field_identical():
    from tradingagents.agents.managers.research_manager import (
        apply_manager_double_count_guard,
    )

    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    verdict = _run(raw, claims, _verification("INV-1"))
    import copy

    snapshot = copy.deepcopy(verdict)

    # 无 expectation_revisions → guard 不激活，账本原样
    _, verdict, _ = apply_manager_double_count_guard(
        claim_cluster_metrics={},
        expectation_revisions={},
        claims=claims,
        manager_verdict=verdict,
    )
    refresh_direction_basis(verdict, claims=claims)
    assert verdict == snapshot


# e) 重算幂等：重复 refresh 不产生重复 warnings


def test_refresh_direction_basis_idempotent_when_unchanged():
    """账本未被裁剪时，refresh 与初次计算结果一致（幂等）。"""
    claims = [_claim("INV-1", "Bear", "bearish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    result = _run(raw, claims, _verification("INV-1"))
    before_db = dict(result["direction_basis"])
    before_warnings = list(result["warnings"])
    refresh_direction_basis(result, claims=claims)
    assert result["direction_basis"] == before_db
    assert result["warnings"] == before_warnings


def test_refresh_idempotent_no_duplicate_warnings():
    claims = [_claim("INV-1", "Bull", "bullish")]
    raw = _verdict_block(adopted_claim_ids=["INV-1"])
    verdict = _run(raw, claims, _verification("INV-1"))
    assert verdict["direction_basis"]["status"] == "unledgered"
    refresh_direction_basis(verdict, claims=claims)
    refresh_direction_basis(verdict, claims=claims)
    db_w = [w for w in verdict["warnings"] if str(w).startswith("direction_basis_")]
    assert len(db_w) == 1
    assert "unledgered" in db_w[0]


# f) refresh 不得改变 winner/action/adopted/partial/rejected/consistency 等裁决字段


def test_refresh_does_not_mutate_verdict_fields():
    claims = [
        _event_claim("INV-1", "Bear", "bearish", event_id="ev1", text="业绩预告下滑"),
        _event_claim("INV-2", "Bear", "bearish", event_id="ev1", text="新闻业绩预告下滑"),
    ]
    vers = _verification("INV-1") + _verification("INV-2")
    raw = _verdict_block(adopted_claim_ids=["INV-2"], rejected_claim_ids=[])
    verdict = _run(raw, claims, vers)

    verdict = _run_guard(verdict, claims)
    post_guard = {k: verdict[k] for k in (
        "winner", "direction", "adopted_claim_ids", "partially_adopted_claims",
        "rejected_claim_ids", "consistency_check_passed", "failed_checks",
        "position_pct", "reason",
    )}
    refresh_direction_basis(verdict, claims=claims)
    for k, v in post_guard.items():
        assert verdict[k] == v, f"refresh 不得改写 {k}"
    # 仅 direction_basis / warnings 允许变化
    assert verdict["direction_basis"]["status"] == "unledgered"


def test_summary_empty_side_fields_falls_back_to_claims():
    """claim_evidence_summary 命中但 speaker_key/stance 均空时，回退 claims 判定。"""
    claims = [_claim("INV-1", "Bear", "bearish")]
    summary = {
        "INV-1": {
            "speaker_key": "",
            "stance": "",
            "counts": {"total": 1, "verified": 1},
        }
    }
    db, warns = compute_direction_basis(
        winner="bear",
        adopted_claim_ids=["INV-1"],
        partially_adopted_claims=[],
        claim_evidence_summary=summary,
        claims=claims,
    )
    assert db["status"] == "ledgered"
    assert db["same_direction_claims"] == [{"claim_id": "INV-1", "source": "adopted"}]
    assert warns == []
