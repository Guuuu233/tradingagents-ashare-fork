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
    # DAV-2056 review: the anchor must be wired explicitly, and must be the
    # same trunk-reachable SHA the wrapper defaults to.
    anchor = _dav2033_anchor()
    assert f"<key>TA_REQUIRED_DAV2033_COMMIT</key>\n        <string>{anchor}</string>" in text


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

DAV2033_ANCHOR_RE = re.compile(
    r'^REQUIRED_DAV2033_COMMIT="\$\{TA_REQUIRED_DAV2033_COMMIT:-([0-9a-f]{40})\}"$',
    re.M)
# 主干 ref（而非裸 SHA）用于 fixture fetch；裸 SHA 在浅克隆/非 origin 克隆里可能
# 不可达，那正是 DAV-2056 复审打回的静默 skip 根因。
TRUNK_REF = "refs/remotes/origin/codex/dav-4-p2a-trunk"


def _dav2033_anchor():
    """The wrapper's default DAV-2033 anchor, parsed from the wrapper itself so
    the tests can never drift from the shipped default."""
    m = DAV2033_ANCHOR_RE.search(WRAPPER.read_text())
    assert m, "REQUIRED_DAV2033_COMMIT default not found in wrapper"
    return m.group(1)


def test_dav2033_anchor_is_reachable_from_trunk():
    """The default anchor must be an ancestor of the trunk tip, otherwise the
    documented install steps produce a checkout the wrapper rejects (DAV-2056)."""
    anchor = _dav2033_anchor()
    ls = subprocess.run(
        ["git", "-C", str(ROOT), "branch", "-r", "--contains", anchor],
        capture_output=True, text=True)
    assert ls.returncode == 0, ls.stderr
    assert TRUNK_REF.removeprefix("refs/remotes/") in ls.stdout, (
        f"anchor {anchor} is not contained in {TRUNK_REF}; contained refs:\n"
        f"{ls.stdout or '(none)'}")


def _make_project(tmp_path, progress_rc=0, accept_rc=0, progress_body=None,
                  contains_dav2033=True):
    """A fake PROJECT_ROOT: scripts/ populated + a git repo whose HEAD either
    contains the pinned DAV-2033 commit or doesn't.

    The wrapper requires ``git -C $PROJECT_ROOT merge-base --is-ancestor
    <anchor> HEAD`` to succeed, so the fixture grafts the real commit object
    (fetched by trunk ref from the candidate clone) underneath a fresh init
    commit when ``contains_dav2033`` is set, and leaves an unrelated
    single-commit history otherwise.
    """
    anchor = _dav2033_anchor()
    proj = tmp_path / ("ta-reports-abcdef12" if contains_dav2033
                       else "ta-reports-stale")
    (proj / "scripts").mkdir(parents=True)
    for name, rc in (("dav1507_exit_progress.py", progress_rc),
                     ("dav1950_exit_acceptance_report.py", accept_rc)):
        (proj / "scripts" / name).write_text("# placeholder\n")
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    if contains_dav2033:
        # Fetch the trunk branch ref (not a bare SHA) so merge-base can find the
        # anchor as an ancestor. Failure here is a hard error: a silently skipped
        # fixture would let a broken anchor ship.
        fetch = subprocess.run(
            ["git", "-C", str(proj), "fetch", "-q", str(ROOT), TRUNK_REF],
            capture_output=True, text=True)
        if fetch.returncode != 0:
            pytest.fail(f"cannot fetch {TRUNK_REF} from {ROOT}: {fetch.stderr}")
        reach = subprocess.run(
            ["git", "-C", str(proj), "merge-base", "--is-ancestor", anchor,
             "FETCH_HEAD"], capture_output=True, text=True)
        if reach.returncode != 0:
            pytest.fail(
                f"DAV-2033 anchor {anchor} is not reachable from {TRUNK_REF} in "
                f"{ROOT} — the wrapper would reject every checkout produced by "
                "the documented install steps (DAV-2056 review finding)")
    else:
        # An empty unrelated history — merge-base finds no common ancestor.
        subprocess.run(["git", "-C", str(proj), "commit", "-qm", "init",
                        "--allow-empty"],
                       check=True, env=_git_env())
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(proj), "commit", "-qm", "scripts",
                    "--allow-empty"], check=True, env=_git_env())
    if contains_dav2033:
        # Re-root the just-created commit on top of the DAV-2033 anchor so that
        # merge-base --is-ancestor <anchor> HEAD succeeds.
        new_head = subprocess.run(
            ["git", "-C", str(proj), "commit-tree", "HEAD^{tree}", "-p",
             anchor, "-m", "rerooted"],
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
def test_wrapper_fails_closed_when_project_root_is_main_checkout_double_slash(tmp_path):
    """A `//`-prefixed path is the same directory; string compare alone let it through."""
    stub = _make_stub_python(tmp_path)
    env, out_root, args_log = _env_for(
        tmp_path, stub,
        Path("/Users/davidliu//Documents/TradingAgents-AShare"))
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert "main checkout" in alerts[0].read_text()
    assert not args_log.exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_wrapper_fails_closed_when_checkout_lacks_dav2033(tmp_path):
    """A PROJECT_ROOT whose history does not contain the DAV-2033 anchor."""
    stub = _make_stub_python(tmp_path)
    stale = _make_project(tmp_path, contains_dav2033=False)
    env, out_root, args_log = _env_for(tmp_path, stub, stale)
    r = _run_wrapper_env(tmp_path, env)
    assert r.returncode != 0
    day = out_root / date.today().isoformat()
    alerts = list(day.glob("ALERT-*.log"))
    assert alerts, f"no ALERT file; stderr={r.stderr!r}"
    assert _dav2033_anchor()[:7] in alerts[0].read_text()
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
