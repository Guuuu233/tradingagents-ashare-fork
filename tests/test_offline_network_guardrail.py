"""Comprehensive tests for offline network guardrail (DAV-1007 / PEP 578 audit hook architecture).

Validates:
1. Address Allowlist / Denylist logic (loopback IPv4/IPv6, testserver, AF_UNIX vs TEST-NET, external domains).
2. Fail-fast interception of all 4 audited events (socket.connect/connect_ex, socket.getaddrinfo, socket.gethostbyname, socket.sendto).
3. Local loopback communication (TCP, UDP, AF_UNIX, FastAPI TestClient ASGI) operates completely unhindered.
4. Coexistence and compatibility with existing test-level socket mocks:
   - tests/test_fund_flow_scale_consumption.py:48 (patch("socket.socket.connect"))
   - tests/test_horizon_return_labels.py:806 (monkeypatch.setattr(socket, "socket", block_socket))
   Proving no cascade crashes, and reporting nodeid & phase on tampering.
5. Explicit network switch fail-closed behavior: omission of switch defaults to offline denial.
6. 1:1 pairing between Observation Log non-local entries and Deny Log entries, plus positive control sentinels with pytest main PID.
7. Architecture invariants: PEP 578 irreversible hooks, no depth counters, TEST-NET addresses only.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    OfflineTestGuardrailError,
    _is_local_or_loopback,
    check_socket_health,
    get_deny_log_path,
    get_observation_log_path,
    is_network_enabled,
    is_offline_network_guard_active,
    run_guardrail_sentinel,
)

# Reserved TEST-NET addresses (RFC 5737) - never use 198.18.x or 127.0.0.1:9
TEST_NET_1_IP = "192.0.2.1"
TEST_NET_2_IP = "198.51.100.1"
TEST_NET_3_IP = "203.0.113.1"


class TestAddressAllowlistUnit:
    """Unit tests for loopback, IPC, and external address classification."""

    def test_loopback_ipv4_and_names(self):
        assert _is_local_or_loopback(("127.0.0.1", 80)) is True
        assert _is_local_or_loopback(("127.0.0.2", 8080)) is True
        assert _is_local_or_loopback(("localhost", 8000)) is True
        assert _is_local_or_loopback(("sub.localhost", 80)) is True
        assert _is_local_or_loopback(("testserver", 80)) is True
        assert _is_local_or_loopback(("0.0.0.0", 80)) is True
        assert _is_local_or_loopback("localhost") is True
        assert _is_local_or_loopback("127.0.0.1") is True
        assert _is_local_or_loopback("testserver") is True
        assert _is_local_or_loopback(None) is True

    def test_loopback_ipv6(self):
        assert _is_local_or_loopback(("::1", 80)) is True
        assert _is_local_or_loopback(("[::1]", 80)) is True
        assert _is_local_or_loopback(("::1", 80, 0, 0)) is True
        assert _is_local_or_loopback(("::", 80)) is True

    def test_af_unix_and_paths(self):
        assert _is_local_or_loopback("/tmp/test.sock") is True
        assert _is_local_or_loopback(b"/tmp/test.sock") is True
        if hasattr(socket, "AF_UNIX"):
            assert _is_local_or_loopback(("/tmp/test.sock",), family=socket.AF_UNIX) is True

    def test_external_addresses_disallowed(self):
        # TEST-NET reserved ranges (RFC 5737)
        assert _is_local_or_loopback((TEST_NET_1_IP, 80)) is False
        assert _is_local_or_loopback((TEST_NET_2_IP, 443)) is False
        assert _is_local_or_loopback((TEST_NET_3_IP, 8080)) is False
        assert _is_local_or_loopback(TEST_NET_1_IP) is False

        # Public IPs
        assert _is_local_or_loopback(("8.8.8.8", 53)) is False
        assert _is_local_or_loopback(("1.1.1.1", 80)) is False

        # External domains
        assert _is_local_or_loopback(("baostock.com", 80)) is False
        assert _is_local_or_loopback(("example.com", 80)) is False
        assert _is_local_or_loopback("baostock.com") is False
        assert _is_local_or_loopback("api.openai.com") is False

        # Private LAN IPs
        assert _is_local_or_loopback(("192.168.1.1", 80)) is False
        assert _is_local_or_loopback(("10.0.0.1", 80)) is False
        assert _is_local_or_loopback(("172.16.0.1", 80)) is False


class TestOfflineGuardrailInterception:
    """Verification of runtime audit hook interception and fail-fast semantics."""

    def test_tcp_connect_external_ip_blocked_fail_fast(self):
        """TCP socket.connect to TEST-NET IP must raise OfflineTestGuardrailError in milliseconds."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            t0 = time.perf_counter()
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.connect((TEST_NET_1_IP, 80))
            elapsed = time.perf_counter() - t0

            assert elapsed < 0.05, f"Fail-fast exceeded 50ms: took {elapsed*1000:.2f}ms"
            err_msg = str(exc_info.value)
            assert TEST_NET_1_IP in err_msg
            assert "OfflineTestGuardrail" in err_msg
            assert issubclass(OfflineTestGuardrailError, RuntimeError)
        finally:
            s.close()

    def test_tcp_connect_ex_external_ip_blocked(self):
        """TCP socket.connect_ex to external destination must trigger interception."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.connect_ex((TEST_NET_2_IP, 443))
            assert TEST_NET_2_IP in str(exc_info.value)
        finally:
            s.close()

    def test_dns_getaddrinfo_external_domain_blocked_fail_fast(self):
        """socket.getaddrinfo for external domain must fail-fast without network DNS query."""
        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            socket.getaddrinfo("example.com", 80)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05, f"Fail-fast exceeded 50ms: took {elapsed*1000:.2f}ms"
        assert "example.com" in str(exc_info.value)

    def test_dns_gethostbyname_external_domain_blocked(self):
        """socket.gethostbyname for external domain must be intercepted."""
        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            socket.gethostbyname("baostock.com")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05
        assert "baostock.com" in str(exc_info.value)

    def test_socket_create_connection_blocked_fail_fast(self):
        """socket.create_connection to external destination must fail-fast at DNS/addrinfo stage."""
        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            socket.create_connection((TEST_NET_3_IP, 80), timeout=1)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05
        assert TEST_NET_3_IP in str(exc_info.value)

    def test_udp_sendto_external_ip_blocked(self):
        """UDP socket.sendto to external destination must be intercepted."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.sendto(b"ping", (TEST_NET_1_IP, 53))
            assert TEST_NET_1_IP in str(exc_info.value)
        finally:
            s.close()

    def test_curl_cffi_blocked_fail_fast(self):
        """curl_cffi perform must be intercepted to prevent C-level libcurl bypass."""
        from tests.conftest import get_curl_cffi_guard_status
        status = get_curl_cffi_guard_status()
        assert status.startswith("installed:") or status.startswith("not_installed:"), (
            f"Unexpected curl_cffi guard status: {status}"
        )
        try:
            import curl_cffi.curl
            c = curl_cffi.curl.Curl()
            c.setopt(curl_cffi.curl.CurlOpt.URL, b"https://example.com")
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                c.perform()
            assert "curl_cffi" in str(exc_info.value)
        except ImportError:
            pytest.skip("curl_cffi not installed")


