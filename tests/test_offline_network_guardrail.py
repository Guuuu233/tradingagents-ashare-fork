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


class TestBaostockHardeningVerification:
    """Explicit verification of baostock socket hardening state and robustness (DAV-1003 / B1 / Gate 6)."""

    def test_baostock_hardening_state_and_callables(self):
        """Hardening must set the state flag and replace baostock socket/send_msg entrypoints."""
        import baostock.util.socketutil as bssock
        from tradingagents.dataflows.providers.cn_baostock_provider import (
            ensure_baostock_socket_hardening,
            is_baostock_hardened,
        )

        success = ensure_baostock_socket_hardening()
        assert success is True
        assert is_baostock_hardened() is True
        assert bssock.SocketUtil.connect.__name__ == "safe_connect"
        assert bssock.send_msg.__name__ == "safe_send_msg"

    def test_baostock_hardening_failure_is_logged_and_detectable(self, monkeypatch, caplog):
        """When baostock internals change (AttributeError), hardening logs warning and reports False."""
        import logging
        import baostock.util.socketutil as bssock
        import tradingagents.dataflows.providers.cn_baostock_provider as pmod

        monkeypatch.setattr(pmod, "_BAOSTOCK_HARDENED", False)
        monkeypatch.delattr(bssock, "SocketUtil", raising=False)

        with caplog.at_level(logging.WARNING):
            res = pmod.ensure_baostock_socket_hardening()

        assert res is False
        assert pmod.is_baostock_hardened() is False
        assert any("Failed to apply baostock socket hardening" in rec.message for rec in caplog.records)

    def test_baostock_context_cleanup_resets_socket_and_singleton(self):
        """_cleanup_baostock_context must reset both default_socket and SocketUtil.instance."""
        import baostock.common.context as conx
        import baostock.util.socketutil as sockutil
        from tradingagents.dataflows.providers.cn_baostock_provider import _cleanup_baostock_context

        class DummySock:
            closed = False
            def close(self):
                self.closed = True

        dummy = DummySock()
        conx.default_socket = dummy
        sockutil.SocketUtil.instance = "dummy_instance"

        _cleanup_baostock_context()

        assert dummy.closed is True
        assert getattr(conx, "default_socket", None) is None
        assert getattr(sockutil.SocketUtil, "instance", None) is None

    def test_session_does_not_pollute_process_socket_default_timeout(self, monkeypatch):
        """_session must not mutate process-level socket.getdefaulttimeout (DAV-1003 / B3)."""
        import socket
        from tradingagents.dataflows.providers.cn_baostock_provider import CnBaoStockProvider

        orig_timeout = socket.getdefaulttimeout()
        provider = CnBaoStockProvider()

        class MockBS:
            def login(self):
                class LoginResult:
                    error_code = "0"
                    error_msg = "ok"
                return LoginResult()
            def logout(self):
                pass

        monkeypatch.setattr(provider, "_bs", lambda: MockBS())

        with provider._session(timeout=3.14):
            assert socket.getdefaulttimeout() == orig_timeout

        assert socket.getdefaulttimeout() == orig_timeout

    def test_baostock_hardening_failure_causes_fail_closed_and_retryable_refusal(self, monkeypatch):
        """Hardening failure must fail closed before login and propagate as retryable refusal in backfill (DAV-1003)."""
        from unittest.mock import MagicMock
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import baostock as bs
        import baostock.util.socketutil as bssock
        from api.database import Base, HistoricalCaseDB
        import tradingagents.dataflows.interface as iface
        import tradingagents.dataflows.providers.cn_baostock_provider as pmod
        from tradingagents.knowledge.historical_cases import (
            calculate_t1_return,
            is_terminal_refusal_code,
            backfill_pending_cases,
            DATA_MISSING_PLACEHOLDER,
            _case_refusal,
        )

        # Spy / mock on bs.login to assert call_count == 0
        mock_login = MagicMock()
        monkeypatch.setattr(bs, "login", mock_login)

        # Simulate hardening failure
        monkeypatch.setattr(pmod, "_BAOSTOCK_HARDENED", False)
        monkeypatch.delattr(bssock, "SocketUtil", raising=False)

        provider = pmod.CnBaoStockProvider()

        # (a) Fail-closed assertions: _bs() and _session() must fail before calling login
        with pytest.raises(NotImplementedError, match="baostock 硬化失败，拒绝使用未硬化客户端以避免 EOF 活锁"):
            provider._bs()

        with pytest.raises(NotImplementedError, match="baostock 硬化失败，拒绝使用未硬化客户端以避免 EOF 活锁"):
            with provider._session():
                pass

        assert mock_login.call_count == 0

        # (b) End-to-end propagation: route_to_vendor routes get_stock_data to real cn_baostock
        monkeypatch.setattr(iface, "_resolve_vendor_chain", lambda method, configured: ["cn_baostock"])

        eval_date, return_pct, refusal = calculate_t1_return("600519", "2024-05-10", "2024-05-13")

        assert return_pct is None
        assert refusal is not None
        assert refusal.code == "vendor_refuse"
        assert refusal.terminal is False
        assert is_terminal_refusal_code(refusal.code) is False
        assert "硬化失败" in refusal.reason
        assert mock_login.call_count == 0

        # End-to-end backfill queue: case must remain in retryable backfill queue
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            case = HistoricalCaseDB(
                id="case-fail-closed-1",
                symbol="600519",
                industry="liquor",
                trade_date="2024-05-10",
                eval_date="2024-05-13",
                decision="BUY",
                direction="看多",
                confidence=70,
                actual_outcome=DATA_MISSING_PLACEHOLDER,
                actual_change_pct=None,
                is_error=None,
            )
            session.add(case)
            session.commit()

            stats1 = backfill_pending_cases(session, as_of="2024-05-20")
            assert stats1["total_scanned"] == 1
            assert stats1["backfilled"] == 0
            assert stats1["still_missing"] == 1

            session.expire_all()
            persisted = session.query(HistoricalCaseDB).filter_by(id="case-fail-closed-1").one()
            persisted_refusal = _case_refusal(persisted)
            assert persisted_refusal is not None
            assert persisted_refusal.code == "vendor_refuse"
            assert persisted_refusal.terminal is False

            # Subsequent scan must still pick up the case (total_scanned > 0)
            stats2 = backfill_pending_cases(session, as_of="2024-05-20")
            assert stats2["total_scanned"] == 1
            assert stats2["backfilled"] == 0
            assert mock_login.call_count == 0
        finally:
            session.close()
            engine.dispose()

    def test_static_guard_no_direct_baostock_import_outside_accessor(self):
        """AST static guard: ban direct baostock imports in tradingagents/, api/, scheduler/, and scripts/ outside accessor."""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        scan_dirs = [repo_root / d for d in ["tradingagents", "api", "scheduler", "scripts"]]
        target_file = (repo_root / "tradingagents/dataflows/providers/cn_baostock_provider.py").resolve()
        allowed_scopes = {"ensure_baostock_socket_hardening", "_cleanup_baostock_context", "get_hardened_baostock"}

        violations = []

        def is_baostock_module(mod_name):
            return mod_name == "baostock" or (mod_name is not None and mod_name.startswith("baostock."))

        class BaostockImportVisitor(ast.NodeVisitor):
            def __init__(self, file_path):
                self.file_path = file_path
                self.scope_stack = []

            def is_allowed(self):
                if self.file_path == target_file:
                    return bool(self.scope_stack and self.scope_stack[-1] in allowed_scopes)
                return False

            def visit_FunctionDef(self, node):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_AsyncFunctionDef(self, node):
                self.scope_stack.append(node.name)
                self.generic_visit(node)
                self.scope_stack.pop()

            def visit_Import(self, node):
                for alias in node.names:
                    if is_baostock_module(alias.name):
                        if not self.is_allowed():
                            scope_desc = self.scope_stack[-1] if self.scope_stack else "<module>"
                            violations.append(
                                f"{self.file_path.relative_to(repo_root)}:{node.lineno} in {scope_desc}: import {alias.name}"
                            )
                self.generic_visit(node)

            def visit_ImportFrom(self, node):
                if is_baostock_module(node.module):
                    if not self.is_allowed():
                        scope_desc = self.scope_stack[-1] if self.scope_stack else "<module>"
                        violations.append(
                            f"{self.file_path.relative_to(repo_root)}:{node.lineno} in {scope_desc}: from {node.module} import ..."
                        )
                self.generic_visit(node)

            def visit_Call(self, node):
                mod_name = None
                if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        mod_name = node.args[0].value
                elif isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        mod_name = node.args[0].value

                if mod_name and is_baostock_module(mod_name):
                    if not self.is_allowed():
                        scope_desc = self.scope_stack[-1] if self.scope_stack else "<module>"
                        violations.append(
                            f"{self.file_path.relative_to(repo_root)}:{node.lineno} in {scope_desc}: dynamic import {mod_name}"
                        )
                self.generic_visit(node)

        for d in scan_dirs:
            if not d.exists():
                continue
            for py_file in d.rglob("*.py"):
                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                    visitor = BaostockImportVisitor(py_file.resolve())
                    visitor.visit(tree)
                except Exception as exc:
                    violations.append(f"Parse error in {py_file.relative_to(repo_root)}: {exc}")

        assert not violations, (
            "Found unauthorized baostock imports outside get_hardened_baostock accessor:\n"
            + "\n".join(violations)
        )
