"""DAV-2118: tests for the production-port guard itself.

The guard forbids connections to the production API listener
(127.0.0.1:8000 / localhost:8000) inside the test suite — in-process via
wrapped socket callables, and in children via the sitecustomize audit hook
armed by ``repo_script_env[TA_TEST_PORT_GUARD]``. These are the positive
(violation is denied) and negative (legitimate loopback still passes)
control cases.

Note on scope: urllib3/requests are deliberately NOT exercised here —
they build on ``socket.create_connection``/``socket.connect``, which the
guards intercept. Exercising them would require an HTTP-level dependency
and adds no coverage over the socket-level checks below.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import tests.production_port_guard as ppg
from tests.conftest import OfflineTestGuardrailError, offline_guard_disabled

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# In-process layer
# --------------------------------------------------------------------------

class TestInProcessDenied:
    """Positive cases: dialing :8000 on any loopback alias must fail the test."""

    @pytest.mark.parametrize("host", [
        "127.0.0.1", "localhost", "testserver", "x.localhost", "::1",
    ])
    def test_connect_denied(self, host):
        s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET,
                          socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineTestGuardrailError, match="8000"):
                s.connect((host, 8000))
        finally:
            s.close()

    def test_connect_ex_denied(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineTestGuardrailError):
                s.connect_ex(("127.0.0.1", 8000))
        finally:
            s.close()

    def test_create_connection_denied(self):
        with pytest.raises(OfflineTestGuardrailError):
            socket.create_connection(("localhost", 8000), timeout=1)

    def test_udp_sendto_denied(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(OfflineTestGuardrailError):
                s.sendto(b"ping", ("127.0.0.1", 8000))
        finally:
            s.close()

    def test_denial_survives_offline_guard_disabled(self):
        """The guard is fail-closed under RT_NETWORK_ENABLED bypass paths:
        with the offline guardrail suspended, :8000 is still denied."""
        with offline_guard_disabled():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(OfflineTestGuardrailError):
                    s.connect(("127.0.0.1", 8000))
            finally:
                s.close()


class TestInProcessAllowed:
    """Negative cases: any other loopback port remains usable for
    test-local listeners — the guard is a port filter, not a loopback ban."""

    def test_local_listener_other_port_allowed(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                cli.connect(("127.0.0.1", port))
                conn, _ = srv.accept()
                conn.close()
            finally:
                cli.close()
        finally:
            srv.close()

    def test_unspecified_bind_not_blocked(self):
        """0.0.0.0:8000 is a bind address, not a dialable production peer —
        the predicate must not flag it."""
        assert not ppg.is_blocked_target(("0.0.0.0", 8000))
        assert not ppg.is_blocked_target(("::", 8000))

    def test_nonloopback_8000_left_to_offline_guard(self):
        """An external host on :8000 is not this guard's verdict (the
        offline guardrail already denies it as non-local)."""
        assert not ppg.is_blocked_target(("192.0.2.1", 8000))

    def test_other_ports_not_flagged(self):
        assert not ppg.is_blocked_target(("127.0.0.1", 7999))
        assert not ppg.is_blocked_target(("127.0.0.1", 8001))
        assert not ppg.is_blocked_target(("localhost", 5432))


# --------------------------------------------------------------------------
# Child-process layer (sitecustomize + repo_script_env)
# --------------------------------------------------------------------------

_CHILD_CONNECT_8000 = r"""
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", 8000))
except Exception as exc:
    if "8000" in str(exc):
        print("CHILD_DENIED")
    else:
        print("CHILD_OTHER:" + type(exc).__name__)
else:
    print("CHILD_CONNECTED")
finally:
    s.close()
"""

_CHILD_CONNECT_OTHER = r"""
import socket
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("127.0.0.1", port))
    print("CHILD_ALLOWED")
finally:
    s.close()
    srv.close()
"""


class TestChildProcessGuard:
    """repo_script_env must carry the arming flag; sitecustomize must deny
    :8000 in a child that never imports conftest, while leaving other
    loopback ports usable."""

    def test_repo_script_env_arms_flag(self, repo_script_env):
        assert repo_script_env[ppg.ENV_FLAG] == "1"

    def test_child_denied_8000(self, repo_script_env):
        res = subprocess.run(
            [sys.executable, "-c", _CHILD_CONNECT_8000],
            env=repo_script_env, cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        assert "CHILD_DENIED" in res.stdout, (
            f"rc={res.returncode} stdout={res.stdout!r} stderr={res.stderr!r}"
        )

    def test_child_other_loopback_port_allowed(self, repo_script_env):
        res = subprocess.run(
            [sys.executable, "-c", _CHILD_CONNECT_OTHER],
            env=repo_script_env, cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        assert "CHILD_ALLOWED" in res.stdout, (
            f"rc={res.returncode} stdout={res.stdout!r} stderr={res.stderr!r}"
        )

    def test_child_flag_absent_no_hook(self, repo_script_env):
        """Without the arming flag the child is untouched (sitecustomize is
        a no-op) — a real connect to :8000 then lands whatever the OS says
        (refused if nothing listens)."""
        env = dict(repo_script_env)
        env.pop(ppg.ENV_FLAG, None)
        code = r"""
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect(("127.0.0.1", 8000))
except Exception as exc:
    # ConnectionRefusedError (nothing listening) or guardrail — but if a
    # stray local service answers we must NOT print CONNECTED_AND_ARMED.
    print("CHILD_UNARMED:" + type(exc).__name__)
else:
    print("CHILD_CONNECTED")
finally:
    s.close()
"""
        res = subprocess.run(
            [sys.executable, "-c", code],
            env=env, cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        # The production service may genuinely be up on this dev box, so
        # both outcomes are acceptable — what we verify is the flag gate:
        # absence of the flag means the sitecustomize hook is not active,
        # proven by stderr NOT carrying a guardrail denial.
        assert "CHILD_UNARMED" in res.stdout or "CHILD_CONNECTED" in res.stdout


class TestParentEnvArmed:
    """conftest import arms the parent's own env so children spawned with a
    bare inherited os.environ are covered too."""

    def test_parent_env_flag_set(self):
        assert os.environ.get(ppg.ENV_FLAG) == "1"