class TestLocalLoopbackAndIpcAllowed:
    """Verify that loopback, in-process clients, and IPC operate completely unhindered."""

    def test_local_loopback_tcp_allowed(self):
        """Local TCP communication on 127.0.0.1 must be permitted without hindrance."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def handle_server():
            conn, _ = server.accept()
            data = conn.recv(1024)
            conn.sendall(b"pong:" + data)
            conn.close()

        th = threading.Thread(target=handle_server, daemon=True)
        th.start()

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        client.sendall(b"guardrail-tcp-test")
        resp = client.recv(1024)
        assert resp == b"pong:guardrail-tcp-test"
        client.close()
        th.join(timeout=2)
        server.close()

    def test_local_loopback_udp_allowed(self):
        """Local UDP datagrams on 127.0.0.1 must be permitted."""
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.sendto(b"udp-local-test", ("127.0.0.1", port))

        data, _ = server.recvfrom(1024)
        assert data == b"udp-local-test"
        client.close()
        server.close()

    def test_af_unix_domain_socket_allowed(self):
        """AF_UNIX sockets must be allowed for local IPC."""
        if not hasattr(socket, "AF_UNIX"):
            pytest.skip("AF_UNIX not supported on this platform")

        temp_dir = tempfile.mkdtemp()
        sock_path = os.path.join(temp_dir, "test_ipc.sock")
        try:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(sock_path)
            server.listen(1)

            def unix_srv():
                conn, _ = server.accept()
                msg = conn.recv(64)
                conn.sendall(b"unix-echo:" + msg)
                conn.close()

            th = threading.Thread(target=unix_srv, daemon=True)
            th.start()

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(sock_path)
            client.sendall(b"hello-unix")
            resp = client.recv(64)
            assert resp == b"unix-echo:hello-unix"
            client.close()
            th.join(timeout=2)
            server.close()
        finally:
            if os.path.exists(sock_path):
                os.unlink(sock_path)
            os.rmdir(temp_dir)

    def test_fastapi_testclient_unaffected(self):
        """FastAPI TestClient in-memory ASGI dispatch operates completely unaffected."""
        app = FastAPI()

        @app.get("/ping")
        def ping():
            return {"status": "ok", "guard_active": is_offline_network_guard_active()}

        client = TestClient(app)
        resp = client.get("/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["guard_active"] is True


class TestExistingSocketMockCompatibility:
    """Targeted compatibility tests for existing test-level socket mocks (门禁 ②).

    1. tests/test_fund_flow_scale_consumption.py:48 autouse guard_no_network_calls (patch socket.socket.connect)
    2. tests/test_horizon_return_labels.py:806 monkeypatch.setattr(socket, "socket", block_socket)

    Must prove:
    - No cascade crash across tests.
    - Guardrail detects tampering and reports exact nodeid and phase without crashing with AttributeError.
    """

    def test_compatibility_with_patch_socket_connect(self):
        """Verify compatibility with test_fund_flow_scale_consumption.py:48 pattern.

        Using patch('socket.socket.connect', side_effect=RuntimeError(...)) must coexist with audit hook.
        """
        from unittest.mock import patch

        custom_error_msg = "Network access forbidden in offline tests"
        with patch("socket.socket.connect", side_effect=RuntimeError(custom_error_msg)):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # The test-level mock intercepts first and raises its own RuntimeError
            with pytest.raises(RuntimeError) as exc_info:
                s.connect((TEST_NET_1_IP, 80))
            assert custom_error_msg in str(exc_info.value)
            s.close()

        # After exiting the patch context, socket.socket is clean and healthy
        healthy, report = check_socket_health(
            nodeid="tests/test_offline_network_guardrail.py::test_compatibility_with_patch_socket_connect",
            phase="call",
        )
        assert healthy is True
        assert "socket healthy" in report

    def test_compatibility_with_monkeypatch_socket_class(self, monkeypatch):
        """Verify compatibility with test_horizon_return_labels.py:806 pattern.

        monkeypatch.setattr(socket, 'socket', block_socket) replaces the socket.socket class
        with a function. Guardrail must:
        1. Not crash with AttributeError when checking or handling socket.
        2. Detect tampering and report exact nodeid and phase.
        3. Restore cleanly after monkeypatch teardown without cascading failures.
        """
        def block_socket(*args, **kwargs):
            raise AssertionError("Unexpected network socket creation attempted!")

        # Replace socket.socket with a function (reproducing line 806 exactly)
        monkeypatch.setattr(socket, "socket", block_socket)

        # 1. Calling socket.socket() raises the test's AssertionError, NOT a guardrail AttributeError
        with pytest.raises(AssertionError) as exc_info:
            socket.socket()
        assert "Unexpected network socket creation attempted!" in str(exc_info.value)

        # 2. Guardrail detects tampering safely and reports nodeid and phase
        fake_nodeid = "tests/test_horizon_return_labels.py::TestResolveHorizonCalendarWindow::test_resolution_makes_no_network_calls"
        fake_phase = "call"
        healthy, report = check_socket_health(nodeid=fake_nodeid, phase=fake_phase)
        assert healthy is False
        assert fake_nodeid in report
        assert fake_phase in report
        assert "is not a class" in report
        assert "block_socket" in report

    def test_sequential_execution_no_cascade_error(self):
        """Ensure that subsequent tests execute normally after socket mock tests."""
        healthy, report = check_socket_health()
        assert healthy is True

        # Ensure normal local TCP operations work
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        assert s.getsockname()[1] > 0
        s.close()


class TestNetworkSwitchFailClosed:
    """Verification of explicit network authorization switch and fail-closed semantics (门禁 ③)."""

    def test_switch_missing_defaults_to_fail_closed(self, monkeypatch):
        """When RT_NETWORK_ENABLED is missing or unset, guardrail must be active (fail-closed)."""
        monkeypatch.delenv("RT_NETWORK_ENABLED", raising=False)
        assert is_network_enabled() is False
        assert is_offline_network_guard_active() is True

        # External connection must be blocked
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineTestGuardrailError):
                s.connect((TEST_NET_1_IP, 80))
        finally:
            s.close()

    def test_switch_empty_or_zero_defaults_to_fail_closed(self, monkeypatch):
        """When RT_NETWORK_ENABLED is empty string or '0', guardrail must remain active."""
        for val in ("", "0", "false", "no", "DISABLED"):
            monkeypatch.setenv("RT_NETWORK_ENABLED", val)
            assert is_network_enabled() is False
            assert is_offline_network_guard_active() is True

    def test_forgetting_switch_subprocess_fail_closed(self):
        """Dedicated subprocess test covering 'forgetting to set switch' path.

        Even when running pytest directly without RT_NETWORK_ENABLED, the process
        defaults to offline mode and denies external outbound connections.
        """
        code = """
