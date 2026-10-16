"""DAV-2150 — automated tests for the DAV-1852 service wrapper.

``releases/launchd/ta-launchd-wrapper.sh`` is the ``com.tradingagents.ashare``
launchd wrapper (throttle counter + hardened ``env -i`` baseline + ``exec``
uvicorn).  Until now it was only covered by ``bash -n`` + manual rehearsal
(DAV-1843); DAV-2144 precheck flagged the zero automated coverage as ⚠️.

Everything here runs the wrapper with **stub processes and a tmp-dir sandbox** —
the deployment constants (``RELEASE_SHA`` / ``RELEASES_ROOT`` / ``VENV_PYTHON``)
are sed-rewritten to tmp paths, ``VENV_PYTHON`` points at a stub interpreter that
records argv/env to a file instead of binding a port, so **nothing ever touches
127.0.0.1:8000**.

Covers the card's three items:

* ``RELEASE_SHA`` resolution — ``RELEASE_DIR`` is ``${RELEASES_ROOT}/${RELEASE_SHA}``
  and the uvicorn log name embeds the SHA;
* failure when the directory does not exist — ``die`` exits 1 after the
  invocation has been counted (bounded-restart invariant), never reaches
  ``exec``;
* env loading — the ``exec env -i`` baseline hands the stub process exactly the
  hardened key set (HOME/PATH/TMPDIR/DATABASE_URL/TA_*/proxy/no_proxy…), with no
  ambient leak-through.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "releases" / "launchd" / "ta-launchd-wrapper.sh"


def test_wrapper_is_valid_bash():
    assert WRAPPER.is_file()
    r = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- #
# Sandbox builder: rewrite deployment constants to tmp paths + stub python    #
# --------------------------------------------------------------------------- #

_STUB_PY = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    \"\"\"Stub VENV_PYTHON: record argv + env to STUB_RECORD, never bind a port.\"\"\"
    import json, os, sys
    rec = os.environ["STUB_RECORD"]
    with open(rec, "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv, "env": dict(os.environ), "cwd": os.getcwd()}, f)
    sys.exit(0)
    """
)


