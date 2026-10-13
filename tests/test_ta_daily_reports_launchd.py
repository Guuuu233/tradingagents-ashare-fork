"""DAV-2035 — launchd template + wrapper for the 20:30 daily reports.

Scope: template files only (plist lint, wrapper shell syntax, and a full
end-to-end wrapper run driven by a **stub interpreter** — no production db,
no model, no provider, nothing installed into launchd).

Covers:
  * plist parses, has the 20:30 Mon–Fri calendar, no KeepAlive, no {{}} after
    the documented single substitution;
  * ``bash -n`` on the wrapper;
  * wrapper happy path: both report JSONs land in ``<root>/YYYY-MM-DD/``,
    a run-status file is written, exit 0;
  * wrapper fail-closed: a failing upstream script writes an ``ALERT-*.log``
    and exits non-zero (never silent);
  * retention: date-shaped directories older than the retention window are
    pruned, non-date directories (``_logs``) survive.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "releases" / "launchd" / "ta-daily-reports-wrapper.sh"
PLIST = ROOT / "releases" / "launchd" / "com.davidliu.ta-daily-reports.plist"


def test_wrapper_is_valid_bash():
    assert WRAPPER.is_file()
    r = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_plist_is_valid_and_scheduled_2030_weekdays():
    assert PLIST.is_file()
    r = subprocess.run(["plutil", "-lint", str(PLIST)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    text = PLIST.read_text()
    assert "<key>Hour</key><integer>20</integer>" in text
    assert "<key>Minute</key><integer>30</integer>" in text
    for wd in range(1, 6):
        assert f"<key>Weekday</key><integer>{wd}</integer>" in text
    # No respawn: a failed run must write ALERT, not be restarted.
    assert "<key>KeepAlive</key>" not in text
    assert "<key>RunAtLoad</key>\n    <false/>" in text
    # Placeholders documented for install-time substitution.
    assert "{{RELEASE_DIR}}" in text


def test_plist_placeholder_substitution_lints(tmp_path):
    rendered = tmp_path / "com.davidliu.ta-daily-reports.plist"
    # Only the machine-readable part matters; strip the install comment first
    # (it legitimately quotes the grep that verifies substitution is complete).
    body = re.sub(r"<!--.*?-->", "", PLIST.read_text(), flags=re.S)
    rendered.write_text(
        body.replace("{{RELEASE_DIR}}", "/Users/davidliu/Documents/TradingAgents-AShare"))
    assert "{{" not in rendered.read_text()
    r = subprocess.run(["plutil", "-lint", str(rendered)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# Wrapper end-to-end with a stub interpreter                                   #
# --------------------------------------------------------------------------- #

def _make_project(tmp_path, progress_rc=0, accept_rc=0, progress_body=None):
    """A fake PROJECT_ROOT whose two scripts are driven by the stub python."""
    proj = tmp_path / "project"
    (proj / "scripts").mkdir(parents=True)
    for name, rc in (("dav1507_exit_progress.py", progress_rc),
                     ("dav1950_exit_acceptance_report.py", accept_rc)):
        (proj / "scripts" / name).write_text("# placeholder\n")
    return proj


def _make_stub_python(tmp_path, progress_rc=0, accept_rc=0):
    """A fake interpreter: dispatch on the script name, honour the exit codes."""
    stub = tmp_path / "stub-python"
    # NOTE: no `case` on the script name here — the macOS bash 3.2 shipped with
    # this image mis-parses `*name.py)` case arms under some locales; plain
    # if/elif string matching is equivalent and portable.
    lines = [
        "#!/usr/bin/env bash",
        'prog="${1:-}"; shift || true',
        'args="$*"',
        'if [[ "${prog}" == *dav1507_exit_progress.py ]]; then',
        '  echo "${prog} ${args}" >> "${STUB_ARGS_LOG}"',
        '  [ %d -eq 0 ] && echo \'{"script":"progress","ok":true}\'' % progress_rc,
        '  exit %d' % progress_rc,
        'elif [[ "${prog}" == *dav1950_exit_acceptance_report.py ]]; then',
        '  echo "${prog} ${args}" >> "${STUB_ARGS_LOG}"',
        '  [ %d -eq 0 ] && echo \'{"script":"acceptance","ok":true}\'' % accept_rc,
        '  exit %d' % accept_rc,
        'fi',
        'echo "stub: unexpected program ${prog}" >&2; exit 64',
    ]
    stub.write_text("\n".join(lines) + "\n")
    stub.chmod(0o755)
    return stub


def _run_wrapper(tmp_path, stub, progress_rc=0, accept_rc=0, retention_days="30"):
    proj = _make_project(tmp_path, progress_rc, accept_rc)
    snap = tmp_path / "ta-snapshot" / "current.db"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(b"SQLite format 3\x00")  # only existence is checked
    out_root = tmp_path / "ta-work" / "reports"
    args_log = tmp_path / "stub-args.log"
    env = dict(os.environ)
    env.update({
        "TA_PYTHON_BIN": str(stub),
        "TA_PROJECT_ROOT": str(proj),
        "TA_SNAPSHOT_DB": str(snap),
        "TA_REPORTS_ROOT": str(out_root),
        "TA_RETENTION_DAYS": retention_days,
        "TA_CALENDAR_JSON": str(tmp_path / "no-calendar.json"),
        "STUB_ARGS_LOG": str(args_log),
    })
    return subprocess.run(["bash", str(WRAPPER)], env=env,
                          capture_output=True, text=True), out_root, args_log


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_happy_path_writes_both_reports(tmp_path):
    stub = _make_stub_python(tmp_path)
    r, out_root, args_log = _run_wrapper(tmp_path, stub)
    assert r.returncode == 0, r.stdout + r.stderr
    day = out_root / date.today().isoformat()
    progress = day / f"exit-progress-{date.today().isoformat()}.json"
    accept = day / f"acceptance-{date.today().isoformat()}.json"
    assert progress.is_file() and accept.is_file()
    assert json.loads(progress.read_text())["script"] == "progress"
    assert json.loads(accept.read_text())["script"] == "acceptance"
    assert list(day.glob("run-status-*.txt")), "no run-status marker on success"
    assert not list(day.glob("ALERT-*.log")), "alert file written on success path"
    # Both scripts received the snapshot db path.
    seen = args_log.read_text()
    assert seen.count("current.db") == 2, seen


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_prunes_old_date_dirs_but_keeps_others(tmp_path):
    stub = _make_stub_python(tmp_path)
    out_root = tmp_path / "ta-work" / "reports"
    old = out_root / (date.today() - timedelta(days=40)).isoformat()
    recent = out_root / (date.today() - timedelta(days=3)).isoformat()
    logs = out_root / "_logs"
    for d in (old, recent, logs):
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.txt").write_text("x")
    r, _, _ = _run_wrapper(tmp_path, stub, retention_days="30")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not old.exists(), "retention did not prune a 40-day-old dir"
    assert recent.exists() and logs.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_with_alert_on_upstream_failure(tmp_path):
    stub = _make_stub_python(tmp_path, progress_rc=2)
    r, out_root, _ = _run_wrapper(tmp_path, stub, progress_rc=2)
    assert r.returncode != 0, "upstream failure must not exit 0"
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file written; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "dav1507_exit_progress.py" in alerts[0].read_text()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_snapshot_missing(tmp_path):
    stub = _make_stub_python(tmp_path)
    env = dict(os.environ)
    env.update({
        "TA_PYTHON_BIN": str(stub),
        "TA_PROJECT_ROOT": str(_make_project(tmp_path)),
        "TA_SNAPSHOT_DB": str(tmp_path / "nope" / "current.db"),
        "TA_REPORTS_ROOT": str(tmp_path / "ta-work" / "reports"),
        "TA_CALENDAR_JSON": str(tmp_path / "no-calendar.json"),
        "STUB_ARGS_LOG": str(tmp_path / "stub-args.log"),
    })
    r = subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    day = Path(env["TA_REPORTS_ROOT"]) / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "snapshot db not found" in alerts[0].read_text()
    # The stub interpreter must never have been invoked.
    assert not (tmp_path / "stub-args.log").exists()