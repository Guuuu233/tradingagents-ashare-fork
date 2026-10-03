"""DAV-1432: 保存前机读块非法不再作废整份双档，改为按档降级。

合成夹具（D-040），零模型调用、零数据库。

裁定覆盖：
- 裁定 1：5 类 LEAK（图内接受、保存期拒绝）改为「图内接受、保存期也接受」。
- 裁定 2：按档降级；根级块按内容归属；两种保存形状都覆盖；就地改写、幂等；
  原文写入 machine_block_invalid.raw 且该键不参与扫描。
- 裁定 4：两档都非法 → 落库 completed 但 A4 统计里算失败。
"""

import json

import pytest

from api.services import report_service


# ── 夹具工具 ────────────────────────────────────────────────────────────────

_VALID_PAYLOAD = {
    "responded_claim_ids": [],
    "new_claims": [
        {
            "claim": "消费升级主线持续验证",
            "evidence": ["三季报净利润增长超预期"],
            "confidence": 0.85,
            "target_claim_ids": [],
        }
    ],
    "resolved_claim_ids": [],
    "unresolved_claim_ids": [],
    "next_focus_claim_ids": ["INV-1"],
    "round_summary": "首轮多头建立核心 claim",
    "round_goal": "建立最核心的正反两方 claim",
}


def _block(payload, tag="DEBATE_STATE"):
    return f"<!-- {tag}: {json.dumps(payload, ensure_ascii=False)} -->"


def _valid_response(tag="DEBATE_STATE"):
    return f"详细论证消费升级逻辑。\n{_block(_VALID_PAYLOAD, tag)}"


def _bad_json_response(tag="DEBATE_STATE"):
    """非法块：模型吐了截断/坏 JSON。"""
    return f'详细论证消费升级逻辑。\n<!-- {tag}: {{"new_claims": [bad json}} -->'


def _arrow_payload():
    """L1/L2：字符串字面量内含 ``-->``（中文财经常见箭头表达）。"""
    return {
        "responded_claim_ids": [],
        "new_claims": [
            {
                "claim": "价格上破 9.15 元 --> 触发止盈",
                "evidence": ["量能确认 A --> B"],
                "confidence": 0.7,
                "target_claim_ids": [],
            }
        ],
        "resolved_claim_ids": [],
        "unresolved_claim_ids": [],
        "round_summary": "价格上破 9.15 元 --> 触发止盈",
        "round_goal": "确认量能",
    }


def _attempt(raw_response, *, accepted=True, message_index=1):
    return {
        "attempt_index": 1,
        "message_index": message_index,
        "debate_round": 1,
        "speaker": "Bull Analyst",
        "speaker_key": "Bull",
        "parse_status": "valid" if accepted else "invalid",
        "error_detail": "" if accepted else "invalid",
        "raw_response": raw_response,
        "accepted": accepted,
    }


def _debate_state(raw_response, *, accepted=True, claims=None):
    return {
        "count": 1,
        "claims": claims if claims is not None else [dict(_VALID_PAYLOAD["new_claims"][0], claim_id="INV-1")],
        "history": "Bull Analyst: 详细论证消费升级逻辑。",
        "bull_history": "Bull Analyst: 详细论证消费升级逻辑。",
        "round_messages": [{"message_index": 1, "attempts": [_attempt(raw_response, accepted=accepted)]}],
        "attempts": [_attempt(raw_response, accepted=accepted)],
    }