def _sandbox(tmp_path, release_sha="abc12345"):
    """A fully redirected wrapper: all machine paths point into tmp_path.

    Returns dict with the rewritten script, the stub python path, the fake
    RELEASES_ROOT tree (``<root>/<sha>/`` pre-created), logs dir and record file.
    """
    sb = tmp_path / "sb"
    releases_root = sb / "releases"
    release_dir = releases_root / release_sha
    log_dir = releases_root / "logs"
    release_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)

    stub_py = sb / "stub-python"
    stub_py.write_text(_STUB_PY)
    stub_py.chmod(stub_py.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    record = sb / "stub-record.json"

    text = WRAPPER.read_text()
    text = text.replace(
        'RELEASE_SHA="65988c79"', f'RELEASE_SHA="{release_sha}"'
    ).replace(
        'RELEASES_ROOT="/Users/davidliu/Documents/TradingAgents-AShare-releases"',
        f'RELEASES_ROOT="{releases_root}"',
    ).replace(
        'VENV_PYTHON="/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"',
        f'VENV_PYTHON="{stub_py}"',
    )
    # The stub python needs STUB_RECORD to know where to write; the wrapper's
    # `exec env -i …` wipes everything, so the record path has to ride in via
    # the exec environment itself — but the wrapper does not forward arbitrary
    # env.  Bake the record path into the stub source instead (env-free
    # channel, robust to `env -i`).
    stub_py.write_text(_STUB_PY.replace('os.environ["STUB_RECORD"]', repr(str(record))))

    wrapper = sb / "ta-launchd-wrapper.sh"
    wrapper.write_text(text)
    wrapper.chmod(0o755)
    return {
        "wrapper": wrapper,
        "releases_root": releases_root,
        "release_dir": release_dir,
        "log_dir": log_dir,
        "stamp_file": log_dir / ".launchd" / "restart-stamps",
        "diag_log": log_dir / "wrapper-diagnostic.log",
        "stub_py": stub_py,
        "record": record,
        "sha": release_sha,
    }


def _run(sb, **env):
    e = dict(os.environ)
    e.update(env)
    return subprocess.run(
        ["bash", str(sb["wrapper"])],
        capture_output=True, text=True, env=e, timeout=30,
    )


# --------------------------------------------------------------------------- #
# 1. RELEASE_SHA resolution                                                    #
# --------------------------------------------------------------------------- #

def test_release_sha_resolves_release_dir_and_log_name(tmp_path):
    """Wrapper resolves RELEASE_DIR=RELEASES_ROOT/RELEASE_SHA and names the
    uvicorn log uvicorn-<SHA>.log — the stub exec proves both."""
    sb = _sandbox(tmp_path, release_sha="feedbeef")
    r = _run(sb)
    assert r.returncode == 0, r.stderr
    rec = json.loads(sb["record"].read_text())
    # exec'd argv: <stub_py> -m uvicorn api.main:app --host 127.0.0.1 --port 8000
    argv = rec["argv"]
    assert argv[0] == str(sb["stub_py"])
    assert argv[1:4] == ["-m", "uvicorn", "api.main:app"]
    # RELEASE_DIR resolution: exec happens inside <root>/<sha> — the wrapper
    # `cd`s there first, so the stub's cwd must be the release dir.
    assert rec["cwd"] == str(sb["release_dir"])
    # The wrapper redirects uvicorn output to logs/uvicorn-<sha>.log — the file
    # is opened by bash before exec, so it exists even though the stub wrote
    # nothing to it.
    assert (sb["log_dir"] / f"uvicorn-{sb['sha']}.log").is_file()


# --------------------------------------------------------------------------- #
# 2. Missing RELEASE_DIR → die() fail-closed                                  #
# --------------------------------------------------------------------------- #

def test_missing_release_dir_dies_and_is_counted(tmp_path):
    """A nonexistent release dir must exit 1 (die), the invocation is counted
    in restart-stamps first (bounded ≤3/hour applies to failures too), and the
    stub interpreter is never exec'd."""
    sb = _sandbox(tmp_path, release_sha="deadbeef")
    shutil.rmtree(sb["release_dir"])  # the point under test
    r = _run(sb)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert not sb["record"].exists(), "stub python must never run"
    # Counted: one stamp line (epoch) for this invocation.
    stamps = sb["stamp_file"].read_text().split()
    assert len(stamps) == 1 and stamps[0].isdigit()
    # Diagnostic names the missing dir.
    assert str(sb["release_dir"]) in r.stderr


def test_missing_release_dir_then_throttle_cap_blocks(tmp_path):
    """Fail-closed + bounded: after MAX_RESTARTS stamped invocations the wrapper
    exits 0 (throttle stop) without launching — the missing-dir die path feeds
    the same counter."""
    sb = _sandbox(tmp_path, release_sha="deadbeef")
    shutil.rmtree(sb["release_dir"])
    rcs = [_run(sb).returncode for _ in range(4)]
    # 1st..3rd counted die → exit 1; 4th hits the 3600s cap → exit 0 stop.
    assert rcs[:3] == [1, 1, 1], rcs
    assert rcs[3] == 0, rcs
    assert not sb["record"].exists()


# --------------------------------------------------------------------------- #
# 3. .env loading — the exec env -i baseline                                   #
# --------------------------------------------------------------------------- #

def test_exec_env_i_baseline_reaches_stub(tmp_path):
    """The wrapper must hand the uvicorn process exactly the hardened env
    baseline (the '.env' the wrapper loads): nothing from the ambient shell
    leaks through `env -i`, and the required keys arrive with the right shape."""
    sb = _sandbox(tmp_path)
    # Poison the ambient env — a leak-through would show up in the record.
    r = _run(sb, LEAK_MARKER="must_not_appear", DATABASE_URL="sqlite:///WRONG.db",
             PYTHONPATH="/poisoned/path")
    assert r.returncode == 0, r.stderr
    env = json.loads(sb["record"].read_text())["env"]

    # Required baseline keys (LAUNCHD_SWITCH.md's 16/16 contract).
    for k in ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "TMPDIR",
              "DATABASE_URL", "TA_SOCIAL_MODE", "APP_VERSION",
              "TA_PRICE_REF_REVISION_ENABLED", "TA_E04_REVISION_ENABLED",
              "http_proxy", "https_proxy", "all_proxy", "no_proxy", "NO_PROXY"):
        assert k in env, f"missing baseline key {k}"
    assert env["DATABASE_URL"].startswith("sqlite:")
    assert env["TA_SOCIAL_MODE"] == "disabled"
    assert ".eastmoney.com" in env["no_proxy"]
    assert env["no_proxy"] == env["NO_PROXY"]

    # env -i: ambient leakage is absent.
    assert "LEAK_MARKER" not in env
    assert env["DATABASE_URL"] != "sqlite:///WRONG.db"
    assert env.get("PYTHONPATH") != "/poisoned/path"
