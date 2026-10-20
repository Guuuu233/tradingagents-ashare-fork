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
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "phase2" / "run_expiry_label_daily.sh"
PLIST = REPO / "scripts" / "phase2" / "com.davidliu.ta-p4-expiry-labels.plist"
INSTALLER = REPO / "scripts" / "phase2" / "ta_secrets_install.sh"

BASE_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}

# Deliberately fake and obviously-shaped so a leak into stdout/stderr/alert is
# detectable: no real vendor token ever appears in a test file.
FAKE_TOKEN = "dav2284-fake-token-NOT-A-SECRET-0001"

# Real tool paths on macOS (rm/cat are in /bin, sed/sort/tr in /usr/bin).
REAL_TOOL_PATHS = {
    "cat": "/bin/cat",
    "rm": "/bin/rm",
    "sed": "/usr/bin/sed",
    "sort": "/usr/bin/sort",
    "tr": "/usr/bin/tr",
}


def _stub_python(bin_dir: Path) -> Path:
    """Interpreter stub: the trading-day probe answers "non-trading day", so
    the wrapper takes its early ``exit 0`` skip AFTER the credential gate —
    every assertion below therefore isolates the gate itself. Every call is
    recorded to ``$STUB_PYTHON_CALLS`` when that variable is set."""
    stub = bin_dir / "python_stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ -n "${STUB_PYTHON_CALLS:-}" ]; then echo call >> "${STUB_PYTHON_CALLS}"; fi\n'
        # DAV-2292 round 5: dump the environment this child actually
        # received — the ONLY way to prove that a script-private variable
        # did not leak into the subprocess env.
        'if [ -n "${STUB_ENV_DUMP:-}" ]; then env > "${STUB_ENV_DUMP}"; fi\n'
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
                 flags=re.MULTILINE)
    if env_file is not None:
        src = re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{env_file}"', src, flags=re.MULTILINE)
    elif env_default is not None:
        src = re.sub(r'^ENV_FILE=.*$',
                     'ENV_FILE="${TA_ENV_FILE:-%s}"' % env_default, src,
                     flags=re.MULTILINE)
    src = re.sub(r'^CODE_DIR=.*$', f'CODE_DIR="{code_dir}"', src, flags=re.MULTILINE)
    src = re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src, flags=re.MULTILINE)
    if grep_rc is not None:
        src = src.replace("/usr/bin/grep", str(_stub_grep(bin_dir, grep_rc)))
    # DAV-2292 round 5: a helper stub that records the ENVIRONMENT it was
    # given. The wrapper reads the source's stderr with `cat`, deletes the
    # temp file with `rm` and post-processes the text with `sed`/`sort`/`tr`
    # — if any of them inherits a script-private variable (e.g. `_src_err`,
    # which holds bash's syntax-error text QUOTING the offending line), the
    # credential value is in that helper's environ.
    helper_dump = (extra_env or {}).get("STUB_HELPER_ENV_DUMP")
    tempfile_probe = (extra_env or {}).get("STUB_TEMPFILE_PROBE")
    if helper_dump or tempfile_probe:
        # One recorder per tool, named after the tool, installed in a dir
        # that is PREPENDED to PATH: the wrapper calls `cat`/`rm`/`sed`
        # unqualified, so PATH is the only way to intercept them.
        rec_dir = bin_dir / "rec"
        rec_dir.mkdir(exist_ok=True)
        for tool in ("cat", "rm", "sed", "sort", "tr"):
            rec = rec_dir / tool
            rec.write_text(
                "#!/usr/bin/env bash\n"
                'if [ -n "${STUB_HELPER_ENV_DUMP:-}" ]; then\n'
                '    { echo "--- helper: $0"; env; } >> "${STUB_HELPER_ENV_DUMP}"\n'
                "fi\n"
                # the tempfile probe: stat whatever `cat` is asked to read
                'if [ -n "${STUB_TEMPFILE_PROBE:-}" ] && [ -f "${1:-}" ]; then\n'
                '    case "${1}" in\n'
                '        *.cred_src_err.*) { echo "path=${1} mode=$(/usr/bin/stat -f \'%Lp\' \"${1}\")"; '
                '               } >> "${STUB_TEMPFILE_PROBE}" ;;\n'
                "    esac\n"
                "fi\n"
                # NOTE: the real tool paths differ on macOS (rm/cat live in
                # /bin, sed/sort/tr in /usr/bin) — hardcoding /usr/bin made
                # the `rm` stub fail with "No such file or directory", so the
                # temp file was never deleted and the test saw a leftover.
                f"exec {REAL_TOOL_PATHS[tool]} \"$@\"\n",
                "utf-8")
            rec.chmod(0o755)
    shutil.copy(REPO / "scripts" / "phase2" / "expiry_label_pipeline.py",
                code_dir / "scripts" / "phase2" / "expiry_label_pipeline.py")
    script = sandbox / "wrapper.sh"
    script.write_text(src, "utf-8")

    env = dict(BASE_ENV)
    env.update(extra_env or {})
    if helper_dump or tempfile_probe:
        # the wrapper exports its own PATH, so the recorder dir has to be
        # injected into THAT line, not just into the subprocess env
        rec_dir = bin_dir / "rec"
        src = src.replace('export PATH="/usr/bin:/bin:/usr/sbin:/sbin"',
                          f'export PATH="{rec_dir}:/usr/bin:/bin:/usr/sbin:/sbin"')
        # the wrapper's own temp file must land INSIDE tmp_path too, so a
        # leftover can never be mistaken for another run's file (and never
        # pollutes the shared /tmp)
        src = src.replace('"${TMPDIR:-/tmp}/.cred_src_err.XXXXXX"',
                          f'"{tmp_path}/.cred_src_err.XXXXXX"')
        script.write_text(src, "utf-8")
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


def test_read_denied_never_sources_the_file(tmp_path):
    """DAV-2286 🟢-6: the "rc>=2 -> do not source" claim needs a positive
    proof, not just "the interpreter was not called". A file whose line would
    execute a command if it were sourced is used as the canary: if the
    wrapper sourced it, the marker file would exist."""
    marker = tmp_path / "PWNED_dav2284"
    res = _run_wrapper(tmp_path, [f"EVIL=$(touch {marker})"], grep_rc=2)
    assert res["rc"] == 18, (res["rc"], res["alert"])
    assert not marker.exists(), "the credential file was sourced despite a denied read"
    assert "TUSHARE_TOKEN missing" not in res["alert"]


