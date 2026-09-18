"""DAV-1086: r0_net semantic mismatch (主力 vs 大单) must not fake-disperse.

Reproduces production report c21456dd: Eastmoney ``moneyflow_dc.net_amount``
is 今日主力净流入额 (超大单+大单), while Tonghuashun ``moneyflow_ths.buy_lg_amount``
is only 今日大单净流入额 (no 超大单 component). Mixing both into ``r0_net``
produced a 30.76% fake dispersion -> ABSTAIN. Plan B: THS buy_lg_amount is
kept as an independent ``lg_net`` field; ``r0_net`` only carries true 主力净额.
"""
from decimal import Decimal
from unittest.mock import patch

from tradingagents.dataflows.fund_flow_evidence import (
    build_consensus_evidence,
    select_fund_flow_source,
)
from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider


class _TushareResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def _tushare_payload(api_name: str, net_amount: str, buy_lg_amount: str):
    """Production c21456dd fixture values (万元)."""
    if api_name == "moneyflow_dc":
        fields = [
            "ts_code", "trade_date", "net_amount",
            "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
        ]
        values = {
            "ts_code": "000001.SZ",
            "trade_date": "20260918",
            "net_amount": net_amount,
            "buy_sm_amount": "100",
            "buy_md_amount": "200",
            "buy_lg_amount": buy_lg_amount,
            "buy_elg_amount": "300",
        }
    else:
        fields = [
            "ts_code", "trade_date", "net_amount", "net_d5_amount",
            "buy_sm_amount", "buy_md_amount", "buy_lg_amount",
        ]
        values = {
            "ts_code": "000001.SZ",
            "trade_date": "20260918",
            "net_amount": net_amount,
            "net_d5_amount": "56000",
            "buy_sm_amount": "100",
            "buy_md_amount": "200",
            "buy_lg_amount": buy_lg_amount,
        }
    return {
        "code": 0,
        "data": {"fields": fields, "items": [[values[f] for f in fields]]},
    }


def _fetch_c21456dd(monkeypatch):
    """DC net_amount=4018.5万 (主力), THS buy_lg_amount=7589.31万 (大单)."""
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    with patch(
        "requests.post",
        side_effect=[
            _TushareResponse(
                _tushare_payload("moneyflow_dc", net_amount="4018.5", buy_lg_amount="8748.16")
            ),
            _TushareResponse(
                _tushare_payload("moneyflow_ths", net_amount="12000", buy_lg_amount="7589.31")
            ),
        ],
    ):
        return provider._fetch_tushare_fund_flow("000001", "2026-09-18")


def _main_force_record(source: str, value: str, *, date: str = "2026-09-18") -> dict:
    return {
        "source": source,
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "000001",
        "date": date,
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": "r0_net",
        "value": value,
        "r0_net": value,
        "unit": "亿元",
        "field_semantics": {"r0_net": "主力净额（负值表示净流出）"},
    }


def test_ths_buy_lg_amount_is_lg_net_not_r0_net(monkeypatch):
    """方案B：THS buy_lg_amount 改独立 lg_net 字段，r0_net 仅承载主力净额。"""
    out, errors, _meta = _fetch_c21456dd(monkeypatch)
    assert errors == []
    assert out is not None

    ths_records = [
        r for r in out.fund_flow_evidence
        if r.get("source") == "tushare_ths_moneyflow_ths"
    ]
    # THS 大单记录必须带 lg_net，且绝不能再以 r0_net 混装主力口径。
    ths_lg = next(r for r in ths_records if r.get("upstream_field") == "buy_lg_amount")
    assert ths_lg["lg_net"] == "0.758931"
    assert "r0_net" not in ths_lg
    assert all("r0_net" not in r for r in ths_records)

    dc = next(
        r for r in out.fund_flow_evidence
        if r.get("source") == "tushare_eastmoney_moneyflow_dc"
    )
    assert dc["r0_net"] == "0.40185"
    # 东财 buy_lg_amount 以 lg_net 保留，供大单对大单同义比较。
    assert dc["lg_net"] == "0.874816"


