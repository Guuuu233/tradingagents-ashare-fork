"""DAV-1412: durable stock names, source failover and code-only fallback."""
import json
import sys
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from api import main


@pytest.fixture(autouse=True)
def cold_map(monkeypatch, tmp_path):
    for key, value in {
        "_cn_stock_map": None, "_cn_stock_reverse_map": None,
        "_cn_stock_map_norm": None, "_cn_stock_map_norm_src": None,
        "_cn_stock_map_loaded_at": 0, "_cn_stock_map_last_failure_at": 0,
        "_cn_stock_map_refresh_inflight": False,
        "_cn_stock_map_refresh_event": threading.Event(),
    }.items():
        monkeypatch.setattr(main, key, value)
    monkeypatch.setenv("TA_STOCK_MAP_ARCHIVE_PATH", str(tmp_path / "stock-map.json"))


def archive_path():
    from pathlib import Path
    import os
    return Path(os.environ["TA_STOCK_MAP_ARCHIVE_PATH"])


def seed_archive(**overrides):
    payload = {
        "schema_version": 1, "saved_at": "2026-09-29T10:00:00+00:00",
        "stock_count": 1, "fund_count": 0,
        "name_to_code": {"贵州茅台": "600519.SH"},
    }
    payload.update(overrides)
    archive_path().write_text(json.dumps(payload), encoding="utf-8")
    return payload


def provider_stub():
    ak = MagicMock()
    ak.stock_info_a_code_name.side_effect = RuntimeError("exchange SSL failure")
    ak.fund_name_em.return_value = pd.DataFrame(columns=["基金代码", "基金简称"])
    return ak


def test_failed_sources_restore_archive_and_keep_short_retry(caplog):
    from api.services import stock_map_service as service
    seed_archive()
    ak = provider_stub()
    with patch.dict(sys.modules, {"akshare": ak}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("backup timeout")
    ), caplog.at_level("INFO"):
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH"}
        assert main._get_reverse_stock_map_cached_only() == {"600519.SH": "贵州茅台"}
        assert main._search_cn_stock_by_name("贵州茅台") == "600519.SH"
        assert main._cn_stock_map_loaded_at == 0
        assert main._cn_stock_map_last_failure_at > 0
        assert not main._stock_map_refresh_needed()
        assert "2026-09-29T10:00:00+00:00" in caplog.text
        assert "archive" in caplog.text.lower()
        main._cn_stock_map_last_failure_at -= main._STOCK_MAP_FAILURE_RETRY_INTERVAL + 1
        assert main._stock_map_refresh_needed()
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH"}
        assert ak.stock_info_a_code_name.call_count == 2


def test_failed_sources_without_archive_report_list_displays_code():
    from api.services import stock_map_service as service
    ak = provider_stub()
    with patch.dict(sys.modules, {"akshare": ak}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("backup timeout")
    ):
        assert main._load_cn_stock_map() == {}
        report = SimpleNamespace(
            id="fixture", user_id="fixture-user", symbol="600519.SH",
            trade_date="2026-09-29", status="completed", decision="HOLD",
            direction=None, confidence=None, target_price=None, stop_loss_price=None,
        )
        # Exercise the real list endpoint without touching any database.
        with patch.object(main.report_service, "count_reports", return_value=1), \
             patch.object(main.report_service, "get_reports_by_user", return_value=[report]), \
             patch.object(main.report_service, "load_post_gate_fragments", return_value={}):
            result = main.list_reports(symbol=None, skip=0, limit=100, db=None,
                                       current_user=SimpleNamespace(id="fixture-user"))
        assert result["reports"][0]["name"] == "600519.SH"
        assert not archive_path().exists()