def _dual_report(short_raw, medium_raw, *, short_accepted=True, medium_accepted=True,
                 shared_raw=None, root_raw=None):
    """有 horizons 的双档保存形状。"""
    report = {
        "horizons": {
            "short": {
                "horizon": "short",
                "investment_debate_state": _debate_state(short_raw, accepted=short_accepted),
                "trade_action": "BUY",
                "analysis_status": "EXECUTABLE",
            },
            "medium": {
                "horizon": "medium",
                "investment_debate_state": _debate_state(medium_raw, accepted=medium_accepted),
                "trade_action": "SELL",
                "analysis_status": "EXECUTABLE",
            },
        },
        "short_term": {
            "investment_debate_state": _debate_state(short_raw, accepted=short_accepted),
            "trade_action": "BUY",
            "analysis_status": "EXECUTABLE",
        },
        "medium_term": {
            "investment_debate_state": _debate_state(medium_raw, accepted=medium_accepted),
            "trade_action": "SELL",
            "analysis_status": "EXECUTABLE",
        },
        "decision": None,
    }
    if shared_raw is not None:
        report["shared_section"] = {"notes": shared_raw}
    if root_raw is not None:
        report["investment_debate_state"] = _debate_state(root_raw, accepted=True)
    return report


def _horizon_of(report, name):
    return report["horizons"][name]


# ── 裁定 1：5 类 LEAK 必须变为「图内接受、保存期也接受」 ──────────────────

@pytest.mark.parametrize(
    "case_id,payload",
    [
        ("L1_round_summary_arrow", {**_VALID_PAYLOAD, "round_summary": "价格上破 9.15 元 --> 触发止盈"}),
        ("L2_evidence_arrow", {**_VALID_PAYLOAD, "new_claims": [
            {"claim": "x", "evidence": ["A --> B"], "confidence": 0.5, "target_claim_ids": []}
        ]}),
    ],
)
def test_裁定1_arrow_in_string_now_accepted_anywhere(case_id, payload):
    """L1/L2：根因是朴素 find('-->') 截断了 JSON。与位置无关，一律修复。"""
    report = {"final_trade_decision": f"正文。\n{_block(payload)}"}
    report_service.validate_report_machine_blocks(report)


@pytest.mark.parametrize(
    "case_id,payload",
    [
        ("L3_confidence_missing", {**_VALID_PAYLOAD, "new_claims": [
            {"claim": "x", "evidence": [], "target_claim_ids": []}
        ]}),
        ("L4_confidence_out_of_range", {**_VALID_PAYLOAD, "new_claims": [
            {"claim": "x", "evidence": [], "confidence": 5, "target_claim_ids": []}
        ]}),
        ("L5_confidence_percentage", {**_VALID_PAYLOAD, "new_claims": [
            {"claim": "x", "evidence": [], "confidence": "50%", "target_claim_ids": []}
        ]}),
    ],
)
def test_裁定1_confidence_downgrade_only_for_accepted_attempts(case_id, payload):
    """裁定 1 的降级范围限定为“图内已接受的尝试”（即 attempts[].raw_response）。

    报告正文（final_trade_decision）不在此列，保留 DAV-210 的 fail-closed 契约。
    """
    raw = f"详细论证。\n{_block(payload)}"
    attempt_report = {"investment_debate_state": {"attempts": [_attempt(raw)]}}
    report_service.validate_report_machine_blocks(attempt_report)

    prose_report = {"final_trade_decision": raw}
    with pytest.raises(ValueError, match="error=payload_schema"):
        report_service.validate_report_machine_blocks(prose_report)


def test_裁定1_arrow_block_round_trips_intact():
    """L1/L2 的根因：朴素 find('-->') 截断了 JSON。字符串感知定位后必须能整块解析。"""
    report = {"final_trade_decision": f"正文。\n{_block(_arrow_payload())}"}
    report_service.validate_report_machine_blocks(report)

    # 块原文未被改写
    assert report["final_trade_decision"].count("<!-- DEBATE_STATE:") == 1
    assert "-->" in report["final_trade_decision"]


def test_裁定1_trailing_prose_reverse_case_still_accepted():
    """反向例：块后有尾随正文，保存期本就接受，不得因本次改动而收紧。"""
    report = {"final_trade_decision": f"正文。\n{_block(_VALID_PAYLOAD)}\n补充说明。"}
    report_service.validate_report_machine_blocks(report)