def test_c21456dd_no_fake_unexplained_dispersion(monkeypatch):
    """修复后不再把 主力 vs 大单 当同字段比较 -> 无 30.76% 假离散。"""
    out, _errors, _meta = _fetch_c21456dd(monkeypatch)
    records = out.fund_flow_evidence
    audit = build_consensus_evidence(
        records, symbol="000001", requested_as_of="2026-09-18"
    )
    assert audit.get("reason_code") != "unexplained_dispersion"
    for field_name, result in (audit.get("field_results") or {}).items():
        assert result.get("reason_code") != "unexplained_dispersion", field_name

    # r0_net 只剩东财一家主力口径：无对端可比值 -> semantic_incomparable fail-closed。
    r0_result = (audit.get("field_results") or {}).get("r0_net") or {}
    daily = r0_result.get("daily_consensus") or {}
    assert any(
        day.get("semantic_incomparable") is True for day in daily.values()
    ), r0_result
    assert r0_result.get("direction_allowed") is not True

    # 大单对大单：0.874816 vs 0.758931，离散 ~7% < 20% -> lg_net 可形成共识。
    lg_result = (audit.get("field_results") or {}).get("lg_net") or {}
    assert lg_result.get("status") == "consensus"
    assert Decimal(lg_result["relative_dispersion"]) < Decimal("0.20")


def test_direction_gate_selects_dc_r0_net_not_blocked(monkeypatch):
    """select_fund_flow_source：DC 主力 r0_net 放行，THS lg_net 不构成假冲突。"""
    out, _errors, _meta = _fetch_c21456dd(monkeypatch)
    selection = select_fund_flow_source(
        out.fund_flow_evidence, symbol="000001", requested_as_of="2026-09-18"
    )
    assert selection["selected_field"] == "r0_net"
    assert selection["selected_source"] == "tushare_eastmoney_moneyflow_dc"
    assert selection["direction_allowed"] is True
    assert selection["reason_code"] != "incomparable_field_semantics"


def test_true_same_field_conflict_still_blocks():
    """真同字段冲突：两个主力口径源差 >20%，仍 unexplained_dispersion + blocked。"""
    # 两个不同 family 的主力口径源真冲突（同语义同字段差 >20%）。
    records = [
        _main_force_record("tushare_eastmoney_moneyflow_dc", "1.0"),
        _main_force_record("tushare_ths_moneyflow_ths", "2.0"),
    ]
    audit = build_consensus_evidence(
        records, symbol="000001", requested_as_of="2026-09-18"
    )
    assert audit["status"] == "data_conflict"
    r0_result = (audit.get("field_results") or {}).get("r0_net") or {}
    daily = r0_result.get("daily_consensus") or {}
    assert any(
        day.get("reason_code") == "unexplained_dispersion" for day in daily.values()
    )
    assert audit["direction_allowed"] is False


def test_single_main_force_source_marks_semantic_incomparable():
    """单源单口径：r0_net 无对端可比值 -> semantic_incomparable + fail-closed。"""
    records = [_main_force_record("tushare_eastmoney_moneyflow_dc", "0.40185")]
    audit = build_consensus_evidence(
        records, symbol="000001", requested_as_of="2026-09-18"
    )
    r0_result = (audit.get("field_results") or {}).get("r0_net") or {}
    daily = r0_result.get("daily_consensus") or {}
    assert any(
        day.get("semantic_incomparable") is True for day in daily.values()
    )
    assert audit["direction_allowed"] is False
    assert audit["status"] == "data_conflict"


def test_large_order_semantics_rejected_as_r0_net():
    """闸门收紧：声明为大单口径的字段不得再以 r0_net 通过方向选择。"""
    record = {
        "source": "tushare_ths_moneyflow_ths",
        "algorithm_group": "new_algorithm_group",
        "status": "available",
        "symbol": "000001",
        "date": "2026-09-18",
        "period_kind": "historical_daily",
        "time_window": "1d",
        "field": "r0_net",
        "value": "0.758931",
        "unit": "亿元",
        "field_semantics": {"r0_net": "今日大单净流入额（大单口径，不含超大单分量；万元）"},
    }
    result = select_fund_flow_source(
        [record], symbol="000001", requested_as_of="2026-09-18"
    )
    assert result["selected_source"] is None
    assert result["direction_allowed"] is False
    assert any(
        item.get("reason") == "field_semantics_or_value_invalid"
        for item in result["rejected_sources"]
    )
