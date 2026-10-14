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
        body.replace("{{RELEASE_DIR}}", "/Users/davidliu/ta-reports-abcdef12")
            .replace("{{RELEASE_SHA}}", "abcdef12"))
    assert "{{" not in rendered.read_text()
    r = subprocess.run(["plutil", "-lint", str(rendered)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------- #
# Wrapper end-to-end with a stub interpreter                                   #
# --------------------------------------------------------------------------- #

def _make_project(tmp_path, progress_rc=0, accept_rc=0, progress_body=None,
                  contains_dav2033=True):
    """A fake PROJECT_ROOT: scripts/ populated + a git repo whose HEAD either
    contains the pinned DAV-2033 commit (cac73667) or doesn't.

    The wrapper now requires ``git -C $PROJECT_ROOT merge-base --is-ancestor
    cac73667 HEAD`` to succeed, so the fixture grafts the real commit object
    (fetched from the candidate clone) underneath a fresh init commit when
    ``contains_dav2033`` is set, and leaves an unrelated single-commit history
    otherwise.
    """
    proj = tmp_path / ("ta-reports-abcdef12" if contains_dav2033
                       else "ta-reports-stale")
    (proj / "scripts").mkdir(parents=True)
    for name, rc in (("dav1507_exit_progress.py", progress_rc),
                     ("dav1950_exit_acceptance_report.py", accept_rc)):
        (proj / "scripts" / name).write_text("# placeholder\n")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    if contains_dav2033:
        # Fetch the real DAV-2033 commit into the fixture repo so merge-base
        # can find it as an ancestor of HEAD.
        fetch = subprocess.run(
            ["git", "-C", str(proj), "fetch", "-q", str(ROOT),
             "cac73667b65f44dfa7c7df1423a302a52f813b01"],
            capture_output=True, text=True)
        if fetch.returncode != 0:
            pytest.skip(f"cac73667 not fetchable from candidate clone: {fetch.stderr}")
        base = "FETCH_HEAD"
    else:
        # An empty unrelated history — merge-base finds no common ancestor.
        subprocess.run(["git", "-C", str(proj), "commit", "-qm", "init",
                        "--allow-empty"],
                       check=True, env=_git_env())
        base = None
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-qm", "scripts",
                    "--allow-empty"], check=True, env=_git_env())
    if base:
        # Re-root the just-created commit on top of cac73667 so that
        # merge-base --is-ancestor cac73667 HEAD succeeds.
        new_head = subprocess.run(
            ["git", "-C", str(proj), "commit-tree", "HEAD^{tree}", "-p",
             "cac73667b65f44dfa7c7df1423a302a52f813b01", "-m", "rerooted"],
            check=True, capture_output=True, text=True, env=_git_env()).stdout.strip()
        subprocess.run(["git", "-C", str(proj), "reset", "--hard", "-q",
                        new_head], check=True)
    return proj


def _git_env():
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    return env


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


def _env_for(tmp_path, stub, proj, retention_days="30", calendar=True,
             snapshot=True):
    """The full TA_* env for one wrapper run; calendar/snapshot can be removed
    by passing False to exercise the new hard-fail checks."""
    snap = tmp_path / "ta-snapshot" / "current.db"
    if snapshot:
        snap.parent.mkdir(parents=True, exist_ok=True)
        snap.write_bytes(b"SQLite format 3\x00")  # only existence is checked
    out_root = tmp_path / "ta-work" / "reports"
    args_log = tmp_path / "stub-args.log"
    cal = tmp_path / "calendar.json"
    if calendar:
        cal.write_text("[]")
    env = dict(os.environ)
    env.update({
        "TA_PYTHON_BIN": str(stub),
        "TA_PROJECT_ROOT": str(proj),
        "TA_SNAPSHOT_DB": str(snap),
        "TA_REPORTS_ROOT": str(out_root),
        "TA_RETENTION_DAYS": retention_days,
        "TA_CALENDAR_JSON": str(cal),
        "STUB_ARGS_LOG": str(args_log),
    })
    return env, out_root, args_log


def _run_wrapper(tmp_path, stub, progress_rc=0, accept_rc=0, retention_days="30",
                 calendar=True, project_root=None):
    proj = project_root or _make_project(tmp_path, progress_rc, accept_rc)
    env, out_root, args_log = _env_for(
        tmp_path, stub, proj, retention_days, calendar)
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
    # Both scripts received the snapshot db path AND the calendar arg.
    seen = args_log.read_text()
    assert seen.count("current.db") == 2, seen
    assert seen.count("--calendar-json") == 2, seen


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
        "TA_CALENDAR_JSON": str(tmp_path / "calendar.json"),
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

# --------------------------------------------------------------------------- #
# 总控 10-14 令: PROJECT_ROOT dedicated-checkout + calendar fail-closed         #
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_project_root_unset(tmp_path):
    """TA_PROJECT_ROOT has no default — unset must fail_alert, not fall back."""
    stub = _make_stub_python(tmp_path)
    env, out_root, args_log = _env_for(tmp_path, stub, _make_project(tmp_path))
    del env["TA_PROJECT_ROOT"]
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "TA_PROJECT_ROOT unset" in alerts[0].read_text()
    assert not args_log.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_project_root_is_main_checkout(tmp_path):
    """Pointing PROJECT_ROOT at the main checkout is explicitly forbidden."""
    stub = _make_stub_python(tmp_path)
    env, out_root, args_log = _env_for(
        tmp_path, stub, Path("/Users/davidliu/Documents/TradingAgents-AShare"))
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "main checkout" in alerts[0].read_text()
    assert not args_log.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_checkout_lacks_dav2033(tmp_path):
    """A PROJECT_ROOT whose history does not contain cac73667 must fail_alert."""
    stub = _make_stub_python(tmp_path)
    stale = _make_project(tmp_path, contains_dav2033=False)
    env, out_root, args_log = _env_for(tmp_path, stub, stale)
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "cac73667" in alerts[0].read_text()
    assert not args_log.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_calendar_missing(tmp_path):
    """Missing trading calendar is now hard-fail (10-14 令), no WARN passthrough."""
    stub = _make_stub_python(tmp_path)
    env, out_root, args_log = _env_for(
        tmp_path, stub, _make_project(tmp_path), calendar=False)
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "calendar" in alerts[0].read_text()
    assert not args_log.exists()


def _run_wrapper_env(tmp_path, env):
    return subprocess.run(["bash", str(WRAPPER)], env=env,
                          capture_output=True, text=True)