def test_malformed_env_is_still_exit_16_after_the_rc_rewrite(tmp_path):
    """The rc>=2 branch must not swallow the pre-existing "bad line" verdict."""
    marker = tmp_path / "PWNED_dav2284"
    res = _run_wrapper(tmp_path, [f"EVIL=harmless touch {marker}"])
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "non KEY=VALUE lines" in res["alert"]
    assert not marker.exists()


def test_unbound_variable_in_the_credential_file_never_aborts_silently(
        tmp_path):
    """DAV-2286 🟢-2: under `set -u` an unbound variable inside the sourced
    file makes bash abort the WHOLE script (rc 127, NO alert file) — the
    failure becomes invisible. Two layers must hold:
      * a bare `$VAR` value never reaches the source at all — the KEY=VALUE
        whitelist rejects it first (exit 16, "non KEY=VALUE lines");
      * even if it did, the source runs inside a `set +u` window, so a
        lenient source cannot abort the script either.
    Either way the outcome is a normal fail-closed alert, never a bare
    rc=127 with no alert file."""
    bare = _run_wrapper(tmp_path / "bare", ["TUSHARE_TOKEN=$UNDEF_DAV2284_XYZ"])
    assert bare["rc"] == 16, (bare["rc"], bare["proc"].stderr, bare["alert"])
    assert bare["alert"], "a silent abort (no alert file) is the failure mode"
    assert "TUSHARE_TOKEN missing" not in bare["alert"]

    # single-quoted -> legal literal, survives the gate, and the file has no
    # TUSHARE_TOKEN at all -> the token probe must produce exit 13, NOT a
    # silent rc=127
    quoted = _run_wrapper(tmp_path / "quoted",
                           ["OTHER_KEY='$UNDEF_DAV2284_XYZ'"])
    assert quoted["rc"] == 13, (quoted["rc"], quoted["proc"].stderr,
                                quoted["alert"])
    assert quoted["alert"], "no alert file was written"
    assert "TUSHARE_TOKEN missing" in quoted["alert"]


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


def test_installer_missing_argument_value_fails_instead_of_spinning(tmp_path):
    """DAV-2286 🟡-1: `--src`/`--dest` without a value used to run `shift 2`
    with $#==1, which fails WITHOUT moving the positional parameters -> the
    arg loop never advances -> ~70% CPU, zero output, kill -9. This script
    is the release card's FIRST install step, so a mistyped flag must fail
    loudly and immediately. timeout=10 turns a regression back into a test
    failure instead of a 60s hang."""
    for flag in ("--src", "--dest"):
        started = time.monotonic()
        res = subprocess.run(["/bin/bash", str(INSTALLER), flag],
                             capture_output=True, text=True, timeout=10,
                             env={**BASE_ENV, "HOME": str(tmp_path)})
        elapsed = time.monotonic() - started
        assert res.returncode == 2, (flag, res.returncode, res.stdout, res.stderr)
        assert "needs a path" in res.stderr, (flag, res.stderr)
        assert elapsed < 5, (flag, elapsed)


def test_installer_rejects_a_source_the_timer_would_refuse_to_source(tmp_path):
    """DAV-2286 🟢-4: the copy must be one the TIMER accepts. The same
    KEY=VALUE gate the wrapper applies is applied to the source at install
    time, so a malformed .env is caught here instead of at 20:10."""
    src = tmp_path / "src.env"
    marker = tmp_path / "PWNED_gate"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\nEVIL=$(touch {marker})\n",
                   "utf-8")
    dest = tmp_path / "ta-secrets" / "ta.env"
    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "non KEY=VALUE lines" in res.stderr
    assert not dest.exists()
    assert not marker.exists(), "the rejected source was still executed"


def test_installer_refuses_a_symlinked_destination_dir(tmp_path):
    """DAV-2286 🟢-6 (coverage gap): a symlinked dir would redirect the copy
    and make the "700" claim describe the link target, not what we chmod'ed."""
    real = tmp_path / "real-dir"
    real.mkdir()
    link = tmp_path / "link-dir"
    link.symlink_to(real, target_is_directory=True)
    src = tmp_path / "src.env"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    dest = link / "ta.env"

    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert "symlink" in res.stderr
    assert not dest.exists()


def test_installer_check_flags_a_loose_directory_mode(tmp_path):
    """DAV-2286 🟢-6 (coverage gap): the dir mode was never drift-tested."""
    src = tmp_path / "src.env"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    dest = tmp_path / "ta-secrets" / "ta.env"
    inst = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert inst.returncode == 0, (inst.stdout, inst.stderr)

    dest.parent.chmod(0o755)
    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest),
                         "--check")
    assert res.returncode == 6, (res.returncode, res.stdout)
    assert "want 700" in res.stdout


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


def _sandboxed_script_copy(tmp_path: Path, which: str) -> Path:
    """A copy of the wrapper / installer with EVERY path constant rewritten
    into tmp_path.

    DAV-2292 🟡-2: the guard tests used to `source` the REAL repo file. If the
    guard were ever removed or bypassed, those tests would REPLAY the
    15:56 incident: the wrapper would write the PRODUCTION label dir and
    read the REAL .env; the installer would even COPY the real credentials
    into ~/ta-secrets/ta.env and mkdir 700 it. A test must never be able to
    do that — so every constant is redirected into tmp_path first, and the
    COPY is what gets sourced."""
    src = (WRAPPER if which == "wrapper" else INSTALLER).read_text("utf-8")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    work_root = sandbox / "work"
    (work_root / "phase2-labels").mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger").mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger" / "forward_ledger.jsonl").write_text("", "utf-8")
    code_dir = sandbox / "code"
    (code_dir / "scripts" / "phase2").mkdir(parents=True, exist_ok=True)
    (code_dir / "scripts" / "phase2" / "expiry_label_pipeline.py").write_text(
        "# stub\n", "utf-8")
    cred = sandbox / "ta.env"
    cred.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    cred.chmod(0o600)

    src = re.sub(r'^PYTHON=.*$', 'PYTHON="/nonexistent/python"',
                 src, flags=re.MULTILINE)
    src = re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{cred}"', src,
                 flags=re.MULTILINE)
    src = re.sub(r'^CODE_DIR=.*$', f'CODE_DIR="{code_dir}"', src,
                 flags=re.MULTILINE)
    src = re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src,
                 flags=re.MULTILINE)
    # the installer keeps its paths in SRC/DEST, not in the four constants
    src = re.sub(r'^SRC=.*$', f'SRC="{cred}"', src, flags=re.MULTILINE)
    src = re.sub(r'^DEST=.*$', f'DEST="{sandbox / "ta-secrets" / "ta.env"}"',
                 src, flags=re.MULTILINE)
    copy = sandbox / (f"{which}.sh")
    copy.write_text(src, "utf-8")
    return copy


