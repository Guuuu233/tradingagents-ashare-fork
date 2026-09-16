"""Comprehensive tests for offline network guardrail and socket isolation (DAV-995 / DAV-979).

Validates:
1. Outbound socket connection attempts to external IPs and domains are intercepted and fail-fast with OfflineTestGuardrailError.
2. UDP outbound datagrams to external addresses are intercepted.
3. socket.create_connection is intercepted without network/DNS delay.
4. Local loopback communication (127.0.0.1, localhost), AF_UNIX sockets, and FastAPI TestClient are completely unaffected.
5. Tests marked with @pytest.mark.network are exempted and allowed normal outbound network behavior.
6. Context managers for selective enabling/disabling function properly.
"""
from __future__ import annotations

import os
import socket
import tempfile
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import (
    OfflineTestGuardrailError,
    _ORIG_SOCKET_CONNECT,
    _is_local_or_loopback,
    disable_offline_network_guard,
    enable_offline_network_guard,
    is_offline_network_guard_active,
    offline_guard_disabled,
    offline_guard_enabled,
)


class TestAddressAllowlistUnit:
    """Unit tests for loopback and local address identification logic."""

    def test_loopback_ipv4_and_names(self):
        assert _is_local_or_loopback(("127.0.0.1", 80)) is True
        assert _is_local_or_loopback(("127.0.0.2", 8080)) is True
        assert _is_local_or_loopback(("localhost", 8000)) is True
        assert _is_local_or_loopback(("sub.localhost", 80)) is True
        assert _is_local_or_loopback(("testserver", 80)) is True
        assert _is_local_or_loopback(("0.0.0.0", 80)) is True

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
        assert _is_local_or_loopback(("8.8.8.8", 53)) is False
        assert _is_local_or_loopback(("1.1.1.1", 80)) is False
        assert _is_local_or_loopback(("baostock.com", 80)) is False
        assert _is_local_or_loopback(("example.com", 80)) is False
        assert _is_local_or_loopback(("192.168.1.1", 80)) is False
        assert _is_local_or_loopback(("10.0.0.1", 80)) is False
        assert _is_local_or_loopback(("172.16.0.1", 80)) is False
        assert _is_local_or_loopback(("2001:4860:4860::8888", 53)) is False