def test_裁定1_other_strict_checks_still_fail_closed():
    """保存期其余严格检查保持不变：非法 JSON / 截断 / 重复 / 缺冒号 / 非对象 / 类型错误。"""
    with pytest.raises(ValueError, match="error=invalid_json"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {\"new_claims\": [bad json} -->"}
        )
    with pytest.raises(ValueError, match="error=truncated"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {\"new_claims\": []"}
        )
    with pytest.raises(ValueError, match="error=duplicated"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {} -->\n<!-- DEBATE_STATE: {} -->"}
        )
    with pytest.raises(ValueError, match="error=missing_colon"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE {\"new_claims\": []} -->"}
        )
    with pytest.raises(ValueError, match="error=not_an_object"):
        report_service.validate_report_machine_blocks({"d": "正文\n<!-- DEBATE_STATE: [1,2] -->"})
    with pytest.raises(ValueError, match="error=payload_schema"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n" + _block({"responded_claim_ids": "notalist", "new_claims": []})}
        )
    with pytest.raises(ValueError, match="error=payload_schema"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n" + _block({"round_summary": 5, "new_claims": []})}
        )


def test_裁定1_legacy_reason_phrases_preserved():
    """保留原有报错文案，避免打断既有日志检索与 DAV-210 回归断言。"""
    with pytest.raises(ValueError, match="DEBATE_STATE machine block contains invalid JSON"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {\"new_claims\": [bad json} -->"}
        )
    with pytest.raises(ValueError, match="DEBATE_STATE machine block is truncated"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {\"new_claims\": []"}
        )
    with pytest.raises(ValueError, match="DEBATE_STATE machine block must not be duplicated"):
        report_service.validate_report_machine_blocks(
            {"d": "正文\n<!-- DEBATE_STATE: {} -->\n<!-- DEBATE_STATE: {} -->"}
        )
    with pytest.raises(ValueError, match="RISK_STATE machine block contains invalid JSON"):
        report_service.validate_report_machine_blocks({"d": "正文\n<!-- RISK_STATE: {invalid} -->"})


def test_裁定1_error_message_carries_metadata_not_prose():
    """卡面第 1 条：error 只写元信息（标签/档位/字段路径/错误类型），不含正文。"""
    secret = "机密正文机密正文机密正文机密正文机密正文机密正文机密正文机密正文机密正文机密正文机密正文机密正文"
    with pytest.raises(ValueError) as exc:
        report_service.validate_report_machine_blocks(
            {"horizons": {"medium": {"investment_debate_state": {"attempts": [
                _attempt(f"{secret}\n<!-- DEBATE_STATE: {{bad}} -->")
            ]}}}}
        )
    message = str(exc.value)
    assert "tag=DEBATE_STATE" in message
    assert "horizon=medium" in message
    assert "horizons.medium.investment_debate_state.attempts.[0].raw_response" in message
    assert "error=invalid_json" in message
    assert "json_error=JSONDecodeError" in message
    assert secret not in message


# ── 裁定 2：按档降级 ────────────────────────────────────────────────────────