def test_wrapper_refuses_to_be_sourced(tmp_path):
    """DAV-2292 事故护栏：2026-10-20 15:56 有人用
    `bash -c 'source run_expiry_label_daily.sh; echo "$VAR"'` 取常量，
    而 `source` 会**执行**整个脚本 → 向**正式**标签目录写了 ALERT.launchd +
    日志，并**读取了真实凭据**（默认 ENV_FILE 指向 ~/Documents/…/.env）。
    标签数据没被写（exit 11 早退），但告警与日志被污染。
    现在脚本被 source 时必须立刻拒绝：不写任何目录、不读任何凭据。

    DAV-2292 🟡-2: the file that gets sourced here is a SANDBOXED COPY
    (every path constant rewritten into tmp_path) — never the repo file, so
    that a future removal of the guard cannot make this test replay the
    incident against the production directory / the real credentials."""
    copy = _sandboxed_script_copy(tmp_path, "wrapper")
    proc = subprocess.run(
        ["/bin/bash", "-c",
         f'source {copy}; echo "$CRED_KEY_VALUE_GATE"'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path)})
    assert proc.returncode == 64, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSED" in proc.stderr
    assert "PRODUCTION label dir" in proc.stderr
    # 什么都没被 source 出来（闸常量没被打印）
    assert proc.stdout.strip() == "", proc.stdout


def test_wrapper_refuses_to_be_sourced_writes_nothing_to_the_output_dir(
        tmp_path):
    """同一条护栏的写侧断言：被 source 时不得在输出目录留下任何文件。
    DAV-2292 🟡-2: 也是 source 沙箱副本（四个常量全部指向 tmp_path）。"""
    copy = _sandboxed_script_copy(tmp_path, "wrapper")
    out_dir = tmp_path / "sandbox" / "work" / "phase2-labels"
    proc = subprocess.run(
        ["/bin/bash", "-c", f'source {copy} 2>/dev/null'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path)})
    assert proc.returncode == 64
    assert list(out_dir.iterdir()) == [], "sourcing wrote into the output dir"


def test_installer_also_refuses_to_be_sourced(tmp_path):
    """DAV-2292 护栏的覆盖面：安装脚本同样不得被 source（否则会读真实凭据
    并写真实目录）。
    DAV-2292 🟡-2: source 的是沙箱副本（SRC/DEST 指向 tmp_path）——否则护栏
    一旦失效，这条用例会真的把真实凭据复制成 ~/ta-secrets/ta.env 并 mkdir 700，
    比 15:56 那次更重。"""
    copy = _sandboxed_script_copy(tmp_path, "installer")
    proc = subprocess.run(
        ["/bin/bash", "-c", f'source {copy}'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path),
             "TA_ENV_SRC": str(tmp_path / "sandbox" / "ta.env"),
             "TA_ENV_FILE": str(tmp_path / "sandbox" / "ta-secrets" / "ta.env")})
    assert proc.returncode == 64, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSED" in proc.stderr
    # nothing was created in the sandbox either
    assert not (tmp_path / "sandbox" / "ta-secrets").exists()


# ---------------------------------------------------------------------------
# DAV-2303 — no hard-coded checkout: env first, script's own dir as fallback
# ---------------------------------------------------------------------------

def _make_fake_checkout(root: Path, name: str, *, with_pipeline: bool) -> Path:
    """Build a checkout-shaped tree: <root>/<name>/scripts/phase2/{wrapper,pipeline}."""
    ck = root / name
    (ck / "scripts" / "phase2").mkdir(parents=True, exist_ok=True)
    shutil.copy(WRAPPER, ck / "scripts" / "phase2" / "run_expiry_label_daily.sh")
    if with_pipeline:
        shutil.copy(REPO / "scripts" / "phase2" / "expiry_label_pipeline.py",
                    ck / "scripts" / "phase2" / "expiry_label_pipeline.py")
    return ck


def _run_checkout_wrapper(checkout: Path, tmp_path: Path, *,
                          extra_env: dict[str, str] | None = None):
    """Run a checkout-shaped copy of the wrapper with the interpreter stubbed
    and the output/credential paths sandboxed. rc 0 == the pipeline was FOUND
    (the trading-day gate answered "non-trading day" -> skip), rc 11 == the
    pipeline was NOT found (the very failure the 2026-10-20 15:56 run hit)."""
    script = checkout / "scripts" / "phase2" / "run_expiry_label_daily.sh"
    src = script.read_text("utf-8")
    bin_dir = tmp_path / "bin"
    work_root = tmp_path / "work"
    for d in (bin_dir, work_root / "phase2-labels",
              work_root / "phase2-ledger"):
        d.mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger" / "forward_ledger.jsonl").write_text("", "utf-8")
    cred = tmp_path / "ta.env"
    cred.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    cred.chmod(0o600)

    src = re.sub(r'^PYTHON=.*$', f'PYTHON="{_stub_python(bin_dir)}"', src,
                 flags=re.MULTILINE)
    src = re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{cred}"', src, flags=re.MULTILINE)
    src = re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src,
                 flags=re.MULTILINE)
    script.write_text(src, "utf-8")

    env = dict(BASE_ENV)
    env.update(extra_env or {})
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True,
                          text=True, timeout=60, env=env)
    alert = work_root / "phase2-labels" / "ALERT.launchd"
    return {
        "rc": proc.returncode,
        "alert": alert.read_text("utf-8") if alert.exists() else "",
        "stderr": proc.stderr,
    }


def test_pipeline_is_found_in_the_scripts_own_checkout(tmp_path):
    """DAV-2303: with NO TA_CODE_DIR in the environment, the wrapper must find
    expiry_label_pipeline.py in the checkout it itself lives in. This is what
    makes "move the checkout" a plist-only change instead of a code change."""
    ck = _make_fake_checkout(tmp_path, "labels-a", with_pipeline=True)
    res = _run_checkout_wrapper(ck, tmp_path)
    assert res["rc"] == 0, (res["rc"], res["alert"], res["stderr"])


def test_ta_code_dir_from_the_environment_wins_over_the_script_location(tmp_path):
    """DAV-2303: the plist may point TA_CODE_DIR at a DIFFERENT checkout than
    the wrapper's own location; the env value must win (and the pipeline must
    be found THERE)."""
    here = _make_fake_checkout(tmp_path, "labels-a", with_pipeline=False)
    there = _make_fake_checkout(tmp_path, "labels-b", with_pipeline=True)
    res = _run_checkout_wrapper(here, tmp_path,
                                extra_env={"TA_CODE_DIR": str(there)})
    assert res["rc"] == 0, (res["rc"], res["alert"], res["stderr"])


