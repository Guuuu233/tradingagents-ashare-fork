"""DAV-2284 — label timer credential path vs macOS TCC (TA_ENV_FILE).

The 2026-10-19 nightly run died because launchd-spawned /bin/bash is denied
*reads* under ``~/Documents``: ``grep`` returned 2 (EPERM), the wrapper read
that as "file is clean", ``source`` failed the same way, and the alert blamed
a missing TUSHARE_TOKEN (exit 13). These tests pin the four behaviours the
card asks for, without touching a real credential file, the real plist
installation, or ``~/Documents``:

* the credential path is TA_ENV_FILE-driven and must be absolute;
* ``grep`` rc >= 2 is its own fail-closed alert (18) and never falls through
  to ``source`` / "token missing";
* the copy hygiene (700 dir / 600 file) is enforced at install time by
  ``ta_secrets_install.sh --check`` while the wrapper only WARNs;
* the shipped plist carries an absolute, secret-free TA_ENV_FILE.

The wrapper is executed as a real subprocess with its fixed paths shadowed
into a sandbox (same technique as tests/test_dav1875_expiry_label_pipeline.py).
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "phase2" / "run_expiry_label_daily.sh"
PLIST = REPO / "scripts" / "phase2" / "com.davidliu.ta-p4-expiry-labels.plist"
INSTALLER = REPO / "scripts" / "phase2" / "ta_secrets_install.sh"

BASE_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

# Deliberately fake and obviously-shaped so a leak into stdout/stderr/alert is
# detectable: no real vendor token ever appears in a test file.
FAKE_TOKEN = "dav2284-fake-token-NOT-A-SECRET-0001"


def _stub_python(bin_dir: Path) -> Path:
    """Interpreter stub: the trading-day probe answers "non-trading day", so
    the wrapper takes its early ``exit 0`` skip AFTER the credential gate —
    every assertion below therefore isolates the gate itself. Every call is
    recorded to ``$STUB_PYTHON_CALLS`` when that variable is set."""
    stub = bin_dir / "python_stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ -n "${STUB_PYTHON_CALLS:-}" ]; then echo call >> "${STUB_PYTHON_CALLS}"; fi\n'
        'input="$(cat 2>/dev/null)"\n'
        'case "$input" in\n'
        '    *is_cn_trading_day*) echo "2026-10-14 False"; exit 0 ;;\n'
        "esac\n"
        "exit 0\n",
        "utf-8")
    stub.chmod(0o755)
    return stub


def _stub_grep(bin_dir: Path, rc: int) -> Path:
    """Stand-in for /usr/bin/grep that fails the way TCC fails: open() is
    denied, so grep exits 2 with an "Operation not permitted" line."""
    stub = bin_dir / "grep_stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "grep: ${!#}: Operation not permitted" >&2\n'
        f"exit {rc}\n",
        "utf-8")
    stub.chmod(0o755)
    return stub


def _run_wrapper(tmp_path: Path,
                 env_lines: list[str] | None = None,
                 *,
                 env_file: Path | None = None,
                 env_default: str | None = None,
                 grep_rc: int | None = None,
                 env_file_mode: int | None = None,
                 extra_env: dict[str, str] | None = None):
    """Run the REAL wrapper with every fixed path shadowed into a sandbox.

    ``env_file``        -> hard-code ``ENV_FILE="<path>"`` (no indirection)
    ``env_default``     -> keep ``ENV_FILE="${TA_ENV_FILE:-<default>}"`` so a
                           test can prove the override wins over the default
    ``grep_rc``         -> rewrite /usr/bin/grep to a stub with that exit code

    One of ``env_file`` / ``env_default`` may be given to control the path;
    otherwise the sandbox credential file written from ``env_lines`` is used,
    so no test can ever reach the real ~/Documents credential file.
    """
    sandbox = tmp_path / "wrapper_sandbox"
    bin_dir = sandbox / "bin"
    work_root = sandbox / "work"
    code_dir = sandbox / "code"
    for d in (bin_dir, work_root / "phase2-labels",
              work_root / "phase2-ledger",
              code_dir / "scripts" / "phase2"):
        d.mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger" / "forward_ledger.jsonl").write_text("", "utf-8")

    cred = sandbox / "ta.env"
    if env_lines is not None:
        cred.write_text("\n".join(env_lines) + "\n", "utf-8")
        if env_file_mode is not None:
            cred.chmod(env_file_mode)
    if env_file is None and env_default is None:
        env_file = cred

    src = WRAPPER.read_text("utf-8")
    src = re.sub(r'^PYTHON=.*$', f'PYTHON="{_stub_python(bin_dir)}"', src,
                 flags=re.M)
    if env_file is not None:
        src = re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{env_file}"', src, flags=re.M)
    elif env_default is not None:
        src = re.sub(r'^ENV_FILE=.*$',
                     'ENV_FILE="${TA_ENV_FILE:-%s}"' % env_default, src,
                     flags=re.M)
    src = re.sub(r'^CODE_DIR=.*$', f'CODE_DIR="{code_dir}"', src, flags=re.M)
    src = re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src, flags=re.M)
    if grep_rc is not None:
        src = src.replace("/usr/bin/grep", str(_stub_grep(bin_dir, grep_rc)))
    shutil.copy(REPO / "scripts" / "phase2" / "expiry_label_pipeline.py",
                code_dir / "scripts" / "phase2" / "expiry_label_pipeline.py")
    script = sandbox / "wrapper.sh"
    script.write_text(src, "utf-8")

    env = dict(BASE_ENV)
    env.update(extra_env or {})
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True,
                          text=True, timeout=60, env=env)
    out_dir = work_root / "phase2-labels"
    alert = out_dir / "ALERT.launchd"
    log = out_dir / "label_pipeline.log"
    return {
        "proc": proc,
        "rc": proc.returncode,
        "alert": alert.read_text("utf-8") if alert.exists() else "",
        "log": log.read_text("utf-8") if log.exists() else "",
        "sandbox": sandbox,
    }


# ---------------------------------------------------------------------------
# item 1/2 — TA_ENV_FILE drives the path, and must be absolute
# ---------------------------------------------------------------------------

def test_ta_env_file_override_wins_over_the_documents_default(tmp_path):
    """The whole point of the card: a plist-supplied TA_ENV_FILE outside
    ~/Documents is the file actually read. The default (a path that does not
    exist here) must NOT be what the gate opens — with the override absent the
    same setup exits 16, with it present it reaches the skip path (exit 0)."""
    cred = tmp_path / "outside" / "ta.env"
    cred.parent.mkdir()
    cred.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    cred.chmod(0o600)

    with_override = _run_wrapper(
        tmp_path / "a",
        env_default=str(tmp_path / "missing-in-documents" / ".env"),
        extra_env={"TA_ENV_FILE": str(cred)})
    assert with_override["rc"] == 0, (with_override["rc"],
                                      with_override["proc"].stderr,
                                      with_override["alert"])
    assert with_override["alert"] == ""
    assert "skip:" in with_override["log"], with_override["log"]

    without_override = _run_wrapper(
        tmp_path / "b",
        env_default=str(tmp_path / "missing-in-documents" / ".env"))
    assert without_override["rc"] == 16
    assert "missing or not readable" in without_override["alert"]


def test_relative_ta_env_file_is_rejected_before_any_read(tmp_path):
    """launchd does not expand `~`: the literal card spelling
    `TA_ENV_FILE=~/ta-secrets/ta.env` would silently resolve inside
    WorkingDirectory. Fail closed (16) instead."""
    cred = tmp_path / "ta.env"
    cred.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    res = _run_wrapper(tmp_path, env_default="/nonexistent/.env",
                       extra_env={"TA_ENV_FILE": "~/ta-secrets/ta.env"})
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "must be absolute" in res["alert"]
    # nothing was sourced, so the fallback probe never ran
    assert "TUSHARE_TOKEN missing" not in res["alert"]


# ---------------------------------------------------------------------------
# item 4 — grep rc >= 2 is an authorization fault, fail-closed
# ---------------------------------------------------------------------------

def test_read_denied_grep_rc2_alerts_exit_18_not_exit_13(tmp_path):
    """Reproduces the 10-19 TCC signature (grep EPERM) at the gate level: the
    wrapper must report "read denied" (18) and must NOT fall through to
    `source`, which previously produced the misleading exit 13."""
    res = _run_wrapper(tmp_path, [f"TUSHARE_TOKEN={FAKE_TOKEN}"], grep_rc=2)
    assert res["rc"] == 18, (res["rc"], res["proc"].stderr, res["alert"])
    assert "读取被拒" in res["alert"]
    assert "rc=2" in res["alert"]
    assert "Operation not permitted" in res["alert"]
    # the misreport this card exists to kill
    assert "TUSHARE_TOKEN missing" not in res["alert"]
    # and the failure is in the log too, not only in the alert file
    assert "grep could not read" in res["log"]
    # the token never leaked into any visible artefact
    assert FAKE_TOKEN not in res["alert"]
    assert FAKE_TOKEN not in res["log"]


def test_grep_rc2_short_circuits_before_the_ledger_or_pipeline(tmp_path):
    """fail-closed means: no run, no gap rows, no interpreter invocation at
    all. The stub interpreter records every call; a denied credential read
    must leave that record empty."""
    calls = tmp_path / "python-calls.txt"
    res = _run_wrapper(tmp_path, [f"TUSHARE_TOKEN={FAKE_TOKEN}"], grep_rc=2,
                       extra_env={"STUB_PYTHON_CALLS": str(calls)})
    assert res["rc"] == 18
    assert not calls.exists(), calls.read_text("utf-8")
    assert "skip:" not in res["log"]
    assert "timer done" not in res["log"]


def test_malformed_env_is_still_exit_16_after_the_rc_rewrite(tmp_path):
    """The rc>=2 branch must not swallow the pre-existing "bad line" verdict."""
    res = _run_wrapper(tmp_path, ["EVIL=harmless touch /tmp/PWNED_dav2284"])
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "non KEY=VALUE lines" in res["alert"]


# ---------------------------------------------------------------------------
# item 3 — 600/700: strict at install time, WARN at run time
# ---------------------------------------------------------------------------

def test_wrapper_warns_but_does_not_stop_on_a_loose_mode(tmp_path):
    """A bare `cp` lands 644. That must be visible in the log (so ops can fix
    it) but must not stop the 20:10 label run — the strict gate lives in
    ta_secrets_install.sh --check. Modes only: never contents."""
    loose = _run_wrapper(tmp_path / "loose", [f"TUSHARE_TOKEN={FAKE_TOKEN}"],
                         env_file_mode=0o644)
    assert loose["rc"] == 0, (loose["rc"], loose["alert"])
    assert "WARN credential file mode 644 (want 600)" in loose["log"]
    assert FAKE_TOKEN not in loose["log"]

    tight = _run_wrapper(tmp_path / "tight", [f"TUSHARE_TOKEN={FAKE_TOKEN}"],
                         env_file_mode=0o600)
    assert tight["rc"] == 0
    assert "WARN credential file mode" not in tight["log"]


def _run_installer(tmp_path: Path, *args: str, home: Path | None = None):
    env = dict(BASE_ENV)
    env["HOME"] = str(home or tmp_path)
    return subprocess.run(["/bin/bash", str(INSTALLER), *args],
                          capture_output=True, text=True, timeout=60, env=env)


def test_installer_creates_700_dir_and_600_file_and_check_gates_on_drift(tmp_path):
    src = tmp_path / "src" / ".env"
    src.parent.mkdir()
    src.write_text(f"# comment\nTUSHARE_TOKEN={FAKE_TOKEN}\n"
                   "FUYAO_API_KEY=fake-fuyao\n", "utf-8")
    src.chmod(0o644)
    dest = tmp_path / "ta-secrets" / "ta.env"

    inst = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert inst.returncode == 0, (inst.returncode, inst.stdout, inst.stderr)
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.parent.stat().st_mode) == 0o700
    assert dest.read_bytes() == src.read_bytes()
    # values never printed, key names are
    assert FAKE_TOKEN not in inst.stdout + inst.stderr
    assert "TUSHARE_TOKEN: present" in inst.stdout

    ok = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest),
                        "--check")
    assert ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr)

    dest.chmod(0o644)
    drift = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest),
                           "--check")
    assert drift.returncode == 6, (drift.returncode, drift.stdout)
    assert "want 600" in drift.stdout
    assert FAKE_TOKEN not in drift.stdout + drift.stderr
    # --check must never rewrite the copy
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644


def test_installer_refuses_a_destination_inside_documents(tmp_path):
    """The one thing this installer must never do: put the copy back behind
    the TCC wall it exists to escape."""
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    src = tmp_path / "src.env"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    dest = home / "Documents" / "TradingAgents-AShare" / "ta.env"

    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest),
                         home=home)
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert "outside it" in res.stderr
    assert not dest.exists()


def test_installer_refuses_a_source_without_a_real_token(tmp_path):
    src = tmp_path / "src.env"
    src.write_text("FUYAO_API_KEY=only-a-fallback\n", "utf-8")
    dest = tmp_path / "ta-secrets" / "ta.env"
    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# item 2 — the shipped plist
# ---------------------------------------------------------------------------

def test_plist_declares_an_absolute_secret_free_ta_env_file():
    with PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["ProgramArguments"][0] == "/bin/bash"
    env = plist["EnvironmentVariables"]
    assert env["TA_ENV_FILE"] == "/Users/davidliu/ta-secrets/ta.env"
    assert env["TA_ENV_FILE"].startswith("/"), "launchd does not expand ~"
    assert "~" not in env["TA_ENV_FILE"]
    # no credential material of any kind in the plist — key NAMES may appear
    # in the explanatory comment, assignments may not appear at all
    blob = PLIST.read_text("utf-8")
    for marker in ("TUSHARE_TOKEN=", "FUYAO_API_KEY=", "INVESTODAY_API_KEY="):
        assert marker not in blob, f"{marker} must not appear in the plist"
    # every EnvironmentVariables value stays a plain one-line path/flag
    for key, value in env.items():
        assert "\n" not in value and len(value) < 200, key


def test_wrapper_documents_the_new_exit_code_and_env_override():
    """Cheap contract check: the header must keep advertising exit 18 and the
    TA_ENV_FILE indirection, so a future edit cannot silently drop either."""
    src = WRAPPER.read_text("utf-8")
    assert 'ENV_FILE="${TA_ENV_FILE:-' in src
    assert "18 credential file READ denied" in src
    assert "GREP_RC" in src
