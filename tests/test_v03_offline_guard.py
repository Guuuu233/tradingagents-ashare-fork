"""DAV-1050: V-03 --offline 真离线取价护栏测试.

覆盖红队验收:
- RT-1: --offline 下 vendor/HTTP/baostock 入口若被调用即失败,测量仍正常结束。
- RT-2: 无本地快照时样本为 typed_missing,收益 NULL,coverage 分母守恒,原因可追溯。
- RT-3: 本地快照正常取价;越界日期/未来行/缺字段/冲突重复行全部 fail-closed。
- RT-4: 显式 forward_oos_end_date 覆盖默认上界;默认边界行为独立验证。
- RT-5: 在线模式仍走 VendorPriceDataProvider,离线开关不改变在线默认路径。
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.eval.v03_return_measure import (
    DEFAULT_FORWARD_OOS_END_DATE,
    MeasurementOutcomeStatus,
    OfflineSnapshotPriceDataProvider,
    OOSSegment,
    PoolFilterStatus,
    PriceSnapshotValidationError,
    SampleRole,
    V03ReturnMeasureEngine,
    VendorPriceDataProvider,
    classify_oos_segment,
)

FWD_END = "2026-09-18"

SNAP_TRADE_DATES = [
    "2026-09-09",
    "2026-09-10",
    "2026-09-11",
    "2026-09-14",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
]


def _bar(date: str, open_: float = 100.0, close: float = 105.0, symbol: str = "600519.SH"):
    return {
        "symbol": symbol,
        "date": date,
        "open": open_,
        "high": max(open_, close),
        "low": min(open_, close),
        "close": close,
        "volume": 1000000.0,
    }


def _write_snapshot(tmp_path: Path, payload: dict, name: str = "snap.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _full_snapshot(tmp_path: Path) -> Path:
    """Valid snapshot covering T=2026-09-10 -> T+1 entry 09-11 -> T+5 exit 09-18."""
    bars = [_bar(d, symbol="600519.SH") for d in SNAP_TRADE_DATES]
    bars += [
        {
            "symbol": "000300.SH",
            "date": d,
            "open": 4000.0,
            "high": 4100.0,
            "low": 3900.0,
            "close": 4050.0,
            "volume": 1.0,
        }
        for d in SNAP_TRADE_DATES
    ]
    return _write_snapshot(
        tmp_path,
        {
            "trade_dates": SNAP_TRADE_DATES,
            "bars": bars,
            "metadata": {"600519.SH": {"list_date": "2001-08-27", "name": "贵州茅台"}},
        },
    )


def _buy_report(report_id: str = "r_fwd", trade_date: str = "2026-09-10") -> dict:
    return {
        "id": report_id,
        "user_id": "429163f7-50b6-4982-8bdf-96ae99506843",
        "symbol": "600519.SH",
        "trade_date": trade_date,
        "status": "completed",
        "decision": "BUY",
        "direction": "看多",
    }


# ---------------------------------------------------------------------------
# RT-1: offline provider must not touch vendor/HTTP/baostock entry points
# ---------------------------------------------------------------------------


def test_rt1_offline_no_vendor_or_network_calls(monkeypatch, tmp_path):
    """--offline: 所有 vendor/HTTP/baostock/socket 入口被调用即失败,测量仍结束."""
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("external vendor/network entry was called in offline mode")

    # Vendor router
    import tradingagents.dataflows.interface as iface

    monkeypatch.setattr(iface, "route_to_vendor", _boom, raising=False)

    # akshare entry points used by VendorPriceDataProvider / trade calendar
    import akshare as ak

    monkeypatch.setattr(ak, "stock_zh_index_daily_tx", _boom, raising=False)
    monkeypatch.setattr(ak, "stock_info_sh_name_code", _boom, raising=False)
    monkeypatch.setattr(ak, "stock_info_sz_name_code", _boom, raising=False)
    monkeypatch.setattr(ak, "tool_trade_date_hist_sina", _boom, raising=False)

    # baostock session
    import tradingagents.dataflows.providers.cn_baostock_provider as bs_mod

    monkeypatch.setattr(bs_mod, "baostock_session", _boom, raising=False)

    # Raw outbound sockets (loopback healthz is skipped by CLI; here even sockets die)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom)

    provider = OfflineSnapshotPriceDataProvider(forward_oos_end_date=FWD_END)
    engine = V03ReturnMeasureEngine(
        price_provider=provider,
        hold_days=5,
        forward_oos_end_date=FWD_END,
    )
    res = engine.measure_dataset([_buy_report()])
    assert res.all_metrics.total_reports == 1

    # With a snapshot the measurement also completes fully offline.
    provider2 = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(_full_snapshot(tmp_path)),
        forward_oos_end_date=FWD_END,
    )
    engine2 = V03ReturnMeasureEngine(
        price_provider=provider2, hold_days=5, forward_oos_end_date=FWD_END
    )
    res2 = engine2.measure_dataset([_buy_report()])
    assert res2.all_metrics.evaluated_count == 1


# ---------------------------------------------------------------------------
# RT-2: no local snapshot -> typed_missing, return NULL, coverage conserved
# ---------------------------------------------------------------------------


def test_rt2_no_snapshot_forward_sample_is_typed_missing():
    provider = OfflineSnapshotPriceDataProvider(forward_oos_end_date=FWD_END)
    engine = V03ReturnMeasureEngine(
        price_provider=provider, hold_days=5, forward_oos_end_date=FWD_END
    )
    res = engine.measure_dataset([_buy_report()])
    rec = res.records[0]

    assert rec.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value
    assert rec.performance_category == "typed_missing"
    # Offline ST/listing metadata cannot be verified -> explicit provider gap reason
    assert rec.missing_reason == "offline_metadata_unavailable"
    assert rec.net_return is None
    assert rec.gross_return is None
    assert rec.included_in_return_metrics is False
    # Coverage denominator conservation: sample counted, not silently dropped
    assert res.forward_oos_metrics.total_reports == 1
    assert res.forward_oos_metrics.typed_missing_count == 1
    assert res.forward_oos_metrics.evaluated_count == 0
    assert res.forward_oos_metrics.mean_net_return is None
    # Provenance traces the provider gap
    assert rec.evidence_provenance["price_provider"] == "OfflineSnapshotPriceDataProvider"
    assert rec.evidence_provenance["price_snapshot_path"] is None


def test_rt2_snapshot_with_metadata_but_no_bars_gives_traceable_gap(tmp_path):
    snap = _write_snapshot(
        tmp_path,
        {
            "trade_dates": SNAP_TRADE_DATES,
            "bars": [
                {
                    "symbol": "000300.SH",
                    "date": d,
                    "open": 4000.0,
                    "high": 4100.0,
                    "low": 3900.0,
                    "close": 4050.0,
                    "volume": 1.0,
                }
                for d in SNAP_TRADE_DATES
            ],
            "metadata": {"600519.SH": {"list_date": "2001-08-27"}},
        },
    )
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(snap), forward_oos_end_date=FWD_END
    )
    engine = V03ReturnMeasureEngine(
        price_provider=provider, hold_days=5, forward_oos_end_date=FWD_END
    )
    res = engine.measure_dataset([_buy_report()])
    rec = res.records[0]
    assert rec.outcome_status == MeasurementOutcomeStatus.TYPED_MISSING.value
    assert rec.missing_reason is not None
    assert "snapshot_missing_symbol" in rec.missing_reason
    assert rec.net_return is None
    assert res.snapshot_manifest.price_snapshot_sha256 == provider.snapshot_sha256


# ---------------------------------------------------------------------------
# RT-3: snapshot path works; fail-closed validation on bad snapshots
# ---------------------------------------------------------------------------


def test_rt3_snapshot_t1_open_t5_close_evaluated(tmp_path):
    snap = _full_snapshot(tmp_path)
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(snap), forward_oos_end_date=FWD_END
    )
    engine = V03ReturnMeasureEngine(
        price_provider=provider, hold_days=5, forward_oos_end_date=FWD_END
    )
    res = engine.measure_dataset([_buy_report()])
    rec = res.records[0]

    assert rec.outcome_status == MeasurementOutcomeStatus.EVALUATED.value
    # T=09-10 -> entry T+1 = 09-11 open=100 -> exit T+5 = 09-18 close=105
    assert rec.entry_date == "2026-09-11"
    assert rec.exit_date == "2026-09-18"
    assert rec.entry_price == 100.0
    assert rec.exit_price == 105.0
    assert rec.gross_return is not None and rec.gross_return > 0
    assert rec.net_return is not None
    assert rec.benchmark_return is not None
    assert res.forward_oos_metrics.evaluated_count == 1
    # Snapshot hash recorded in manifest
    assert res.snapshot_manifest.price_snapshot_sha256 == provider.snapshot_sha256
    assert res.snapshot_manifest.price_provider == "OfflineSnapshotPriceDataProvider"


def test_rt3_snapshot_rejects_date_beyond_forward_oos_end(tmp_path):
    snap = _write_snapshot(
        tmp_path,
        {"bars": [_bar("2026-09-21", symbol="600519.SH")]},  # > 2026-09-18
    )
    with pytest.raises(PriceSnapshotValidationError):
        OfflineSnapshotPriceDataProvider(
            snapshot_path=str(snap), forward_oos_end_date=FWD_END
        )


def test_rt3_snapshot_rejects_missing_fields(tmp_path):
    bad = _write_snapshot(
        tmp_path,
        {"bars": [{"symbol": "600519.SH", "date": "2026-09-11", "open": 1.0}]},
    )
    with pytest.raises(PriceSnapshotValidationError):
        OfflineSnapshotPriceDataProvider(
            snapshot_path=str(bad), forward_oos_end_date=FWD_END
        )


def test_rt3_snapshot_rejects_conflicting_duplicates(tmp_path):
    dup = _write_snapshot(
        tmp_path,
        {
            "bars": [
                _bar("2026-09-11", open_=100.0),
                _bar("2026-09-11", open_=101.0),  # conflicting duplicate
            ]
        },
    )
    with pytest.raises(PriceSnapshotValidationError):
        OfflineSnapshotPriceDataProvider(
            snapshot_path=str(dup), forward_oos_end_date=FWD_END
        )


def test_rt3_snapshot_dedupes_identical_duplicates(tmp_path):
    dup = _write_snapshot(
        tmp_path,
        {"bars": [_bar("2026-09-11"), _bar("2026-09-11")]},
    )
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(dup), forward_oos_end_date=FWD_END
    )
    bar = provider.get_bar("600519.SH", "2026-09-11")
    assert bar is not None and bar.open == 100.0


def test_rt3_csv_snapshot_supported(tmp_path):
    csv_path = tmp_path / "snap.csv"
    csv_path.write_text(
        "symbol,date,open,high,low,close,volume\n"
        "600519.SH,2026-09-11,100,106,99,105,1000000\n"
        "600519.SH,2026-09-18,105,106,99,105.5,1000000\n",
        encoding="utf-8",
    )
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(csv_path), forward_oos_end_date=FWD_END
    )
    bar = provider.get_bar("600519.SH", "2026-09-18")
    assert bar is not None and bar.close == 105.5


def test_rt3_get_bar_beyond_forward_end_returns_none(tmp_path):
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(_full_snapshot(tmp_path)),
        forward_oos_end_date=FWD_END,
    )
    assert provider.get_bar("600519.SH", "2026-09-21") is None
    assert (
        provider.describe_price_gap("600519.SH", "2026-09-21")
        == "date_beyond_forward_oos_end"
    )


# ---------------------------------------------------------------------------
# RT-4: explicit --forward-oos-end-date overrides default upper bound
# ---------------------------------------------------------------------------


def test_rt4_explicit_forward_end_classifies_forward_oos():
    assert (
        classify_oos_segment(
            "2026-09-10",
            historical_cutoff="2026-09-08",
            forward_oos_end="2026-09-18",
        )
        == OOSSegment.FORWARD_OOS
    )
    assert (
        classify_oos_segment(
            "2026-09-17",
            historical_cutoff="2026-09-08",
            forward_oos_end="2026-09-18",
        )
        == OOSSegment.FORWARD_OOS
    )

    provider = OfflineSnapshotPriceDataProvider(forward_oos_end_date="2026-09-18")
    engine = V03ReturnMeasureEngine(
        price_provider=provider, hold_days=5, forward_oos_end_date="2026-09-18"
    )
    res = engine.measure_dataset(
        [_buy_report("r1", "2026-09-10"), _buy_report("r2", "2026-09-17")]
    )
    assert res.forward_oos_metrics.total_reports == 2
    for rec in res.records:
        assert rec.sample_role == SampleRole.FORWARD_OOS.value


def test_rt4_default_bound_boundary_unchanged():
    """默认上界 2026-09-09: >=09-09 为 FORWARD_OOS,>09-09 为 FUTURE_DATA."""
    assert (
        classify_oos_segment("2026-09-09", forward_oos_end=DEFAULT_FORWARD_OOS_END_DATE)
        == OOSSegment.FORWARD_OOS
    )
    assert (
        classify_oos_segment("2026-09-10", forward_oos_end=DEFAULT_FORWARD_OOS_END_DATE)
        == OOSSegment.FUTURE_DATA
    )


# ---------------------------------------------------------------------------
# RT-5: online default path unchanged
# ---------------------------------------------------------------------------


def test_rt5_online_mode_still_uses_vendor_provider():
    engine = V03ReturnMeasureEngine(forward_oos_end_date=FWD_END)
    assert isinstance(engine.price_provider, VendorPriceDataProvider)
    assert not getattr(engine.price_provider, "offline", False)

    # --offline equivalent construction uses the offline provider only
    offline_provider = OfflineSnapshotPriceDataProvider(forward_oos_end_date=FWD_END)
    assert offline_provider.offline is True
    assert not isinstance(offline_provider, VendorPriceDataProvider)


def test_rt5_cli_requires_offline_for_price_snapshot():
    """--price-snapshot without --offline must be rejected by argparse."""
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "run_v03_return_measure.py"),
            "--price-snapshot",
            "x.json",
            "--no-ablations",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "--price-snapshot requires --offline" in (proc.stderr + proc.stdout)


def test_rt5_pool_exclusion_st_still_excluded_offline(tmp_path):
    """离线快照若给出 ST 元数据,真实 ST 排除语义保持不变(不放宽)."""
    snap = _write_snapshot(
        tmp_path,
        {
            "trade_dates": SNAP_TRADE_DATES,
            "bars": [_bar(d, symbol="600519.SH") for d in SNAP_TRADE_DATES],
            "metadata": {
                "600519.SH": {"list_date": "2001-08-27", "st": True}
            },
        },
    )
    provider = OfflineSnapshotPriceDataProvider(
        snapshot_path=str(snap), forward_oos_end_date=FWD_END
    )
    engine = V03ReturnMeasureEngine(
        price_provider=provider, hold_days=5, forward_oos_end_date=FWD_END
    )
    res = engine.measure_dataset([_buy_report()])
    rec = res.records[0]
    assert rec.pool_status == PoolFilterStatus.EXCLUDED_ST.value
    assert rec.outcome_status == MeasurementOutcomeStatus.EXCLUDED_POOL.value