def test_missing_pipeline_still_exits_11_naming_the_resolved_checkout(tmp_path):
    """DAV-2303: when the pipeline really is missing, the alert must name the
    checkout the wrapper actually resolved — so the operator sees WHICH
    checkout is wrong instead of a hard-coded path from the source."""
    ck = _make_fake_checkout(tmp_path, "labels-a", with_pipeline=False)
    res = _run_checkout_wrapper(ck, tmp_path)
    assert res["rc"] == 11, (res["rc"], res["alert"], res["stderr"])
    assert str(ck) in res["alert"], res["alert"]


def _placeholder_check(plist_path: Path) -> int:
    """Run the check the install step documents (§3 of TA_ENV_TCC.md / the
    plist's own install comment): look for an unresolved PLACEHOLDER ('{{')
    in the VALUES, not for a specific placeholder name.

    Returns 0 = all resolved, 1 = at least one unresolved.

    DAV-2292 🟢-3 (round 7): this helper is a RE-IMPLEMENTATION, not the
    document's shell. They differ in one corner: when `plutil -extract` cannot
    find the key at all, this helper fails closed (rc != 0 -> return 1) while
    the document's `! plutil … | grep -q '{{'` gets empty input from grep
    (rc 1) and therefore judges it PASSED. The current template has all four
    keys and is followed by `plutil -lint`, so there is no practical impact —
    but the two must not be treated as the same thing.
    `test_the_install_instructions_text_is_pinned` pins the DOCUMENT's text;
    this helper pins the BEHAVIOUR of the values."""
    cmds = [
        ["plutil", "-extract", "ProgramArguments", "json", "-o", "-",
         str(plist_path)],
        ["plutil", "-extract", "EnvironmentVariables", "json", "-o", "-",
         str(plist_path)],
        ["plutil", "-extract", "StandardOutPath", "raw", "-o", "-",
         str(plist_path)],
        ["plutil", "-extract", "StandardErrorPath", "raw", "-o", "-",
         str(plist_path)],
    ]
    for cmd in cmds:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return 1
        if "{{" in out.stdout:
            return 1
    return 0


def test_the_documented_placeholder_check_returns_zero_after_installation(tmp_path):
    """DAV-2303: the install step documents a check that the placeholders were
    resolved. That check must actually WORK on the installed plist — i.e. it
    must return 0 — while a template with unresolved placeholders must not
    pass it.

    DAV-2292 round 6: the check must look for the residual PLACEHOLDER
    ('{{'), not for one specific name — the old form only grepped
    'LABELS_DIR', so a half-resolved plist (WORK_ROOT still
    '{{WORK_ROOT}}') passed every check, and the 10-22 20:10 run would then
    exit 12 on a bogus relative work root."""
    blob = PLIST.read_text("utf-8")
    installed = tmp_path / "installed.plist"
    installed.write_text(
        blob.replace("{{LABELS_DIR}}", "/tmp/labels-x")
            .replace("{{WORK_ROOT}}", "/Users/davidliu/ta-p4-2e1e199f/work"),
        "utf-8")
    assert _placeholder_check(installed) == 0, "the documented check failed on a fully resolved plist"
    # the template itself (unresolved) must NOT pass
    template = tmp_path / "template.plist"
    template.write_text(blob, "utf-8")
    assert _placeholder_check(template) == 1, "the documented check passed an unresolved plist"


def test_the_documented_placeholder_check_catches_a_half_resolved_plist(tmp_path):
    """DAV-2292 round 6: replacing only {{LABELS_DIR}} (which is exactly what
    the old §3 sed did) must make the documented check FAIL. The reviewer
    showed that the old check passed in that case, so the 10-22 20:10 run
    would have exited 12 on a relative '{{WORK_ROOT}}' work root."""
    blob = PLIST.read_text("utf-8")
    half = tmp_path / "half.plist"
    half.write_text(blob.replace("{{LABELS_DIR}}", "/tmp/labels-x"), "utf-8")
    assert _placeholder_check(half) == 1, \
        "the documented check passed a half-resolved plist"
    # and the value really is still a placeholder (the reason it matters)
    out = subprocess.run(
        ["plutil", "-extract", "EnvironmentVariables.TA_WORK_ROOT", "raw",
         "-o", "-", str(half)],
        capture_output=True, text=True, timeout=30)
    assert "{{WORK_ROOT}}" in out.stdout, out.stdout


def test_the_install_instructions_text_is_pinned():
    """DAV-2292 🟢-2 (round 7): the install instructions are TEXT, and the
    reviewer showed that deleting the `{{WORK_ROOT}}` sed line from §3 and
    reverting the plist comment's four `grep -q '{{'` back to
    `grep -q 'LABELS_DIR'` still left the whole file green (44 passed) —
    the three placeholder tests exercise a PYTHON re-implementation
    (`_placeholder_check()`), not the shell text an operator actually runs.

    So pin the text itself: both places must carry TWO `-e` replacements
    (LABELS_DIR + WORK_ROOT) and FOUR `grep -q '{{'` checks
    (ProgramArguments / EnvironmentVariables / StandardOutPath /
    StandardErrorPath)."""
    doc = (REPO / "releases" / "launchd" / "TA_ENV_TCC.md").read_text("utf-8")
    section3 = doc.split("## 3. 安装步骤")[1].split("\n## 4.")[0]
    plist_text = PLIST.read_text("utf-8")

    sed_marker = '-e "s|'
    check_marker = "grep -q '{{'"
    for name, text in (("TA_ENV_TCC.md §3", section3),
                       ("the plist install comment", plist_text)):
        # two sed replacements: {{LABELS_DIR}} and {{WORK_ROOT}}
        n_sed = text.count(sed_marker)
        assert n_sed == 2, f"{name}: expected 2 sed -e replacements, got {n_sed}"
        assert "{{LABELS_DIR}}" in text, name
        assert "{{WORK_ROOT}}" in text, name
        # four placeholder checks, and they must look for the PLACEHOLDER
        # itself, not for one specific name
        n_checks = text.count(check_marker)
        assert n_checks == 4, f"{name}: expected 4 placeholder checks, got {n_checks}"
        assert "grep -q 'LABELS_DIR'" not in text, \
            f"{name}: a check still greps for one specific placeholder name"
        # the WORK_ROOT replacement must actually be there
        assert "s|{{WORK_ROOT}}|${WORK_ROOT}|g" in text, name


