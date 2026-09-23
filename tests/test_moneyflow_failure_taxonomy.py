"""DAV-1138 — moneyflow_dc / moneyflow_ths structured failure taxonomy.

Covers the gap audit contract:

* transport subclasses (proxy / connect-timeout / read-timeout / TLS /
  DNS / peer-close) must land as distinct, persisted categories instead of
  collapsing into a single ``transport_error`` bucket;
* retry-exhausted probes keep the last error plus attempt counts;
* ``no_rows`` (confirmed empty by the gateway) stays strictly separated
  from provider/transport failures;
* dual-source and single-source failure chains persist per-api failure
  records inside ``tushare_provider.tushare_failures`` and survive the
  collector → ``market_data_context`` → ``result_data`` boundary;
* the DAV-1097-style read-only stability probe covers both moneyflow APIs
  with a stable output schema.
"""

from __future__ import annotations

import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

import tradingagents.dataflows.providers.cn_akshare_provider as cn_akshare_provider
from tradingagents.dataflows.providers.cn_akshare_provider import (
    CnAkshareProvider,
    FundFlowText,
)


class _TushareResponse:
    def __init__(self, payload, *, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


def _tushare_payload(
    api_name: str,
    *,
    code=0,
    trade_date: str = "20260814",
    net_amount="12000",
    ts_code: str = "600519.SH",
    items=None,
):
    fields = ["ts_code", "trade_date", "net_amount"]
    if api_name == "moneyflow_ths":
        fields.append("net_d5_amount")
    if api_name == "moneyflow_dc":
        fields.extend(
            ["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]
        )
    else:
        fields.extend(["buy_sm_amount", "buy_md_amount", "buy_lg_amount"])
    values = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "net_amount": net_amount,
        "net_d5_amount": "60000",
        "buy_sm_amount": "100",
        "sell_sm_amount": "80",
        "buy_md_amount": "200",
        "sell_md_amount": "150",
        "buy_lg_amount": "300",
        "sell_lg_amount": "250",
        "buy_elg_amount": "400",
        "sell_elg_amount": "350",
    }
    if items is None:
        items = [[values[field] for field in fields]]
    return {"code": code, "data": {"fields": fields, "items": items}}


@pytest.fixture
def trading_day(monkeypatch, frozen_trade_date):
    monkeypatch.setattr(cn_akshare_provider, "is_cn_trading_day", lambda _date: True)
    return frozen_trade_date


# ── A. transport subclasses ─────────────────────────────────────────


@pytest.mark.parametrize(
    "exc, expected_category",
    [
        (requests.exceptions.ProxyError("HTTPSConnectionPool proxy refused"), "transport_proxy"),
        (requests.exceptions.ConnectTimeout("connect timed out"), "transport_connect_timeout"),
        (requests.exceptions.ReadTimeout("read timed out"), "transport_read_timeout"),
        (requests.exceptions.Timeout("generic timeout"), "transport_timeout"),
        (
            requests.exceptions.SSLError("EOF occurred in violation of protocol"),
            "transport_tls",
        ),
        (
            requests.exceptions.ConnectionError(
                "HTTPSConnectionPool: Failed to resolve 't.xiaodefa.top' "
                "(NameResolutionError: getaddrinfo failed)"
            ),
            "transport_dns",
        ),
        (
            requests.exceptions.ConnectionError(
                "Remote end closed connection without response "
                "(RemoteDisconnected)"
            ),
            "transport_peer_closed",
        ),
        (requests.exceptions.ConnectionError("connection failed"), "transport_error"),
    ],
)
def test_tushare_transport_subclasses_are_typed_and_persisted(
    monkeypatch, exc, expected_category
):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    with patch("requests.post", side_effect=exc) as mock_post:
        out, errors, meta = provider._fetch_tushare_fund_flow(
            "600519", "2026-08-14"
        )

    assert out is None
    assert meta["status"] == "unavailable"
    categories = [f["category"] for f in meta["tushare_failures"]]
    assert categories == [expected_category, expected_category]
    assert expected_category in meta["failure_categories"]
    # Both moneyflow apis must keep independent failure records.
    assert [f["api"] for f in meta["tushare_failures"]] == [
        "moneyflow_dc",
        "moneyflow_ths",
    ]
    # No token or URL material may leak into persisted error strings.
    for failure in meta["tushare_failures"]:
        assert "configured" not in failure["error"]
        assert "token" not in failure["error"].lower()


def test_retry_exhausted_preserves_attempt_count_and_last_error(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    with patch(
        "requests.post", side_effect=requests.exceptions.ReadTimeout("read timed out")
    ) as mock_post:
        out, errors, meta = provider._fetch_tushare_fund_flow(
            "600519", "2026-08-14"
        )

    assert out is None
    # MAX_ATTEMPTS=2 retryable transport: 2 calls per api, 4 total.
    assert mock_post.call_count == 4
    for failure in meta["tushare_failures"]:
        assert failure["category"] == "transport_read_timeout"
        assert failure["attempts"] == "2"
        assert failure["retry_exhausted"] == "true"
        assert "transport_read_timeout" in failure["error"]


def test_non_retryable_api_code_records_single_attempt(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    payload = _tushare_payload("moneyflow_dc", code=12345)
    with patch("requests.post", return_value=_TushareResponse(payload)) as mock_post:
        out, errors, meta = provider._fetch_tushare_fund_flow(
            "600519", "2026-08-14"
        )

    assert out is None
    assert mock_post.call_count == 2  # one attempt per api, no retry
    for failure in meta["tushare_failures"]:
        assert failure["category"] == "api_code"
        assert failure["attempts"] == "1"
        assert failure["retry_exhausted"] == "false"


# ── confirmed_empty vs provider failure ──────────────────────────────


def test_confirmed_empty_rows_stay_separate_from_provider_failure(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    payload = _tushare_payload("moneyflow_dc", items=[])
    with patch("requests.post", return_value=_TushareResponse(payload)):
        out, errors, meta = provider._fetch_tushare_fund_flow(
            "600519", "2026-08-14"
        )

    assert out is None
    categories = [f["category"] for f in meta["tushare_failures"]]
    assert categories == ["no_rows", "no_rows"]
    assert "no_rows" in meta["failure_categories"]
    assert "transport_timeout" not in meta["failure_categories"]


# ── dual / single source failure chains via the real collector path ──


def _all_other_sources_fail(provider, monkeypatch):
    ak = MagicMock()
    ak.stock_individual_fund_flow.side_effect = ConnectionError("EM down")
    provider._ak = lambda: ak
    monkeypatch.setattr(
        cn_akshare_provider.CnAkshareProvider,
        "_fetch_eastmoney_direct_fund_flow",
        lambda self, *a, **k: (None, "eastmoney_direct: transport_proxy"),
    )
    monkeypatch.setattr(
        cn_akshare_provider.CnAkshareProvider,
        "_fetch_sina_historical_fund_flow",
        lambda self, *a, **k: None,
    )


def test_dual_moneyflow_failure_persists_per_source_chain(trading_day, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    _all_other_sources_fail(provider, monkeypatch)
    with patch("requests.post", side_effect=requests.exceptions.ProxyError("proxy refused")):
        out = provider.get_individual_fund_flow("600519", curr_date="2026-08-14")

    meta = out.fund_flow_evidence_meta
    tushare = meta["tushare_provider"]
    assert [f["api"] for f in tushare["tushare_failures"]] == [
        "moneyflow_dc",
        "moneyflow_ths",
    ]
    assert all(
        f["category"] == "transport_proxy" for f in tushare["tushare_failures"]
    )
    # Aggregate stays fail-close: unavailable, direction blocked.
    assert meta["direction"] == "blocked"
    assert meta["direction_allowed"] is False
    assert meta["final_source"] == "unavailable"
    assert "transport" in meta["failure_categories"]


def test_single_source_failure_keeps_failed_source_metadata(
    trading_day, monkeypatch
):
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    ths_payload = _tushare_payload("moneyflow_ths")
    with patch(
        "requests.post",
        side_effect=[
            requests.exceptions.ProxyError("dc proxy down"),
            requests.exceptions.ProxyError("dc proxy down"),
            _TushareResponse(ths_payload),
        ],
    ):
        out, errors, meta = provider._fetch_tushare_fund_flow(
            "600519", "2026-08-14"
        )

    assert out is not None
    assert meta["status"] == "partial"
    assert len(meta["tushare_failures"]) == 1
    failure = meta["tushare_failures"][0]
    assert failure["api"] == "moneyflow_dc"
    assert failure["category"] == "transport_proxy"
    # Successful THS source still selected; the DC failure is not swallowed.
    sources = {record.get("source") for record in out.fund_flow_evidence}
    assert "tushare_ths_moneyflow_ths" in sources
    assert meta["selection"]["selected_source"] == "tushare_ths_moneyflow_ths"


def test_moneyflow_failure_chain_survives_collector_serialization(
    trading_day, monkeypatch
):
    """collector → market_data_context keeps the per-api failure records."""
    from tradingagents.graph import data_collector

    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()
    _all_other_sources_fail(provider, monkeypatch)
    with patch("requests.post", side_effect=requests.exceptions.ProxyError("proxy refused")):
        gap = provider.get_individual_fund_flow("600519", curr_date="2026-08-14")

    def fake_safe(tool, _payload):
        if tool is data_collector.get_individual_fund_flow:
            return gap
        return ""

    with patch.object(data_collector, "_safe", side_effect=fake_safe), patch.object(
        data_collector, "FETCH_ALL_TIMEOUT", 1
    ):
        result = data_collector._fetch_all("600519.SH", "2026-08-14")

    serialized = json.loads(
        json.dumps(result["market_data_context"], ensure_ascii=False)
    )
    fund_flow = serialized["fund_flow_evidence"]
    failures = fund_flow["tushare_provider"]["tushare_failures"]
    assert {f["api"] for f in failures} == {"moneyflow_dc", "moneyflow_ths"}
    assert all(f["category"] == "transport_proxy" for f in failures)
    assert all(f["attempts"] == "2" for f in failures)
    assert fund_flow["direction_allowed"] is False


# ── C. stability probe (offline schema) ─────────────────────────────

_PROBE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "probe_moneyflow_stability.py",
)


def _load_probe_module():
    spec = importlib.util.spec_from_file_location(
        "probe_moneyflow_stability", _PROBE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_moneyflow_probe_output_schema_and_dual_failure_count(monkeypatch):
    probe = _load_probe_module()
    monkeypatch.setenv("TUSHARE_TOKEN", "configured")
    provider = CnAkshareProvider()

    ths_payload = _tushare_payload("moneyflow_ths")
    # round1: dc proxy-fails twice (retry), ths ok; round2: both fail.
    side_effects = [
        requests.exceptions.ProxyError("dc down"),
        requests.exceptions.ProxyError("dc down"),
        _TushareResponse(ths_payload),
        requests.exceptions.ReadTimeout("dc timeout"),
        requests.exceptions.ReadTimeout("dc timeout"),
        requests.exceptions.ReadTimeout("ths timeout"),
        requests.exceptions.ReadTimeout("ths timeout"),
    ]
    with patch("requests.post", side_effect=side_effects):
        report = probe.run_probe(
            provider,
            [("600519", "2026-08-14")],
            rounds=2,
            now_fn=lambda: "2026-09-23T00:00:00+08:00",
        )

    assert report["probe"] == "moneyflow_stability"
    assert report["read_only"] is True
    assert report["rounds"] == 2
    assert len(report["records"]) == 4  # 2 rounds x 2 apis
    for record in report["records"]:
        assert record["symbol"] == "600519"
        assert record["trade_date"] == "2026-08-14"
        assert record["api"] in ("moneyflow_dc", "moneyflow_ths")
        assert isinstance(record["success"], bool)
        assert record["probe_time"] == "2026-09-23T00:00:00+08:00"
        assert "latency_ms" in record
        assert "attempts" in record
        if not record["success"]:
            assert record["failure_category"]
            assert record["error"]

    dc_records = [r for r in report["records"] if r["api"] == "moneyflow_dc"]
    assert [r["failure_category"] for r in dc_records] == [
        "transport_proxy",
        "transport_read_timeout",
    ]
    assert dc_records[1]["retry_exhausted"] is True

    summary = report["summary"]
    assert summary["moneyflow_dc"]["failures"] == 2
    assert summary["moneyflow_ths"]["failures"] == 1
    assert summary["moneyflow_ths"]["successes"] == 1
    assert summary["dual_source_failure_rounds"] == 1
    assert summary["single_source_failure_rounds"] == 1
    assert summary["transient_failure_categories"]["transport_proxy"] == 1
    assert summary["transient_failure_categories"]["transport_read_timeout"] == 2


def test_moneyflow_probe_without_token_reports_token_missing(monkeypatch):
    probe = _load_probe_module()
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    provider = CnAkshareProvider()
    with patch("requests.post") as mock_post:
        report = probe.run_probe(
            provider,
            [("600905", "2026-08-14")],
            rounds=1,
            now_fn=lambda: "2026-09-23T00:00:00+08:00",
        )
    assert mock_post.call_count == 0
    assert report["records"][0]["failure_category"] == "token_missing"
    assert report["records"][0]["success"] is False