def test_success_atomically_persists_names_and_restart_reads_them():
    from api.services import stock_map_service as service
    ak = provider_stub()
    ak.stock_info_a_code_name.side_effect = None
    ak.stock_info_a_code_name.return_value = pd.DataFrame([
        {"name": "贵州茅台", "code": "600519"},
        {"name": "京东方Ａ", "code": "000725"},
    ])
    with patch.dict(sys.modules, {"akshare": ak}):
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH", "京东方Ａ": "000725.SZ"}
    raw = json.loads(archive_path().read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["stock_count"] == 2
    assert raw["fund_count"] == 0
    assert datetime.fromisoformat(raw["saved_at"]).tzinfo is not None
    assert raw["name_to_code"] == {"贵州茅台": "600519.SH", "京东方Ａ": "000725.SZ"}
    assert list(archive_path().parent.iterdir()) == [archive_path()]
    main._cn_stock_map = main._cn_stock_reverse_map = None
    main._cn_stock_map_loaded_at = 0
    ak.stock_info_a_code_name.side_effect = RuntimeError("down")
    with patch.dict(sys.modules, {"akshare": ak}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("down")
    ):
        assert main._load_cn_stock_map() == raw["name_to_code"]
        assert main._search_cn_stock_by_name("京东方A") == "000725.SZ"


def test_backup_can_bootstrap_without_archive():
    from api.services import stock_map_service as service
    with patch.dict(sys.modules, {"akshare": provider_stub()}), patch.object(
        service, "fetch_sina_stock_names", return_value={"贵州茅台": "600519.SH", "纬达光电": "920001.BJ"}
    ):
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH", "纬达光电": "920001.BJ"}
    assert json.loads(archive_path().read_text())["stock_count"] == 2
    assert main._cn_stock_map_last_failure_at == 0


@pytest.mark.parametrize("bad", [
    {"schema_version": 2}, {"saved_at": "not-a-date"}, {"saved_at": "2026-09-29"},
    {"name_to_code": {}}, {"name_to_code": {"贵州茅台": "bad"}}, {"stock_count": 9},
])
def test_invalid_archive_is_rejected(bad):
    from api.services import stock_map_service as service
    seed_archive(**bad)
    with patch.dict(sys.modules, {"akshare": provider_stub()}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("down")
    ):
        assert main._load_cn_stock_map() == {}
        assert main._cn_stock_map_last_failure_at > 0


def test_archive_write_failure_does_not_discard_live_names(caplog):
    from api.services import stock_map_service as service
    with patch.dict(sys.modules, {"akshare": provider_stub()}), patch.object(
        service, "fetch_sina_stock_names", return_value={"贵州茅台": "600519.SH"}
    ), patch("os.replace", side_effect=PermissionError("read-only")), caplog.at_level("WARNING"):
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH"}
    assert "archive" in caplog.text.lower()
    assert not archive_path().exists()
    assert not list(archive_path().parent.iterdir())


def test_default_archive_path_survives_release_cwd(monkeypatch, tmp_path):
    from api.services import stock_map_service as service
    monkeypatch.delenv("TA_STOCK_MAP_ARCHIVE_PATH")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    for sha in ["old", "new"]:
        release = tmp_path / "releases" / sha
        release.mkdir(parents=True)
        monkeypatch.chdir(release)
        assert service.archive_path() == tmp_path / "state" / "tradingagents" / "stock-map.json"


def sina_fixture(monkeypatch, pages=None, error=None, count="3"):
    from api.services import stock_map_service as service
    monkeypatch.setattr(service, "_SINA_MIN_STOCK_COUNT", 3)
    monkeypatch.setattr(service, "_SINA_PAGE_SIZE", 2)
    rows = [
        {"symbol": "bj920001", "code": "920001", "name": "纬达光电"},
        {"symbol": "sh600519", "code": "600519", "name": "贵州茅台"},
        {"symbol": "sz000725", "code": "000725", "name": "京东方Ａ"},
    ]
    payloads = iter([count] + (pages if pages is not None else [rows[:2], rows[2:]]))
    def get(url, *, params, timeout):
        assert params["node"] == "hs_a"
        assert timeout == (5, 10)
        if error:
            raise error
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: next(payloads))
    session = SimpleNamespace(get=get, mount=lambda *a: None, trust_env=True)
    fake = MagicMock()
    fake.__enter__.return_value = session
    monkeypatch.setattr(requests, "Session", lambda: fake)
    return service


def test_sina_adapter_fetches_all_pages_and_preserves_exchange(monkeypatch):
    service = sina_fixture(monkeypatch)
    assert service.fetch_sina_stock_names() == {
        "纬达光电": "920001.BJ", "贵州茅台": "600519.SH", "京东方Ａ": "000725.SZ",
    }


@pytest.mark.parametrize("pages", [
    [[], []], [[{"code": "600519", "name": "贵州茅台"}], []],
    [[{"symbol": "sh600519", "code": "600519", "name": "贵州茅台"}] * 2, []],
])
def test_sina_adapter_rejects_partial_or_malformed_response(monkeypatch, pages):
    service = sina_fixture(monkeypatch, pages=pages)
    with pytest.raises(ValueError):
        service.fetch_sina_stock_names()


def test_sina_adapter_propagates_network_failure(monkeypatch):
    service = sina_fixture(monkeypatch, error=requests.Timeout("provider timed out"))
    with pytest.raises(requests.Timeout):
        service.fetch_sina_stock_names()


def test_failed_atomic_replace_preserves_last_good_archive():
    from api.services import stock_map_service as service
    seed_archive()
    previous = archive_path().read_bytes()
    with patch("os.replace", side_effect=PermissionError("read-only")):
        service.write_archive({"京东方Ａ": "000725.SZ"}, 1, 0)
    assert archive_path().read_bytes() == previous
    assert list(archive_path().parent.iterdir()) == [archive_path()]


def test_warm_names_survive_failed_refresh_even_if_archive_is_missing():
    from api.services import stock_map_service as service
    main._cn_stock_map = {"贵州茅台": "600519.SH"}
    main._cn_stock_reverse_map = {"600519.SH": "贵州茅台"}
    main._cn_stock_map_loaded_at = 1
    with patch.dict(sys.modules, {"akshare": provider_stub()}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("down")
    ):
        assert main._load_cn_stock_map() == {"贵州茅台": "600519.SH"}
        assert main._get_reverse_stock_map_cached_only() == {"600519.SH": "贵州茅台"}
        assert not main._stock_map_refresh_needed()


@pytest.mark.parametrize("configured", ["relative/stock-map.json", "/tmp/releases/old/stock-map.json"])
def test_archive_rejects_ephemeral_paths(monkeypatch, configured):
    from api.services import stock_map_service as service
    monkeypatch.setenv("TA_STOCK_MAP_ARCHIVE_PATH", configured)
    with pytest.raises(ValueError):
        service.archive_path()


def test_corrupt_archive_is_logged_and_code_fallback_remains_usable(caplog):
    from api.services import stock_map_service as service
    archive_path().write_text("{broken", encoding="utf-8")
    with patch.dict(sys.modules, {"akshare": provider_stub()}), patch.object(
        service, "fetch_sina_stock_names", side_effect=RuntimeError("down")
    ), caplog.at_level("WARNING"):
        assert main._load_cn_stock_map() == {}
    assert "Cannot read archive" in caplog.text