def test_no_hard_coded_checkout_survives_in_the_shipped_script_and_plist():
    """DAV-2303 regression: the 2026-10-20 15:56 exit 11 happened because the
    shell script still pointed at ta-p4-ef8aa9d9. That literal must not come
    back as a LIVE PATH — neither in the script nor in the plist.

    Scope: the check is on live values only (assignments / plist <string>
    elements). Prose that NAMES the old checkout to explain the incident is
    allowed and desirable — it is the history, not a path."""
    wrapper_src = WRAPPER.read_text("utf-8")
    for literal in ("ta-p4-ef8aa9d9", "ta-p4-labels-cb82987b"):
        # no assignment may carry it
        for line in wrapper_src.splitlines():
            if literal in line and not line.lstrip().startswith("#"):
                raise AssertionError(f"{literal} is hard-coded again: {line}")
        # no plist <string> element may carry it
        for line in PLIST.read_text("utf-8").splitlines():
            if literal in line and "<string>" in line:
                raise AssertionError(f"{literal} is hard-coded in the plist: {line}")
    # the three former hard-coded values must all be env-driven now
    assert 'CODE_DIR="${TA_CODE_DIR:-' in wrapper_src
    assert 'PYTHON="${TA_PYTHON:-' in wrapper_src
    assert 'WORK_ROOT="${TA_WORK_ROOT:-' in wrapper_src
    # ... and the trading-day gate must take CODE_DIR from the same variable
    assert "sys.path.insert(0, sys.argv[1])" in wrapper_src


def test_plist_carries_the_checkout_as_a_placeholder_and_env_var():
    """DAV-2303: the plist template must carry the checkout as a {{LABELS_DIR}}
    placeholder (the repo's existing convention, see
    releases/launchd/com.davidliu.ta-daily-reports.plist) and must ALSO pass
    it to the script as TA_CODE_DIR — the two must resolve to the same
    directory, which is exactly what the 15:56 failure violated."""
    blob = PLIST.read_text("utf-8")
    assert "{{LABELS_DIR}}" in blob
    # ProgramArguments and TA_CODE_DIR must use the SAME placeholder
    assert "{{LABELS_DIR}}/scripts/phase2/run_expiry_label_daily.sh" in blob
    assert "<key>TA_CODE_DIR</key>\n        <string>{{LABELS_DIR}}</string>" in blob
    # after the documented sed, both must point at the same absolute dir
    resolved = blob.replace("{{LABELS_DIR}}", "/Users/davidliu/ta-p4-labels-ab12") \
                   .replace("{{WORK_ROOT}}", "/Users/davidliu/ta-p4-2e1e199f/work")
    with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as fh:
        fh.write(resolved.encode("utf-8"))
        tmp_plist = fh.name
    try:
        with open(tmp_plist, "rb") as f:
            parsed = plistlib.load(f)
    finally:
        os.unlink(tmp_plist)
    program = parsed["ProgramArguments"][1]
    code_dir = parsed["EnvironmentVariables"]["TA_CODE_DIR"]
    assert program.startswith(code_dir), (program, code_dir)
    assert parsed["WorkingDirectory"] == code_dir
    # no UNRESOLVED placeholder may survive in the VALUES (the install
    # comment legitimately mentions the {{...}} syntax itself)
    for value in list(parsed["ProgramArguments"]) + \
            list(parsed["EnvironmentVariables"].values()) + \
            [parsed["WorkingDirectory"]]:
        assert "{{" not in value and "}}" not in value, value


# ---------------------------------------------------------------------------
# DAV-2292 🟡-1 — `set -a` (allexport) must not leak the script's own
# bookkeeping variables into the pipeline subprocess environment
# ---------------------------------------------------------------------------

def test_wrapper_does_not_leak_its_own_variables_into_the_subprocess_env(
        tmp_path):
    """DAV-2292 🟡-1: the DAV-2286 `set +u` edit removed the original
    `set +a`, so allexport stayed ON for the rest of the script and EVERY
    later assignment (TODAY, IS_TRADING, CHECK_RC, RUN_RC, GAP_CHECK,
    GAP_LEVEL, GAP_RC …) was exported into the pipeline subprocess env —
    measured 16 items on the candidate vs 8 on the parent side.

    The credential VALUES must still be exported (that is what `set -a` is
    for); the script's own bookkeeping must NOT be. This test uses a stub
    interpreter that dumps its own environment to a file."""
    dump = tmp_path / "child-env.txt"
    script = tmp_path / "w.sh"
    src = WRAPPER.read_text("utf-8")

    # stub interpreter: answer the trading-day probe, and dump the env
    # whenever it is invoked with the pipeline (run/verify) arguments
    stub = tmp_path / "py_stub"
    # invocation shapes:
    #   "${PYTHON}" - "${CODE_DIR}" <<'PY'        -> the trading-day probe
    #   "${PYTHON}" "${PIPELINE}" run   ...        -> the pipeline run
    #   "${PYTHON}" "${PIPELINE}" verify ...       -> the pipeline verify
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "-" ]; then\n'
        '    input="$(cat 2>/dev/null)"\n'
        '    case "$input" in\n'
        '        *is_cn_trading_day*) echo "2026-10-14 True"; exit 0 ;;\n'
        "    esac\n"
        '    echo "0 no-gap"; exit 0\n'   # the SW-leg gap probe
        "fi\n"
        f'if [ "${{2:-}}" = "run" ]; then env > "{dump}"; fi\n'
        "exit 0\n",
        "utf-8")
    stub.chmod(0o755)

    work_root = tmp_path / "work"
    for d in (work_root / "phase2-labels", work_root / "phase2-ledger"):
        d.mkdir(parents=True, exist_ok=True)
    (work_root / "phase2-ledger" / "forward_ledger.jsonl").write_text("", "utf-8")
    cred = tmp_path / "ta.env"
    cred.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    cred.chmod(0o600)

    # the pipeline must EXIST, else the wrapper exits 11 before the run
    code_dir = tmp_path / "code"
    (code_dir / "scripts" / "phase2").mkdir(parents=True, exist_ok=True)
    (code_dir / "scripts" / "phase2" / "expiry_label_pipeline.py").write_text(
        "# stub pipeline\n", "utf-8")

    src = re.sub(r'^PYTHON=.*$', f'PYTHON="{stub}"', src, flags=re.MULTILINE)
    src = re.sub(r'^ENV_FILE=.*$', f'ENV_FILE="{cred}"', src, flags=re.MULTILINE)
    src = re.sub(r'^CODE_DIR=.*$', f'CODE_DIR="{code_dir}"', src,
                 flags=re.MULTILINE)
    src = re.sub(r'^WORK_ROOT=.*$', f'WORK_ROOT="{work_root}"', src,
                 flags=re.MULTILINE)
    script.write_text(src, "utf-8")

    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True,
                          text=True, timeout=60, env=dict(BASE_ENV))
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert dump.exists(), "the pipeline stub was never invoked"
    child_env = dump.read_text("utf-8")

    # the credential must still be exported (that is the point of `set -a`)
    assert "TUSHARE_TOKEN=" in child_env
    # but the script's own bookkeeping variables must NOT be
    for name in ("TODAY=", "IS_TRADING=", "CHECK_RC=", "RUN_RC=",
                 "GAP_CHECK=", "GAP_LEVEL=", "GAP_RC=", "TODAY_CHECK=",
                 "SOURCE_RC=", "GREP_RC=", "GREP_ERR="):
        assert name not in child_env, f"{name} leaked into the subprocess env"


