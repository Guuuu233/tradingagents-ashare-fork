"""DAV-1291: 模型数值抽取的日期/区间归属（F1 日期绑定 / F2 区间泛化 / F4 回归）。

fixture 句式全部取自真实被误替换报告的写法（见 DAV-1291 卡内样本）。
"""
from decimal import Decimal

from tradingagents.dataflows.fund_flow_evidence import (
    extract_model_daily_values,
    validate_model_summary,
)

_SOURCE = "eastmoney_direct"


def _record(value: str, date: str, *, field: str = "r0_net", source: str = _SOURCE) -> dict:
    semantics = (
        "主力净额（负值表示净流出）"
        if field == "r0_net"
        else "总净额（负值表示净流出）"
    )
    return {
        "source": source,
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "601398",
        "date": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": field,
        "value": value,
        "unit": "亿元",
        "field_semantics": {field: semantics},
    }


def _september_records() -> list[dict]:
    """近似 d32929c4/1db525e8 场景：as_of=2026-09-24，09-24 主力净流入 1.086 亿。"""
    return [
        _record("-0.19", "2026-09-18"),
        _record("0.30", "2026-09-21"),
        _record("0.40", "2026-09-22"),
        _record("-0.20", "2026-09-23"),
        _record("1.086", "2026-09-24"),
    ]


def _validate(records, text, *, window_days=1):
    return validate_model_summary(
        records,
        text,
        window_days=window_days,
        selected_field="r0_net",
        selected_source=_SOURCE,
        requested_as_of="2026-09-24",
    )


# ---------------------------------------------------------------------------
# F1 日期绑定
# ---------------------------------------------------------------------------

def test_dated_value_is_compared_to_its_own_date_not_as_of():
    """「9月18日主力净流出0.19亿」不得与 09-24 的 1.086 比对。"""
    val = _validate(_september_records(), "9月18日主力净流出0.19亿，短线情绪偏弱。")

    assert val["status"] != "mismatch"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []
    # 与 09-18 自身记录（-0.19）比对成功 → matched
    assert val["status"] == "matched"


def test_dated_value_without_record_is_unverifiable_not_mismatch():
    """records 没有该日期时记 unverifiable，不得记 mismatch。"""
    val = _validate(
        _september_records(),
        "9月15日主力净流出0.50亿，9月24日主力净流入1.09亿。",
    )

    assert val["status"] != "mismatch"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []
    assert "r0_net" in val["unverifiable_fields"]


def test_dated_value_different_from_same_date_record_is_mismatch():
    """带日期的数值若与同日结构化值不符，仍必须判 mismatch（F4）。"""
    val = _validate(_september_records(), "9月18日主力净流入0.50亿。")

    assert val["status"] == "mismatch"
    assert val["hard_guard"]["blocked"] is True
    assert val["mismatches"][0]["structured"] == "-0.19"


# ---------------------------------------------------------------------------
# F2 区间识别
# ---------------------------------------------------------------------------

def test_multi_day_cumulative_is_not_treated_as_single_day():
    """「近20日主力累计净流入1.72亿」不得当作 09-24 单日值比对。"""
    val = _validate(
        _september_records(),
        "近20日主力累计净流入1.72亿，中期资金持续布局。",
    )

    assert val["status"] != "mismatch"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []
    # 结构化没有 20 日窗口 → unverifiable
    assert "r0_net" in val["unverifiable_fields"]


def test_interval_value_compared_against_same_window_structured():
    """「近5日主力累计净流入1.396亿」与 5 日结构化累计比对 → matched。"""
    records = _september_records()
    expected = str(sum(Decimal(r["value"]) for r in records))
    val = _validate(records, f"近5日主力累计净流入{expected}亿。", window_days=5)

    assert val["status"] == "matched"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []


def test_interval_value_wrong_against_same_window_is_mismatch():
    """同窗口区间数值错误仍必须判 mismatch（F4）。"""
    val = _validate(_september_records(), "近5日主力累计净流入9.99亿。", window_days=5)

    assert val["status"] == "mismatch"
    assert val["hard_guard"]["blocked"] is True


def test_symbolic_interval_is_unverifiable_not_mismatch():
    """「近一月/本周以来」等无精确窗口 → unverifiable，不得记 mismatch。"""
    val = _validate(_september_records(), "近一月主力净流入3.5亿。")

    assert val["status"] != "mismatch"
    assert val["hard_guard"]["blocked"] is False
    assert "r0_net" in val["unverifiable_fields"]


# ---------------------------------------------------------------------------
# F4 回归：当日值比对语义不变
# ---------------------------------------------------------------------------