class TestOfflineGuardrailInterception:
    """Verification of runtime socket interception and fail-fast semantics."""

    def test_tcp_connect_external_ip_blocked_fail_fast(self):
        """TCP socket.connect to external IP must raise OfflineTestGuardrailError in milliseconds."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            t0 = time.perf_counter()
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.connect(("8.8.8.8", 53))
            elapsed = time.perf_counter() - t0

            assert elapsed < 0.05, f"Fail-fast exceeded 50ms: took {elapsed*1000:.2f}ms"
            err_msg = str(exc_info.value)
            assert "8.8.8.8:53" in err_msg
            assert "OfflineTestGuardrail: outbound network connection to 8.8.8.8:53 is disallowed in offline test suite" in err_msg
            assert issubclass(OfflineTestGuardrailError, RuntimeError)
        finally:
            s.close()

    def test_tcp_connect_external_domain_blocked_fail_fast(self):
        """TCP socket.connect to external domain name must fail-fast without network DNS query."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            t0 = time.perf_counter()
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.connect(("baostock.com", 80))
            elapsed = time.perf_counter() - t0

            assert elapsed < 0.05, f"Fail-fast exceeded 50ms: took {elapsed*1000:.2f}ms"
            assert "baostock.com:80" in str(exc_info.value)
        finally:
            s.close()

    def test_tcp_connect_ex_external_ip_blocked(self):
        """TCP socket.connect_ex to external destination must also be intercepted."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.connect_ex(("8.8.8.8", 443))
            assert "8.8.8.8:443" in str(exc_info.value)
        finally:
            s.close()

    def test_socket_create_connection_blocked_fail_fast(self):
        """socket.create_connection to external address must be intercepted immediately."""
        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            socket.create_connection(("8.8.8.8", 53), timeout=1)
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05
        assert "8.8.8.8:53" in str(exc_info.value)

        # Domain test
        with pytest.raises(OfflineTestGuardrailError) as exc_info_domain:
            socket.create_connection(("example.com", 80), timeout=1)
        assert "example.com:80" in str(exc_info_domain.value)

    def test_udp_sendto_external_ip_blocked(self):
        """UDP socket.sendto to external destination must be intercepted."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 2 arguments: (data, address)
            with pytest.raises(OfflineTestGuardrailError) as exc_info1:
                s.sendto(b"ping", ("8.8.8.8", 53))
            assert "8.8.8.8:53" in str(exc_info1.value)

            # 3 arguments: (data, flags, address)
            with pytest.raises(OfflineTestGuardrailError) as exc_info2:
                s.sendto(b"ping", 0, ("8.8.8.8", 53))
            assert "8.8.8.8:53" in str(exc_info2.value)

            # Keyword argument: address=...
            with pytest.raises(OfflineTestGuardrailError) as exc_info3:
                s.sendto(b"ping", address=("8.8.8.8", 53))
            assert "8.8.8.8:53" in str(exc_info3.value)
        finally:
            s.close()

    def test_lan_and_private_ips_blocked(self):
        """Outbound calls to LAN / private ranges (192.168.x, 10.x, 172.16.x) must be blocked."""
        for private_ip in ("192.168.1.100", "10.0.1.25", "172.16.0.50"):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                with pytest.raises(OfflineTestGuardrailError):
                    s.connect((private_ip, 80))
            finally:
                s.close()

    def test_existing_socket_io_blocked_if_connected_to_external(self, monkeypatch):
        """If a socket was somehow connected to an external host, send/recv are intercepted."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        monkeypatch.setattr(socket.socket, "getpeername", lambda self: ("120.24.225.105", 10030))
        try:
            with pytest.raises(OfflineTestGuardrailError) as exc_info:
                s.send(b"data")
            assert "120.24.225.105:10030" in str(exc_info.value)

            with pytest.raises(OfflineTestGuardrailError) as exc_info2:
                s.recv(1024)
            assert "120.24.225.105:10030" in str(exc_info2.value)
        finally:
            s.close()

    def test_baostock_eof_livelock_prevention(self):
        """Ensure baostock client send_msg raises ConnectionResetError on EOF instead of spinning 100% CPU."""
        import baostock.util.socketutil as bssock
        from tradingagents.dataflows.providers.cn_baostock_provider import ensure_baostock_socket_hardening

        ensure_baostock_socket_hardening(timeout=1.0)

        class MockEOFSocket:
            def settimeout(self, t): pass
            def sendall(self, b): pass
            def recv(self, size): return b""  # EOF!
            def close(self): pass

        bssock.context.default_socket = MockEOFSocket()
        t0 = time.perf_counter()
        with pytest.raises(ConnectionResetError) as exc_info:
            bssock.send_msg("mock_login")
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05, f"EOF liveloop took {elapsed*1000:.2f}ms"
        assert "EOF" in str(exc_info.value)
        assert bssock.context.default_socket is None




class TestLocalLoopbackAndIpcAllowed:
    """Verify that loopback, in-process clients, and IPC continue to work seamlessly."""

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

        client = socket.create_connection(("127.0.0.1", port))
        client.sendall(b"guardrail-test")
        resp = client.recv(1024)
        assert resp == b"pong:guardrail-test"
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
            return {"status": "ok", "offline_guard": is_offline_network_guard_active()}

        client = TestClient(app)
        resp = client.get("/ping")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["offline_guard"] is True


class TestGuardrailControlsAndMarkerExemption:
    """Verify guardrail lifecycle controls and network marker exemption."""

    def test_offline_guard_disabled_context_manager(self):
        """offline_guard_disabled temporarily restores unpatched socket methods."""
        assert is_offline_network_guard_active() is True
        assert socket.socket.connect is not _ORIG_SOCKET_CONNECT

        with offline_guard_disabled():
            assert is_offline_network_guard_active() is False
            assert socket.socket.connect is _ORIG_SOCKET_CONNECT

        assert is_offline_network_guard_active() is True
        assert socket.socket.connect is not _ORIG_SOCKET_CONNECT

    def test_offline_guard_enabled_context_manager(self):
        """offline_guard_enabled can explicitly ensure guardrail is active."""
        with offline_guard_disabled():
            assert is_offline_network_guard_active() is False
            with offline_guard_enabled():
                assert is_offline_network_guard_active() is True
            assert is_offline_network_guard_active() is False

    @pytest.mark.network
    def test_network_marker_exempts_test_from_guardrail(self):
        """Tests marked with @pytest.mark.network must have offline guardrail disabled."""
        assert is_offline_network_guard_active() is False
        assert socket.socket.connect is _ORIG_SOCKET_CONNECT