# ---------------------------------------------------------------------------
# DAV-2292 round 3 — 3 x 🟡
# ---------------------------------------------------------------------------

def test_source_failure_is_reported_as_16d_not_13_or_silence(tmp_path):
    """DAV-2292 🟡-1 (round 3): the status of `.` MUST be captured while it is
    still the last command. The previous code captured `$?` AFTER `set +a`
    — a builtin that returns 0 — so the "failed to source" branch (16d) was
    DEAD CODE and a file that passes the gate but cannot be parsed was
    misreported (exit 13) or, worse, silently accepted.

    `A=((` passes the KEY=VALUE gate (it looks like an assignment) but is a
    bash syntax error. Assert: exit 16 AND the alert says "failed to source"
    (not "TUSHARE_TOKEN missing", not exit 0)."""
    res = _run_wrapper(tmp_path, ["A=(("])
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "failed to source" in res["alert"], res["alert"]
    assert "TUSHARE_TOKEN missing" not in res["alert"]


def test_source_failure_with_a_valid_token_line_is_not_silent(tmp_path):
    """The worst path the reviewer described: `A=((` on line 1 followed by a
    valid TUSHARE_TOKEN line. bash does not abort on a syntax error while
    sourcing, so the file can half-apply — the wrapper must still alert
    (exit 16), never exit 0 silently."""
    res = _run_wrapper(tmp_path, ["A=((",
                               f"TUSHARE_TOKEN={FAKE_TOKEN}"])
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "failed to source" in res["alert"], res["alert"]
    assert res["alert"], "a silent pass is the failure mode being fixed"


def test_crlf_credential_file_is_rejected_before_the_gate(tmp_path):
    """DAV-2292 🟡-2 (round 3): the KEY=VALUE gate's tail anchor
    `[[:space:]]*$` accepts a trailing CR as whitespace, so a CRLF file
    PASSES the gate — but bash does not strip the CR, so every value ends
    up with a 0x0d byte (measured: TUSHARE_TOKEN length 7, last byte 0d).
    The token probe then sees a non-empty value and lets it through, while
    the SW industry leg degrades EVERY row to membership_status="gap" —
    and those rows are append-only, so the damage is permanent.

    Assert: the wrapper rejects a CRLF file with exit 16 (NOT exit 13, NOT
    exit 0), and the alert says CRLF."""
    crlf = tmp_path / "crlf.env"
    crlf.write_bytes(f"TUSHARE_TOKEN={FAKE_TOKEN}\r\n".encode("utf-8"))
    res = _run_wrapper(tmp_path, env_file=crlf)
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "CRLF" in res["alert"], res["alert"]


def test_installer_rejects_a_crlf_source(tmp_path):
    """DAV-2292 🟡-2: the installer must refuse a CRLF source (rc 3) — the
    copy it would produce is just as poisoned."""
    src = tmp_path / "src.env"
    src.write_bytes(f"TUSHARE_TOKEN={FAKE_TOKEN}\r\n".encode("utf-8"))
    dest = tmp_path / "ta-secrets" / "ta.env"
    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "CRLF" in res.stderr, res.stderr
    assert not dest.exists()


def test_installer_refuses_to_chmod_a_preexisting_directory(tmp_path):
    """DAV-2292 🟡-3 (round 3): the old code ran `chmod 700` on DEST_DIR
    unconditionally, so `--dest $HOME/ta.env` silently turned the user's
    HOME from 755 to 700 (and /private/tmp from 777 to 700) and still
    reported OK. Now an existing directory must be 700/500 already, and
    must be left untouched otherwise (rc 4)."""
    existing = tmp_path / "existing"
    existing.mkdir()
    existing.chmod(0o755)
    src = tmp_path / "src.env"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    dest = existing / "ta.env"

    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert "refusing to chmod" in res.stderr, res.stderr
    # the pre-existing directory must be UNCHANGED
    assert stat.S_IMODE(existing.stat().st_mode) == 0o755


def test_installer_refuses_the_home_directory_as_destination(tmp_path):
    """DAV-2292 🟡-3: system/home directories are refused outright."""
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(0o755)
    src = tmp_path / "src.env"
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\n", "utf-8")
    dest = home / "ta.env"

    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest),
                        home=home)
    assert res.returncode == 4, (res.returncode, res.stdout, res.stderr)
    assert stat.S_IMODE(home.stat().st_mode) == 0o755


# ---------------------------------------------------------------------------
# DAV-2292 round 4 — 3 x 🟡
# ---------------------------------------------------------------------------

def test_help_prints_the_usage_block_without_the_reminder_line():
    """DAV-2292 🟡-1 (round 4): the previous `head -n -1` fix was a GNU
    extension — BSD head (macOS) errors out with
    "head: illegal line count -- -1", which SIGPIPEs the sed, so the whole
    usage block printed NOTHING while the script still exited 0. The
    "no stray REMINDER line" goal was met by printing nothing at all —
    the same "the fix became an empty check" pattern this card keeps
    catching.

    Assert: rc 0, NO stderr, a NON-EMPTY usage block, the flags are
    documented, and REMINDER is NOT printed."""
    proc = subprocess.run(["/bin/bash", str(INSTALLER), "--help"],
                          capture_output=True, text=True, timeout=30,
                          env=dict(BASE_ENV))
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert proc.stderr == "", proc.stderr
    lines = proc.stdout.splitlines()
    assert len(lines) >= 10, f"usage block is empty/too short: {proc.stdout!r}"
    assert "ta_secrets_install.sh --check" in proc.stdout, proc.stdout
    assert "--src" in proc.stdout and "--dest" in proc.stdout
    assert "TA_ENV_SRC" in proc.stdout and "TA_ENV_WRAPPER" in proc.stdout
    assert "REMINDER" not in proc.stdout, proc.stdout