def test_same_day_value_within_tolerance_is_matched():
    """「9月24日主力净流入1.09亿」对结构化 1.086 → matched。"""
    val = _validate(_september_records(), "9月24日主力净流入1.09亿。")

    assert val["status"] == "matched"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []


def test_same_day_wrong_value_still_mismatch_and_blocked():
    """「9月24日主力净流入2.5亿」对 1.086 → mismatch，必须仍阻断。"""
    val = _validate(_september_records(), "9月24日主力净流入2.5亿。")

    assert val["status"] == "mismatch"
    assert val["hard_guard"]["blocked"] is True
    assert len(val["mismatches"]) == 1


def test_undated_single_day_value_compared_to_as_of():
    """无日期且确属单日表述的数值仍与 requested_as_of 当日值比对。"""
    val = _validate(_september_records(), "主力净流入1.09亿。")

    assert val["status"] == "matched"
    assert val["hard_guard"]["blocked"] is False


# ---------------------------------------------------------------------------
# extract_model_daily_values 的日期归属
# ---------------------------------------------------------------------------

def test_daily_extractor_drops_other_date_values_when_as_of_known():
    assert extract_model_daily_values(
        "9月18日主力净流出0.19亿", requested_as_of="2026-09-24"
    ) == {}
    assert extract_model_daily_values(
        "9月24日主力净流入1.09亿", requested_as_of="2026-09-24"
    ) == {"r0_net": "1.09"}


def test_daily_extractor_keeps_iso_date_for_backward_compat():
    """无 as_of 语境下保持旧行为（带日期仍返回，由 validate 做归属）。"""
    assert extract_model_daily_values("2026-09-24 主力净流入 1.09 亿") == {
        "r0_net": "1.09"
    }


# ---------------------------------------------------------------------------
# F3 被拦原文保留
# ---------------------------------------------------------------------------

def test_blocked_original_payload_preserves_draft_and_mismatch_detail():
    from tradingagents.agents.analysts.smart_money_analyst import (
        build_blocked_report_original,
    )

    draft = "中期主力资金持续流入，建议增持……"
    validation = {
        "status": "mismatch",
        "mismatches": [
            {
                "field": "r0_net",
                "structured": "1.086",
                "model": "2.5",
                "unit": "亿元",
                "reason": "model daily value differs from structured evidence",
                "kind": "daily",
            }
        ],
        "unverifiable_fields": ["netamount"],
        "hard_guard": {"blocked": True, "reason": "模型数值与结构化 evidence 不一致"},
    }
    payload = build_blocked_report_original(validation, {"status": "selected"}, draft)

    assert payload["schema"] == "smart_money_report_blocked_original.v1"
    assert payload["original_text"] == draft
    assert payload["validation_status"] == "mismatch"
    assert payload["mismatches"][0]["model"] == "2.5"
    assert payload["unverifiable_fields"] == ["netamount"]
    assert payload["guard_reason"]


# ---------------------------------------------------------------------------
# rework（总控裁决）：仅 unverifiable 不得误陈述为「存在出入」
# ---------------------------------------------------------------------------

def test_unverifiable_only_status_is_unverifiable_not_validation_warning():
    """只有无法核对的数值、没有任何偏差 → 新状态 unverifiable，不阻断。"""
    val = _validate(
        _september_records(),
        "9月24日主力净流入1.09亿；近20日主力累计净流入1.72亿，中期持续布局。",
    )

    assert val["status"] == "unverifiable"
    assert val["hard_guard"]["blocked"] is False
    assert val["mismatches"] == []
    assert "r0_net" in val["unverifiable_fields"]


def test_unverifiable_header_is_neutral_and_contains_no_deviation_words():
    from tradingagents.agents.analysts.smart_money_analyst import (
        validation_notice_header,
    )

    header = validation_notice_header({"status": "unverifiable"})

    assert header is not None
    assert "出入" not in header
    assert "偏差" not in header
    assert "无法与结构化数据逐日核对" in header


def test_rhetorical_deviation_keeps_original_warning_copy():
    """确有容差外近似偏差 → 仍为 validation_warning 且头部沿用原文案。"""
    from tradingagents.agents.analysts.smart_money_analyst import (
        validation_notice_header,
    )

    records = _september_records()[-1:]  # 09-24 r0_net = 1.086
    val = _validate(records, "今日主力资金净流入约1.20亿，温和介入。")

    assert val["status"] == "validation_warning"
    assert val["hard_guard"]["blocked"] is False
    header = validation_notice_header(val)
    assert header is not None
    assert "存在细微出入" in header


def test_matched_status_has_no_notice_header():
    from tradingagents.agents.analysts.smart_money_analyst import (
        validation_notice_header,
    )

    assert validation_notice_header({"status": "matched"}) is None
    assert validation_notice_header({"status": "mismatch"}) is None
    assert validation_notice_header(None) is None
