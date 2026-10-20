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

import plistlib
import re
import shutil
import stat
import subprocess
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
    res = _run_wrapper(tmp_path, ["EVIL=harmless touch /tmp/PWNED_dav2284"])
    assert res["rc"] == 16, (res["rc"], res["alert"])
    assert "non KEY=VALUE lines" in res["alert"]


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
    src.write_text(f"TUSHARE_TOKEN={FAKE_TOKEN}\nEVIL=$(touch /tmp/PWNED_gate)\n",
                   "utf-8")
    dest = tmp_path / "ta-secrets" / "ta.env"
    res = _run_installer(tmp_path, "--src", str(src), "--dest", str(dest))
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "non KEY=VALUE lines" in res.stderr
    assert not dest.exists()


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


def test_wrapper_refuses_to_be_sourced(tmp_path):
    """DAV-2292 事故护栏：2026-10-20 15:56 有人用
    `bash -c 'source run_expiry_label_daily.sh; echo "$VAR"'` 取常量，
    而 `source` 会**执行**整个脚本 → 向**正式**标签目录写了 ALERT.launchd +
    日志，并**读取了真实凭据**（默认 ENV_FILE 指向 ~/Documents/…/.env）。
    标签数据没被写（exit 11 早退），但告警与日志被污染。
    现在脚本被 source 时必须立刻拒绝：不写任何目录、不读任何凭据。"""
    proc = subprocess.run(
        ["/bin/bash", "-c",
         f'source {WRAPPER}; echo "$CRED_KEY_VALUE_GATE"'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path)})
    assert proc.returncode == 64, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSED" in proc.stderr
    assert "PRODUCTION label dir" in proc.stderr
    # 什么都没被 source 出来（闸常量没被打印）
    assert proc.stdout.strip() == "", proc.stdout


def test_wrapper_refuses_to_be_sourced_writes_nothing_to_the_output_dir(
        tmp_path):
    """同一条护栏的写侧断言：被 source 时不得在输出目录留下任何文件。"""
    out_dir = tmp_path / "out" / "phase2-labels"
    out_dir.mkdir(parents=True)
    src = WRAPPER.read_text("utf-8")
    src = re.sub(r'^WORK_ROOT=.*$',
                 f'WORK_ROOT="{tmp_path / "out"}"', src, flags=re.M)
    copy = tmp_path / "w.sh"
    copy.write_text(src, "utf-8")
    proc = subprocess.run(
        ["/bin/bash", "-c", f'source {copy} 2>/dev/null'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path)})
    assert proc.returncode == 64
    assert list(out_dir.iterdir()) == [], "sourcing wrote into the output dir"


def test_installer_also_refuses_to_be_sourced(tmp_path):
    """DAV-2292 护栏的覆盖面：安装脚本同样不得被 source（否则会读真实凭据
    并写真实目录）。"""
    proc = subprocess.run(
        ["/bin/bash", "-c", f'source {INSTALLER}'],
        capture_output=True, text=True, timeout=30,
        env={**BASE_ENV, "HOME": str(tmp_path)})
    assert proc.returncode == 64, (proc.returncode, proc.stdout, proc.stderr)
    assert "REFUSED" in proc.stderr


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