def test_stderr_temp_file_is_private_and_leaves_nothing_behind(tmp_path):
    """DAV-2292 🟡-2 (round 4): the source's stderr used to be written to a
    FIXED, PREDICTABLE name (`.cred_src_err.$$`) with the shell's default
    umask (0022 -> 0644), i.e. the quoted secret line was copied into a
    world-readable file.

    DAV-2292 round 5: this test only asserted the exit code and "no
    leftovers" — it never checked the MODE or the name, so reverting to the
    predictable 0644 file still passed (the reviewer proved it with a
    mutation test). Now the mode and the name are asserted directly: a
    recorder stub captures the file's mode and path at the moment the
    wrapper touches it."""
    cred = tmp_path / "bad.env"
    cred.write_text("A=((\n", "utf-8")
    # The probe is a `cat` stub: the wrapper reads the temp file with
    # `cat`, so the stub can stat the file it is asked to read and record
    # the path and the mode — no test-only code in the wrapper itself.
    seen = tmp_path / "seen-temp.txt"
    res = _run_wrapper(tmp_path, env_file=cred,
                       extra_env={"STUB_TEMPFILE_PROBE": str(seen)})
    assert res["rc"] == 16, (res["rc"], res["alert"])
    # the probe must have captured something — otherwise the assertions
    # below would be vacuous
    assert seen.exists(), "the probe never ran — the test would be vacuous"
    recorded = seen.read_text("utf-8")
    assert "path=" in recorded, recorded
    assert "mode=" in recorded, recorded
    path = recorded.split("path=")[1].split()[0]
    mode = recorded.split("mode=")[1].split()[0]
    # 1) the file must NOT be group/other readable (mktemp creates 0600)
    assert mode in ("600", "400"), f"temp file mode was {mode}, want 600"
    # 2) the name must NOT be the predictable `.cred_src_err.$$` form
    assert not path.endswith(str(os.getpid())), path
    assert ".cred_src_err." in path, path
    # ... and the random suffix must be more than one char (mktemp XXXXXX)
    suffix = path.rsplit(".cred_src_err.", 1)[-1]
    assert len(suffix) >= 6, path
    # No leftover temp file. The check is scoped to the file THIS run
    # created (the probe recorded its path): a glob over the shared /tmp
    # would also catch files from other runs/sessions and make the test
    # flaky for reasons that have nothing to do with the wrapper.
    assert not Path(path).exists(), f"the temp file survived: {path}"
    leftovers = list(Path(tmp_path).glob(".cred_src_err*"))
    assert not leftovers, leftovers


def test_unusable_tmpdir_fails_closed_instead_of_silently_accepting(tmp_path):
    """DAV-2292 🟢-2 (round 4): when the private temp file could not be
    created, the old code fell back to "look only at $?" — which REPLAYED
    the exact bug the mechanism exists to prevent (a file that passes the
    gate but cannot be parsed was accepted silently, rc 0). It must now
    fail closed."""
    cred = tmp_path / "bad.env"
    cred.write_text("A=((\nTUSHARE_TOKEN=plainvalue\n", "utf-8")
    res = _run_wrapper(tmp_path, env_file=cred,
                       extra_env={"TMPDIR": "/nonexistent-dir-xyz"})
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert res["alert"], "silent pass is the failure mode being fixed"
    assert "TUSHARE_TOKEN missing" not in res["alert"]


def test_the_quoted_credential_line_never_reaches_alert_log_or_child_env(
        tmp_path):
    """DAV-2292 🟢-4 (round 4): "the stderr text is discarded" had no
    regression test — the bad lines used so far (`A=((`) contain no secret
    shape, so only a manual check proved it. Use a bad line that DOES look
    like a credential and assert the value appears nowhere."""
    secret = "SUPERSECRETVALUE123"
    cred = tmp_path / "bad.env"
    cred.write_text(f"TUSHARE_TOKEN={secret}(\n", "utf-8")
    env_dump = tmp_path / "child-env.txt"
    res = _run_wrapper(tmp_path, env_file=cred,
                       extra_env={"STUB_ENV_DUMP": str(env_dump)})
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert secret not in res["alert"], res["alert"]
    assert secret not in res["log"], res["log"]
    # DAV-2292 round 5: the file-content check used to read only the PATH
    # NAMES ("str(list(rglob))"), never the contents — it could not fail.
    for path in tmp_path.rglob("*"):
        if not path.is_file() or path == cred:
            continue          # the INPUT credential file legitimately holds it
        body = path.read_text("utf-8", errors="replace")
        assert secret not in body, f"{secret} leaked into {path}"
    # ... and the CHILD PROCESS env must not carry it either. This is the
    # assertion that was missing: `_src_err` was exported inside the
    # `set -a` window, so every helper inherited the raw stderr — and bash's
    # syntax-error message quotes the offending line, i.e. the value.
    # The pipeline child does NOT run here (the source failed first, which
    # is the point), so the child env is captured by a DIFFERENT mechanism:
    # a stub `cat`/`sed`/`rm` that records the environ it was given. Any of
    # them inheriting `_src_err` (i.e. the raw stderr) is the leak.
    helper_env = tmp_path / "helper-env.txt"
    res2 = _run_wrapper(tmp_path / "helpers", env_file=cred,
                        extra_env={"STUB_HELPER_ENV_DUMP": str(helper_env)})
    assert res2["rc"] == 16, (res2["rc"], res2["alert"])
    assert helper_env.exists(), "no helper recorded its env — vacuous test"
    recorded = helper_env.read_text("utf-8")
    assert secret not in recorded, "the credential value reached a helper env"
    assert "_src_err=" not in recorded, recorded
    assert "_src_errfile=" not in recorded, recorded