import socket, sys
from tests.conftest import OfflineTestGuardrailError

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.connect(("192.0.2.1", 80))
    sys.exit(10)  # Should have been blocked!
except OfflineTestGuardrailError:
    sys.exit(0)   # Correctly blocked fail-closed
finally:
    s.close()
"""
        # Run in clean subprocess without RT_NETWORK_ENABLED
        clean_env = os.environ.copy()
        clean_env.pop("RT_NETWORK_ENABLED", None)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            env=clean_env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Subprocess did not fail-closed: returncode={proc.returncode}"


class TestObservationAndDenyLogsPairing:
    """Verification of dual-hook logging, 1:1 pairing, and positive control sentinels (门禁 2 & 3)."""

    def test_sentinel_executed_and_pid_matches_pytest_main(self):
        """Both observation log and deny log must contain positive control sentinels with current PID."""
        obs_path = get_observation_log_path()
        deny_path = get_deny_log_path()

        assert os.path.exists(obs_path), f"Observation log does not exist: {obs_path}"
        assert os.path.exists(deny_path), f"Deny log does not exist: {deny_path}"

        current_pid = str(os.getpid())

        # Read observation log
        with open(obs_path, "r", encoding="utf-8") as f:
            obs_lines = f.readlines()
        with open(deny_path, "r", encoding="utf-8") as f:
            deny_lines = f.readlines()

        # Check positive control in observation log
        obs_sentinels = [line for line in obs_lines if "SENTINEL_" in line]
        assert len(obs_sentinels) >= 1, "Observation log missing SENTINEL records"
        assert any(f"PID:{current_pid}" in line for line in obs_sentinels), (
            f"Observation sentinel PID does not match current PID {current_pid}"
        )

        # Check positive control in deny log
        deny_sentinels = [line for line in deny_lines if "SENTINEL_" in line]
        assert len(deny_sentinels) >= 1, "Deny log missing SENTINEL records"
        assert any(f"PID:{current_pid}" in line for line in deny_sentinels), (
            f"Deny sentinel PID does not match current PID {current_pid}"
        )

    def test_non_local_events_one_to_one_paired(self):
        """Every non-local event in Observation Log must have a matching entry in Deny Log."""
        obs_path = get_observation_log_path()
        deny_path = get_deny_log_path()

        with open(obs_path, "r", encoding="utf-8") as f:
            obs_lines = f.readlines()
        with open(deny_path, "r", encoding="utf-8") as f:
            deny_lines = f.readlines()

        # Extract non-local events from observation log
        obs_non_local = [
            line.strip() for line in obs_lines
            if "LOCAL:False" in line
        ]
        deny_entries = [
            line.strip() for line in deny_lines
            if "ACTION:DENY" in line
        ]

        assert len(obs_non_local) > 0, "No non-local attempts recorded during tests"
        assert len(obs_non_local) == len(deny_entries), (
            f"Mismatch between observation non-local ({len(obs_non_local)}) "
            f"and deny entries ({len(deny_entries)})"
        )

        # Verify each non-local observation has matching event and target in deny log
        for i, (obs, deny) in enumerate(zip(obs_non_local, deny_entries)):
            # Extract target and event
            obs_target = obs.split("TARGET:")[-1].strip()
            deny_target = deny.split("TARGET:")[-1].strip()
            assert obs_target == deny_target, f"Item {i} target mismatch: {obs_target} vs {deny_target}"


class TestGuardrailArchitectureInvariants:
    """Verification of foundational architectural requirements."""

    def test_pep578_hooks_irreversible(self):
        """PEP 578 specifies that Python audit hooks cannot be removed or replaced."""
        assert not hasattr(sys, "removeaudithook"), "PEP 578 invariant violated: removeaudithook exists"

    def test_depth_counter_completely_removed(self):
        """The depth counter pattern must not exist in conftest.py."""
        import tests.conftest as ct
        assert not hasattr(ct, "_guard_active_depth"), "Architecture violated: depth counter still exists"
        assert not hasattr(ct, "enable_offline_network_guard"), "Architecture violated: per-test enable exists"
        assert not hasattr(ct, "disable_offline_network_guard"), "Architecture violated: per-test disable exists"

    def test_only_test_net_ips_used(self):
        """External test addresses must strictly use TEST-NET blocks (RFC 5737)."""
        assert TEST_NET_1_IP.startswith("192.0.2.")
        assert TEST_NET_2_IP.startswith("198.51.100.")
        assert TEST_NET_3_IP.startswith("203.0.113.")
        assert not TEST_NET_1_IP.startswith("198.18.")
        assert not TEST_NET_2_IP.startswith("198.18.")
        assert not TEST_NET_3_IP.startswith("198.18.")
        assert TEST_NET_1_IP != "127.0.0.1"
