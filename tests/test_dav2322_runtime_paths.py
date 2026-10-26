"""DAV-2322: runtime paths follow the interpreter and deployed code tree.

All imports and subprocesses use temporary data, state and snapshot directories.
The legacy data path is rewritten even when testing the unfixed wrapper, so its
module-level chdir/makedirs cannot reach the operator's checkout.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[1]
DAILY = REPO / "work/daily-batch/run_daily.py"
WRAPPER = REPO / "work/ta-snapshot/run_snapshot.py"
LEGACY_ROOT = "/Users/davidliu/Documents/TradingAgents-AShare"
DEPLOY_ROOT = "/Users/davidliu/ta-work/dav-snap-8571"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _calendar(prefix):
    return (prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages/akshare/file_fold/calendar.json")


def _prepare_daily(tmp_path, monkeypatch):
    prefix = tmp_path / "interpreter"
    legacy = tmp_path / "legacy-data"
    legacy_cal = legacy / ".venv310/lib/python3.10/site-packages/akshare/file_fold/calendar.json"
    legacy_cal.parent.mkdir(parents=True)
    legacy_cal.write_text('["20261016"]')
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setenv("TA_DAILY_ROOT", str(legacy))
    monkeypatch.delenv("TA_DAILY_CALENDAR", raising=False)
    return prefix, legacy


def _dry_run(module, tmp_path, monkeypatch):
    state = tmp_path / "daily-state"
    state.mkdir()
    module.HERE = str(state)
    cli_root = tmp_path / "cli-data-without-a-venv"
    monkeypatch.setattr(sys, "argv", [
        str(DAILY), "--dry-run", "--date", "2026-10-15",
        "--root", str(cli_root), "--base", "http://127.0.0.1:1",
    ])
    assert module.main() == module.EXIT_OK
    assert module.ROOT == str(cli_root)
    assert module.DB == str(cli_root / "data/tradingagents.db")


def test_daily_calendar_follows_prefix_after_root_override(tmp_path, monkeypatch, capsys):
    prefix, _ = _prepare_daily(tmp_path, monkeypatch)
    calendar = _calendar(prefix)
    calendar.parent.mkdir(parents=True)
    calendar.write_text('["20261015"]')
    module = _load(DAILY, "dav2322_daily_prefix")
    assert Path(module.CAL) == calendar
    assert module.trading_days() == ["2026-10-15"]
    _dry_run(module, tmp_path, monkeypatch)
    assert "dry-run: date=2026-10-15 group=A" in capsys.readouterr().out
    assert Path(module.CAL) == calendar


def test_daily_explicit_calendar_survives_root_override(tmp_path, monkeypatch, capsys):
    _prepare_daily(tmp_path, monkeypatch)
    calendar = tmp_path / "fixture-calendar.json"
    calendar.write_text('["20261015"]')
    monkeypatch.setenv("TA_DAILY_CALENDAR", str(calendar))
    module = _load(DAILY, "dav2322_daily_explicit")
    assert Path(module.CAL) == calendar
    assert module.trading_days() == ["2026-10-15"]
    _dry_run(module, tmp_path, monkeypatch)
    assert "dry-run: date=2026-10-15 group=A" in capsys.readouterr().out
    assert Path(module.CAL) == calendar


@pytest.mark.parametrize("malformed", [False, True], ids=["missing", "malformed"])
def test_daily_calendar_errors_do_not_fall_back_to_data_root(tmp_path, monkeypatch, malformed):
    prefix, _ = _prepare_daily(tmp_path, monkeypatch)
    if malformed:
        calendar = _calendar(prefix)
        calendar.parent.mkdir(parents=True)
        calendar.write_text("invalid-json")
    module = _load(DAILY, "dav2322_daily_error")
    with pytest.raises(json.JSONDecodeError if malformed else FileNotFoundError):
        module.trading_days()


def _load_snapshot(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy-data"
    legacy.mkdir()
    deploy = tmp_path / "deployed-code"
    here = deploy / "work/ta-snapshot"
    here.mkdir(parents=True)
    output = tmp_path / "snapshots"
    source = WRAPPER.read_text()
    source = source.replace(LEGACY_ROOT, str(legacy)).replace(DEPLOY_ROOT, str(deploy))
    source = source.replace('os.path.expanduser("~/ta-snapshot")', repr(str(output)))
    wrapper = here / "run_snapshot.py"
    wrapper.write_text(source)
    monkeypatch.delenv("TA_SNAPSHOT_SOURCE_DB", raising=False)
    monkeypatch.delenv("TA_SNAPSHOT_BATCH_DIR", raising=False)
    monkeypatch.setenv("TA_SNAPSHOT_DB", str(tmp_path / "reader-current.db"))
    return wrapper, legacy, deploy, output


def _import_snapshot(path):
    cwd = os.getcwd()
    try:
        module = _load(path, "dav2322_snapshot")
        imported_cwd = Path.cwd()
    finally:
        os.chdir(cwd)
    return module, imported_cwd


def test_snapshot_code_and_interpreter_are_independent_of_data_root(tmp_path, monkeypatch):
    wrapper, legacy, deploy, _ = _load_snapshot(tmp_path, monkeypatch)
    module, imported_cwd = _import_snapshot(wrapper)
    assert module.PY == sys.executable
    assert Path(module.ROOT) == deploy
    assert imported_cwd == deploy
    assert Path(module.SCRIPT) == deploy / "scripts/ta_snapshot.py"
    assert Path(module.HERE) == wrapper.parent
    assert Path(module.DB) == legacy / "data/tradingagents.db"
    assert Path(module.BATCH_DIR) == legacy / "work/daily-batch"


def test_snapshot_passes_configured_data_to_its_own_script(tmp_path, monkeypatch):
    wrapper, _, deploy, output = _load_snapshot(tmp_path, monkeypatch)
    source_db = tmp_path / "external-data/live.db"
    batch = tmp_path / "external-batch"
    from datetime import date
    batch_date = date.today().isoformat()
    summary_dir = batch / batch_date
    summary_dir.mkdir(parents=True)
    (summary_dir / "summary.json").write_text("{}")
    monkeypatch.setenv("TA_SNAPSHOT_SOURCE_DB", str(source_db))
    monkeypatch.setenv("TA_SNAPSHOT_BATCH_DIR", str(batch))
    script = deploy / "scripts/ta_snapshot.py"
    script.parent.mkdir()
    script.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'skipped': 'already_exists', 'python': sys.executable, "
        "'cwd': os.getcwd(), 'argv': sys.argv[1:]}))\n"
    )
    module, _ = _import_snapshot(wrapper)
    assert Path(module.DB) == source_db
    assert Path(module.BATCH_DIR) == batch
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=120 * 2**30))
    monkeypatch.setenv("PYTHONPATH", "/nonexistent-host-shadow")
    assert module.main() == 0
    report = json.loads((wrapper.parent / "last_run.json").read_text())
    assert report["python"] == sys.executable
    assert Path(report["cwd"]) == deploy
    assert report["argv"] == ["--date", batch_date, "--out-dir", str(output), "--db", str(source_db)]
    assert (wrapper.parent / f"done-{batch_date}.ok").exists()