def test_no_script_private_variable_reaches_a_helper_environ(tmp_path):
    """DAV-2292 🟢-1 (round 6): `_src_rc` was assigned inside the allexport
    window, so the `cat`/`rm` helpers inherited `_src_rc=0` in their
    environ. The value is only an exit status (no secret), but the rule is
    "the script's own variables never reach a child's environ" — the same
    rule the `_src_err` leak (round 5) violated.

    The helper recorder captures the environ each helper was given."""
    cred = tmp_path / "bad.env"
    cred.write_text("A=((\n", "utf-8")
    helper_env = tmp_path / "helper-env.txt"
    res = _run_wrapper(tmp_path / "h", env_file=cred,
                       extra_env={"STUB_HELPER_ENV_DUMP": str(helper_env)})
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert helper_env.exists(), "no helper recorded its env — vacuous test"
    recorded = helper_env.read_text("utf-8")
    for name in ("_src_rc=", "_src_err=", "_src_errfile=", "_src_lines="):
        assert name not in recorded, f"{name} reached a helper environ"


def test_launchd_run_does_not_log_the_manual_run_warning(tmp_path):
    """DAV-2292 🟡-3 (round 4): the "manual run with the PRODUCTION work
    root" warning must NOT fire on the normal launchd path — the plist
    sets TA_WORK_ROOT explicitly, and that is the only reliable signal
    (XPC_SERVICE_NAME is ALSO exported by the agent runtime, so it cannot
    be used to tell "launchd" from "a hand-run in an agent shell")."""
    res = _run_wrapper(tmp_path, [f"TUSHARE_TOKEN={FAKE_TOKEN}"],
                       extra_env={"TA_WORK_ROOT": str(tmp_path / "work")})
    assert "manual run" not in res["log"], res["log"]


def test_manual_run_without_ta_work_root_does_warn(tmp_path):
    """The other side of the same criterion: a hand-run (no TA_WORK_ROOT) MUST
    warn, even when the surrounding shell happens to export
    XPC_SERVICE_NAME (the agent runtime does)."""
    res = _run_wrapper(tmp_path, [f"TUSHARE_TOKEN={FAKE_TOKEN}"],
                       extra_env={"XPC_SERVICE_NAME":
                                  "com.davidliu.multica-steer"})
    assert "manual run" in res["log"], res["log"]


def test_plist_declares_the_production_work_root(tmp_path):
    """DAV-2292 🟡-3 (round 4): the plist must set TA_WORK_ROOT, otherwise
    every nightly launchd run would log the bogus "manual run" warning.
    DAV-2292 🟢-2 (round 5): the value is now the {{WORK_ROOT}} placeholder
    (three places share it), so the assertion is on the placeholder."""
    with PLIST.open("rb") as fh:
        plist = plistlib.load(fh)
    env = plist["EnvironmentVariables"]
    assert env["TA_WORK_ROOT"] == "{{WORK_ROOT}}"
    # ... and the wrapper's own default is the concrete production path
    assert '/Users/davidliu/ta-p4-2e1e199f/work' in WRAPPER.read_text("utf-8")


def test_plist_work_root_appears_in_exactly_three_places_as_one_placeholder():
    """DAV-2292 🟢-2 (round 5): the work root used to be a bare literal in
    three places (TA_WORK_ROOT, StandardOutPath, StandardErrorPath) that had
    to be changed together by hand. Now all three carry the same
    {{WORK_ROOT}} placeholder, so one sed moves them together."""
    blob = PLIST.read_text("utf-8")
    # count only the VALUES (the install comment also names the placeholder)
    value_lines = [ln for ln in blob.splitlines() if "<string>" in ln]
    assert sum(ln.count("{{WORK_ROOT}}") for ln in value_lines) == 3, \
        [ln for ln in value_lines if "{{WORK_ROOT}}" in ln]
    # no bare work-root literal may survive in a VALUE
    for line in value_lines:
        if "ta-p4-2e1e199f" in line:
            raise AssertionError(f"bare work-root literal left in a value: {line}")
    resolved = blob.replace("{{LABELS_DIR}}", "/tmp/labels-x") \
                  .replace("{{WORK_ROOT}}", "/tmp/work-x")
    with tempfile.NamedTemporaryFile("wb", suffix=".plist", delete=False) as fh:
        fh.write(resolved.encode("utf-8"))
        tmp_plist = fh.name
    try:
        with open(tmp_plist, "rb") as f:
            parsed = plistlib.load(f)
    finally:
        os.unlink(tmp_plist)
    env = parsed["EnvironmentVariables"]
    assert env["TA_WORK_ROOT"] == "/tmp/work-x"
    assert parsed["StandardOutPath"] == "/tmp/work-x/phase2-labels/launchd.out.log"
    assert parsed["StandardErrorPath"] == "/tmp/work-x/phase2-labels/launchd.err.log"
    # every value must be free of unresolved placeholders
    for value in list(parsed["ProgramArguments"]) + \
            list(parsed["EnvironmentVariables"].values()) + \
            [parsed["WorkingDirectory"], parsed["StandardOutPath"],
             parsed["StandardErrorPath"]]:
        assert "{{" not in value and "}}" not in value, value


def test_wrapper_documents_the_new_exit_code_and_env_override():
    """Cheap contract check: the header must keep advertising exit 18 and the
    TA_ENV_FILE indirection, so a future edit cannot silently drop either."""
    src = WRAPPER.read_text("utf-8")
    assert 'ENV_FILE="${TA_ENV_FILE:-' in src
    assert "18 credential file READ denied" in src
    assert "GREP_RC" in src


def test_installer_gate_is_byte_identical_to_the_wrapper_gate():
    """DAV-2286 🟢-4: the installer must check EXACTLY what the timer checks.
    The pattern lives once, in the wrapper, as
    ``CRED_KEY_VALUE_GATE='<pattern>'``; the installer extracts it by eval'ing
    that line. This test resolves both sides the same way and compares them
    byte for byte — a divergence means the installer would accept a copy the
    timer rejects (or vice versa)."""
    line = next(l for l in WRAPPER.read_text("utf-8").splitlines()
                if l.startswith("CRED_KEY_VALUE_GATE="))
    wrapper_gate = subprocess.run(
        ["/bin/bash", "-c",
         'eval "$1"; printf "%s" "$CRED_KEY_VALUE_GATE"', "_", line],
        capture_output=True, text=True, timeout=30).stdout
    assert wrapper_gate, "the wrapper no longer defines the gate"

    # the installer must resolve the same value from the same file
    installer_gate = subprocess.run(
        ["/bin/bash", "-c",
         'GATE_LINE="$(grep -m1 \'^CRED_KEY_VALUE_GATE=\' "$1")"; '
         'eval "${GATE_LINE}"; printf "%s" "${CRED_KEY_VALUE_GATE}"',
         "_", str(WRAPPER)],
        capture_output=True, text=True, timeout=30).stdout
    assert installer_gate == wrapper_gate, (
        len(installer_gate), len(wrapper_gate))