def test_裁定2_single_horizon_invalid_other_horizon_unaffected():
    report = _dual_report(_valid_response(), _bad_json_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    medium = _horizon_of(report, "medium")
    assert medium["trade_action"] == "NO_TRADE"
    assert medium["analysis_status"] == "ABSTAIN"
    assert "machine_block_invalid" in medium["reason_codes"]

    short = _horizon_of(report, "short")
    assert short["trade_action"] == "BUY"
    assert short["analysis_status"] == "EXECUTABLE"
    assert "reason_codes" not in short


def test_裁定2_both_alias_copies_are_rewritten():
    """horizons.short 与 short_term 是相等但不同的对象，两处都必须改。"""
    report = _dual_report(_bad_json_response(), _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    for payload in (
        report["horizons"]["short"]["investment_debate_state"],
        report["short_term"]["investment_debate_state"],
    ):
        assert "<!-- MACHINE_BLOCK_INVALID: DEBATE_STATE -->" in payload["attempts"][0]["raw_response"]
        assert "bad json" not in payload["attempts"][0]["raw_response"]


def test_裁定2_raw_text_preserved_under_machine_block_invalid():
    report = _dual_report(_bad_json_response(), _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    # 切片内的块：证据写在短档切片上（两个别名副本各一份）
    short_evidence = report["horizons"]["short"]["machine_block_invalid"]
    assert "bad json" in short_evidence["raw"][0]["text"]
    assert short_evidence["raw"][0]["tag"] == "DEBATE_STATE"
    assert short_evidence["raw"][0]["reason"] == "invalid_json"
    assert report["short_term"]["machine_block_invalid"]["raw"][0]["text"] == (
        short_evidence["raw"][0]["text"]
    )
    # 长档干净，不应被写入证据
    assert "machine_block_invalid" not in report["horizons"]["medium"]


def test_裁定2_root_level_evidence_written_on_report_root():
    """根级块的证据写在报告根上。"""
    report = _dual_report(_valid_response(), _valid_response())
    report["investment_debate_state"] = _debate_state(_bad_json_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)
    assert "bad json" in report["machine_block_invalid"]["raw"][0]["text"]


def test_裁定2_raw_key_is_scan_exempt():
    """machine_block_invalid.raw 内含非法块原文，校验器不得把它当机读块解析。"""
    report = _dual_report(_bad_json_response(), _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report["horizons"]["short"]["machine_block_invalid"]["raw"].append(
        {"tag": "DEBATE_STATE", "path": "x", "reason": "invalid_json", "text": '<!-- DEBATE_STATE: {"a":1,} -->'}
    )
    report_service.validate_report_machine_blocks(report)


def test_裁定2_placeholder_has_no_json():
    report = _dual_report(_bad_json_response(), _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    placeholder = report["horizons"]["short"]["investment_debate_state"]["attempts"][0]["raw_response"]
    body = placeholder.split("<!-- MACHINE_BLOCK_INVALID: DEBATE_STATE -->")[0]
    assert "{" not in body
    assert "<!--" not in body


def test_裁定2_idempotent():
    report = _dual_report(_bad_json_response(), _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    snapshot = json.dumps(report, sort_keys=True, ensure_ascii=False)
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.quarantine_invalid_report_machine_blocks(report)
    assert json.dumps(report, sort_keys=True, ensure_ascii=False) == snapshot


def test_裁定2_no_invalid_blocks_is_a_noop():
    report = _dual_report(_valid_response(), _valid_response())
    before = json.dumps(report, sort_keys=True, ensure_ascii=False)
    result = report_service.quarantine_invalid_report_machine_blocks(report)
    assert json.dumps(result, sort_keys=True, ensure_ascii=False) == before
    assert "_dav1432" not in result


def test_裁定2_both_horizons_invalid():
    report = _dual_report(_bad_json_response(), _bad_json_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    for name in ("short", "medium"):
        assert _horizon_of(report, name)["trade_action"] == "NO_TRADE"
        assert _horizon_of(report, name)["analysis_status"] == "ABSTAIN"
    assert report["_dav1432"]["a4_excluded"] is False
    assert set(report["_dav1432"]["degraded_horizons"]) == {"short", "medium"}


def test_裁定4_shared_field_invalid_degrades_both():
    """共用字段非法 → 两档都不可执行，且 A4 统计里算失败。"""
    report = _dual_report(_valid_response(), _valid_response(),
                          shared_raw=_bad_json_response("RISK_STATE"))
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    for name in ("short", "medium"):
        assert _horizon_of(report, name)["trade_action"] == "NO_TRADE"
        assert _horizon_of(report, name)["analysis_status"] == "ABSTAIN"
    assert report["_dav1432"]["a4_excluded"] is True


def test_裁定2_root_level_block_attributed_by_content():
    """根级块按内容归属：claim-id 集合匹配到 medium 就只降 medium，short 档不受影响。"""
    # 只有根级块非法；两档切片自身的块都合法，才能单独观察归属结果。
    report = _dual_report(_valid_response(), _valid_response())
    report["investment_debate_state"] = _debate_state(
        _bad_json_response(),
        claims=[dict(_VALID_PAYLOAD["new_claims"][0], claim_id="MED-1")],
    )
    for container in (report["horizons"]["medium"], report["medium_term"]):
        container["investment_debate_state"]["claims"] = [
            dict(_VALID_PAYLOAD["new_claims"][0], claim_id="MED-1")
        ]
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)

    assert report["_dav1432"]["degraded_horizons"] == ["medium"]
    assert report["horizons"]["medium"]["trade_action"] == "NO_TRADE"
    assert report["medium_term"]["trade_action"] == "NO_TRADE"
    assert report["horizons"]["short"]["trade_action"] == "BUY"
    assert report["short_term"]["trade_action"] == "BUY"


def test_裁定2_single_horizon_save_shape_without_horizons():
    """另一种保存形状：只有 short_term/medium_term，没有 horizons。"""
    report = {
        "short_term": {
            "investment_debate_state": _debate_state(_bad_json_response()),
            "trade_action": "BUY",
        },
        "medium_term": {
            "investment_debate_state": _debate_state(_valid_response()),
            "trade_action": "SELL",
        },
    }
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)
    assert report["short_term"]["trade_action"] == "NO_TRADE"
    assert report["medium_term"]["trade_action"] == "SELL"


def test_裁定2_single_horizon_report_root_only():
    """单档报告：根级块就是这份报告的正文，必须降级且仍能落库。"""
    report = {
        "investment_debate_state": _debate_state(_bad_json_response()),
        "trade_action": "BUY",
        "analysis_status": "EXECUTABLE",
    }
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)
    assert report["trade_action"] == "NO_TRADE"
    assert report["analysis_status"] == "ABSTAIN"
    assert "machine_block_invalid" in report["reason_codes"]


@pytest.mark.parametrize(
    "case_id,bad",
    [
        ("truncated", "正文\n<!-- DEBATE_STATE: {\"new_claims\": []"),
        ("duplicated", "正文\n<!-- DEBATE_STATE: {} -->\n<!-- DEBATE_STATE: {} -->"),
        ("missing_colon", "正文\n<!-- DEBATE_STATE {\"new_claims\": []} -->"),
        ("not_object", "正文\n<!-- DEBATE_STATE: [1,2] -->"),
    ],
)
def test_裁定4_other_invalid_forms_same_treatment(case_id, bad):
    """卡面第 4 条：重复块/缺冒号/截断/非对象与非法 JSON 同一处理方式。"""
    report = _dual_report(bad, _valid_response())
    report_service.quarantine_invalid_report_machine_blocks(report)
    report_service.validate_report_machine_blocks(report)
    assert _horizon_of(report, "short")["trade_action"] == "NO_TRADE"
    assert _horizon_of(report, "medium")["trade_action"] == "SELL"


def test_A1_invalid_block_never_leaves_horizon_executable():
    """A1 硬约束：任何非法块都不得让该档成为可执行档。"""
    for bad in (_bad_json_response(), "正文\n<!-- DEBATE_STATE: {\"a\":1",):
        report = _dual_report(bad, _bad_json_response())
        report_service.quarantine_invalid_report_machine_blocks(report)
        report_service.validate_report_machine_blocks(report)
        for name in ("short", "medium"):
            assert _horizon_of(report, name)["analysis_status"] == "ABSTAIN"
            assert _horizon_of(report, name)["trade_action"] == "NO_TRADE"


def test_裁定3_top_level_decision_unchanged():
    """裁定 3：双档沿用现口径，顶层 decision 留空，不改。"""
    report = _dual_report(_bad_json_response(), _bad_json_response())
    assert report["decision"] is None
    report_service.quarantine_invalid_report_machine_blocks(report)
    assert report["decision"] is None
