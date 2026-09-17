"""Targeted tests for BaoStock hardening, fail-fast semantics, and AST import guards (DAV-1009 Card B).

Validates:
1. AST guard: No bypass imports of baostock across tradingagents/, api/, scheduler/, scripts/.
2. Fail-closed: If socket hardening installation fails, subsequent attempts to invoke
   baostock actively fail before bs.login() can be called (call count == 0).
3. Connect failure: Socket connect error cleans up context.default_socket, closes socket,
   and re-raises the original exception without swallowing.
4. EOF handling: Receiving empty bytes (peer disconnect) raises ConnectionResetError,
   closes socket, cleans context, and avoids busy-loop livelock.
5. Per-socket timeout: Uses single-socket timeout; socket.setdefaulttimeout() is never called.
6. Concurrency: Installation lock only protects one-time patch; queries are not serialized.
7. V03 integration: Verifies v03_return_measure metadata/is_st paths use unified baostock_session.
"""
from __future__ import annotations

import ast
import importlib
import os
import socket
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import baostock.common.context as bs_context
import baostock.util.socketutil as bs_socketutil
from tradingagents.dataflows.providers.cn_baostock_provider import (
    DEFAULT_BAOSTOCK_SOCKET_TIMEOUT,
    CnBaoStockProvider,
    _cleanup_context_socket,
    baostock_session,
    ensure_baostock_socket_hardening,
)


@pytest.fixture(autouse=True)
def reset_baostock_module_state():
    """Autouse fixture ensuring complete isolation of module-level globals across all tests.

    Restores:
    - baostock.common.context.default_socket -> None (with socket closed)
    - baostock.util.socketutil.SocketUtil.instance -> None
    - baostock.util.socketutil.SocketUtil.init_flag -> False
    - tradingagents.dataflows.providers.cn_baostock_provider._HARDENING_ERROR -> None
    """
    import baostock.common.context as ctx
    import baostock.util.socketutil as su
    import tradingagents.dataflows.providers.cn_baostock_provider as bp

    _cleanup_context_socket()
    setattr(ctx, "default_socket", None)
    setattr(su.SocketUtil, "instance", None)
    setattr(su.SocketUtil, "init_flag", False)
    bp._HARDENING_ERROR = None

    # Assert clean baseline before test body executes
    assert getattr(ctx, "default_socket", None) is None
    assert su.SocketUtil.instance is None
    assert su.SocketUtil.init_flag is False

    yield

    _cleanup_context_socket()
    setattr(ctx, "default_socket", None)
    setattr(su.SocketUtil, "instance", None)
    setattr(su.SocketUtil, "init_flag", False)
    bp._HARDENING_ERROR = None


class TestBaoStockAstGuard:
    """AST guard verifying that no bypass imports of baostock exist."""

    @staticmethod
    def _find_baostock_imports_in_code(source_code: str, filename: str = "<test>"):
        tree = ast.parse(source_code, filename=filename)
        hits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "baostock" or alias.name.startswith("baostock."):
                        hits.append((node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.module and (node.module == "baostock" or node.module.startswith("baostock.")):
                    hits.append((node.lineno, f"from {node.module} import ..."))
            elif isinstance(node, ast.Call):
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if func_name in ("__import__", "import_module") and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                        if arg0.value == "baostock" or arg0.value.startswith("baostock."):
                            hits.append((node.lineno, f"{func_name}('{arg0.value}')"))
        return hits

    def test_no_bypass_baostock_imports_in_production_trees(self):
        """tradingagents/, api/, scheduler/, scripts/ must not contain bypass imports of baostock."""
        repo_root = Path(__file__).resolve().parent.parent
        target_dirs = ["tradingagents", "api", "scheduler", "scripts"]
        allowed_file = repo_root / "tradingagents" / "dataflows" / "providers" / "cn_baostock_provider.py"

        violations = []
        for target_dir in target_dirs:
            dir_path = repo_root / target_dir
            if not dir_path.exists():
                continue
            for file_path in dir_path.rglob("*.py"):
                if file_path.resolve() == allowed_file.resolve():
                    continue
                try:
                    source = file_path.read_text(encoding="utf-8")
                    hits = self._find_baostock_imports_in_code(source, filename=str(file_path))
                    for lineno, hit in hits:
                        violations.append(f"{file_path.relative_to(repo_root)}:{lineno} -> {hit}")
                except Exception as exc:
                    violations.append(f"Failed to parse {file_path}: {exc}")

        assert not violations, (
            "Found bypass baostock imports outside cn_baostock_provider.py:\n"
            + "\n".join(violations)
        )

    def test_ast_guard_detects_all_direct_and_dynamic_forms(self):
        """Verify the AST guard actively catches both static and dynamic literal import patterns."""
        cases = [
            ("import baostock", True),
            ("import baostock.common.context", True),
            ("import baostock as bs", True),
            ("from baostock import login", True),
            ("from baostock.util import socketutil", True),
            ("__import__('baostock')", True),
            ("__import__('baostock.common')", True),
            ("importlib.import_module('baostock')", True),
            ("importlib.import_module('baostock.login')", True),
            ("import numpy as np", False),
            ("from datetime import datetime", False),
            ("__import__('pandas')", False),
            ("importlib.import_module('os')", False),
        ]

        for code_snippet, should_detect in cases:
            hits = self._find_baostock_imports_in_code(code_snippet)
            if should_detect:
                assert len(hits) >= 1, f"Failed to detect prohibited import pattern: {code_snippet}"
            else:
                assert len(hits) == 0, f"False positive on legitimate pattern: {code_snippet}"


class TestBaoStockHardeningFailClosed:
    """Verification of fail-closed semantics on hardening installation (Scenario S2)."""

    def test_scenario_s2_hardening_installation_failure_blocks_with_zero_login_calls(self, monkeypatch):
        """Scenario S2: When hardening fails to install, bs.login() call count is strictly 0 and __cause__ is original error.

        Assertions:
        1. Verified independent clean initial state (not affected by prior S1 run).
        2. bs.login.call_count == 0 (call path stops before bs.login).
        3. Raised error is new production-side RuntimeError.
        4. __cause__ is the original exception (raise ... from exc).
        """
        import tradingagents.dataflows.providers.cn_baostock_provider as bp
        import baostock as bs

        # Step 1: Explicit reset and confirm clean initial state before test
        _cleanup_context_socket()
        setattr(bs_context, "default_socket", None)
        setattr(bs_socketutil.SocketUtil, "instance", None)
        setattr(bs_socketutil.SocketUtil, "init_flag", False)
        assert getattr(bs_context, "default_socket", None) is None
        assert bs_socketutil.SocketUtil.instance is None
        assert bs_socketutil.SocketUtil.init_flag is False

        synthetic_error = AttributeError("Synthetic socketutil attribute error")
        monkeypatch.setattr(bp, "_HARDENING_INSTALLED", False)
        monkeypatch.setattr(bp, "_HARDENING_ERROR", synthetic_error)

        login_mock = MagicMock()
        monkeypatch.setattr(bs, "login", login_mock)

        # Attempt to run baostock_session
        with pytest.raises(RuntimeError) as exc_info:
            with baostock_session():
                pass

        assert "fail-closed" in str(exc_info.value)
        # S2 assertion a: exception is wrapped into new type RuntimeError
        assert isinstance(exc_info.value, RuntimeError)
        # S2 assertion b: __cause__ is the original exception
        assert exc_info.value.__cause__ is synthetic_error
        # S2 assertion c: bs.login() call count is strictly 0
        assert login_mock.call_count == 0, f"Expected 0 calls to bs.login(), got {login_mock.call_count}"

    def test_provider_bs_method_fail_closed(self, monkeypatch):
        """CnBaoStockProvider._bs() must also fail-closed on hardening installation error."""
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        synthetic_error = RuntimeError("Hardening lock failure")
        monkeypatch.setattr(bp, "_HARDENING_INSTALLED", False)
        monkeypatch.setattr(bp, "_HARDENING_ERROR", synthetic_error)

        provider = CnBaoStockProvider()
        with pytest.raises(RuntimeError) as exc_info:
            provider._bs()

        assert "fail-closed" in str(exc_info.value)
        assert exc_info.value.__cause__ is synthetic_error


class TestBaoStockSocketOperationsHardened:
    """Verification of runtime socket behavior under hardening."""

    @pytest.fixture(autouse=True)
    def ensure_hardened_environment(self):
        """Ensure hardening is installed for test cases and context socket is cleaned up."""
        ensure_baostock_socket_hardening()
        assert getattr(bs_context, "default_socket", None) is None
        assert bs_socketutil.SocketUtil.instance is None
        yield
        _cleanup_context_socket()
        setattr(bs_context, "default_socket", None)
        setattr(bs_socketutil.SocketUtil, "instance", None)
        setattr(bs_socketutil.SocketUtil, "init_flag", False)

    def test_scenario_s1_guardrail_connect_rejection_in_login(self, monkeypatch):
        """Scenario S1: connect rejected by offline guardrail during bs.login().

        Full link verification:
        login (count==1) -> connect (count==1) rejected by guardrail ->
        cleanup (socket closed, context.default_socket is None) ->
        send_msg never entered (count==0, raw send==0) ->
        bare raise propagates original exception unaltered (__cause__ is None).
        """
        import time
        from tests.conftest import OfflineTestGuardrailError
        import baostock as bs
        import baostock.util.socketutil as bs_sockutil

        # Confirm clean initial state before test starts
        assert getattr(bs_context, "default_socket", None) is None
        assert bs_socketutil.SocketUtil.instance is None

        # Spy on bs.login
        login_spy = MagicMock(wraps=bs.login)
        monkeypatch.setattr(bs, "login", login_spy)

        # Spy on connect
        orig_connect = bs_sockutil.SocketUtil.connect
        connect_calls = []

        def spy_connect(self, *args, **kwargs):
            connect_calls.append(self)
            return orig_connect(self, *args, **kwargs)

        monkeypatch.setattr(bs_sockutil.SocketUtil, "connect", spy_connect)

        # Spy on send_msg
        send_msg_spy = MagicMock(wraps=bs_sockutil.send_msg)
        monkeypatch.setattr(bs_sockutil, "send_msg", send_msg_spy)

        # Spy on raw socket send/sendall
        raw_send_spy = MagicMock()
        monkeypatch.setattr(socket.socket, "send", raw_send_spy)
        monkeypatch.setattr(socket.socket, "sendall", raw_send_spy)

        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            with baostock_session():
                pass
        elapsed = time.perf_counter() - t0

        # S1.1: bs.login was invoked (call_count == 1)
        assert login_spy.call_count == 1, f"Expected 1 call to bs.login(), got {login_spy.call_count}"

        # S1.2: connect was invoked inside login (call_count == 1)
        assert len(connect_calls) == 1, f"Expected 1 call to connect(), got {len(connect_calls)}"

        # S1.3: send_msg was NEVER invoked (0 send calls)
        assert send_msg_spy.call_count == 0, f"Expected 0 calls to send_msg(), got {send_msg_spy.call_count}"

        # S1.4: Raw socket send/sendall was NEVER invoked (0 raw send calls)
        assert raw_send_spy.call_count == 0, f"Expected 0 raw socket sends, got {raw_send_spy.call_count}"

        # S1.5: context.default_socket is cleaned up to None immediately
        assert getattr(bs_context, "default_socket", None) is None

        # S1.6: Original exception type is preserved (bare raise, not wrapped)
        assert isinstance(exc_info.value, OfflineTestGuardrailError)
        assert exc_info.value.__cause__ is None

        # S1.7: Elapsed time is < 0.1s, far below 45s
        assert elapsed < 0.1, f"Connect failure took too long: {elapsed:.3f}s"

    def test_offline_guardrail_connect_failure_immediately_leaves_clean_context(self):
        """Under real test guardrail, SocketUtil().connect() must raise OfflineTestGuardrailError,

        leave context.default_socket as None, and complete in milliseconds.
        """
        import time
        from tests.conftest import OfflineTestGuardrailError

        # Initial state: clean
        setattr(bs_context, "default_socket", None)

        sock_util = bs_socketutil.SocketUtil()
        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            sock_util.connect()
        elapsed = time.perf_counter() - t0

        # Must fail-fast (< 100ms, not 45s or 60s)
        assert elapsed < 0.1, f"Connect took too long: {elapsed:.3f}s"
        # Must preserve original exception
        assert "OfflineTestGuardrail" in str(exc_info.value)
        # Context must be None (not assigned the unconnected socket)
        assert getattr(bs_context, "default_socket", None) is None

    def test_context_socket_never_written_before_connect_succeeds(self, monkeypatch):
        """context.default_socket must NOT be set prior to or upon connect failure."""
        observed_states_during_connect = []

        class MockTrackingSocket:
            def __init__(self, *args, **kwargs):
                self._timeout = None

            def settimeout(self, t):
                self._timeout = t

            def connect(self, addr):
                observed_states_during_connect.append(getattr(bs_context, "default_socket", None))
                raise ConnectionRefusedError("Forced connect rejection")

            def close(self):
                pass

        monkeypatch.setattr(socket, "socket", MockTrackingSocket)
        setattr(bs_context, "default_socket", None)

        sock_util = bs_socketutil.SocketUtil()
        with pytest.raises(ConnectionRefusedError):
            sock_util.connect()

        # During connect, context.default_socket was None
        assert observed_states_during_connect == [None]
        # After failure, context.default_socket remains None
        assert getattr(bs_context, "default_socket", None) is None

    def test_connect_failure_cleans_context_and_reraises(self, monkeypatch):
        """When socket.connect fails, default_socket must be None and original error re-raised."""
        mock_sock_instance = MagicMock(spec=socket.socket)
        expected_error = ConnectionRefusedError("Connection refused by target test-endpoint")
        mock_sock_instance.connect.side_effect = expected_error

        def fake_socket_constructor(family, type_):
            return mock_sock_instance

        monkeypatch.setattr(socket, "socket", fake_socket_constructor)
        setattr(bs_context, "default_socket", "leftover_marker")

        sock_util = bs_socketutil.SocketUtil()
        with pytest.raises(ConnectionRefusedError) as exc_info:
            sock_util.connect()

        assert exc_info.value is expected_error
        # context.default_socket must be cleaned to None
        assert getattr(bs_context, "default_socket", None) is None
        # Socket close must have been called
        assert mock_sock_instance.close.call_count >= 1

    def test_connect_success_sets_context_default_socket(self, monkeypatch):
        """When socket.connect succeeds, default_socket is assigned."""
        mock_sock_instance = MagicMock(spec=socket.socket)
        mock_sock_instance.connect.return_value = None

        def fake_socket_constructor(family, type_):
            return mock_sock_instance

        monkeypatch.setattr(socket, "socket", fake_socket_constructor)

        sock_util = bs_socketutil.SocketUtil()
        sock_util.connect()

        assert getattr(bs_context, "default_socket", None) is mock_sock_instance

    def test_send_msg_eof_breaks_loop_cleanly(self):
        """Receiving empty bytes (EOF) must raise ConnectionResetError and break loop immediately."""
        mock_sock = MagicMock(spec=socket.socket)
        # Return empty bytes to simulate EOF on first recv
        mock_sock.recv.return_value = b""
        setattr(bs_context, "default_socket", mock_sock)

        with pytest.raises(ConnectionResetError) as exc_info:
            bs_socketutil.send_msg("test_eof_message")

        assert "EOF" in str(exc_info.value)
        assert getattr(bs_context, "default_socket", None) is None
        assert mock_sock.close.call_count >= 1

    def test_send_msg_when_default_socket_is_none(self):
        """When default_socket is None, send_msg must raise ConnectionError rather than return None."""
        setattr(bs_context, "default_socket", None)
        with pytest.raises(ConnectionError) as exc_info:
            bs_socketutil.send_msg("test_no_socket")

        assert "default_socket is None" in str(exc_info.value)

    def test_single_socket_timeout_no_setdefaulttimeout(self, monkeypatch):
        """Hardening must configure per-socket timeout without calling socket.setdefaulttimeout."""
        setdefaulttimeout_spy = MagicMock()
        monkeypatch.setattr(socket, "setdefaulttimeout", setdefaulttimeout_spy)

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.connect.return_value = None
        monkeypatch.setattr(socket, "socket", lambda f, t: mock_sock)

        sock_util = bs_socketutil.SocketUtil()
        sock_util.connect()

        # socket.setdefaulttimeout must NEVER be called
        assert setdefaulttimeout_spy.call_count == 0
        # per-socket settimeout must be called on mock_sock
        mock_sock.settimeout.assert_called_with(DEFAULT_BAOSTOCK_SOCKET_TIMEOUT)

    def test_no_global_request_lock(self):
        """Verifies that _INSTALL_LOCK is not held during operations."""
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        # Verify _INSTALL_LOCK is currently unlocked
        assert not bp._INSTALL_LOCK.locked()

        # Acquire lock in thread to test non-blocking nature for other threads
        acquired = bp._INSTALL_LOCK.acquire(blocking=False)
        assert acquired is True
        try:
            # Another thread trying to ensure hardening (when already installed) does not block
            def worker():
                ensure_baostock_socket_hardening()

            t = threading.Thread(target=worker)
            t.start()
            t.join(timeout=1.0)
            assert not t.is_alive()
        finally:
            bp._INSTALL_LOCK.release()


class TestV03SessionIntegration:
    """Verify v03 return measurement engine uses unified baostock_session and propagates NetworkAccessDeniedError."""

    def test_v03_query_stock_basic_uses_baostock_session(self, monkeypatch):
        """Verify v03 query_stock_basic invokes baostock_session on fallback."""
        from contextlib import contextmanager
        from tradingagents.eval.v03_return_measure import VendorPriceDataProvider
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        session_calls = []

        @contextmanager
        def mock_baostock_session():
            session_calls.append("called")
            mock_bs = MagicMock()
            mock_rs = MagicMock()
            mock_rs.error_code = "0"
            mock_rs.next.return_value = True
            mock_rs.fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]
            mock_rs.get_row_data.return_value = ["sz.000001", "平安银行", "1991-04-03", "", "1", "1"]
            mock_bs.query_stock_basic.return_value = mock_rs
            yield mock_bs

        monkeypatch.setattr(bp, "baostock_session", mock_baostock_session)

        provider = VendorPriceDataProvider(Path("/tmp/nonexistent_db.sqlite"))
        provider._meta_cache.clear()
        # Force global metadata cache miss to drive into baostock fallback
        provider._ensure_global_metadata = lambda: {}
        res = provider._get_stock_metadata("000001.SZ")

        # Must have invoked baostock_session and parsed result
        assert len(session_calls) == 1
        assert res == {"name": "平安银行", "list_date": "1991-04-03"}

    def test_v03_is_st_uses_baostock_session(self, monkeypatch):
        """Verify v03 is_st invokes baostock_session."""
        from contextlib import contextmanager
        from tradingagents.eval.v03_return_measure import VendorPriceDataProvider
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        st_calls = []

        @contextmanager
        def mock_baostock_session():
            st_calls.append("called")
            mock_bs = MagicMock()
            mock_rs = MagicMock()
            mock_rs.error_code = "0"
            mock_rs.next.side_effect = [True, False]
            mock_rs.get_row_data.return_value = ["2026-01-05", "0"]
            mock_bs.query_history_k_data_plus.return_value = mock_rs
            yield mock_bs

        monkeypatch.setattr(bp, "baostock_session", mock_baostock_session)

        provider = VendorPriceDataProvider(Path("/tmp/nonexistent_db.sqlite"))
        provider._meta_cache["000001.SZ"] = {"name": "平安银行", "list_date": "1991-04-03"}
        provider._st_cache.clear()

        res = provider.is_st("000001.SZ", "2026-01-05")
        assert len(st_calls) == 1
        assert res is False

    def test_v03_paths_propagate_network_access_denied_without_swallowing(self, monkeypatch):
        """v03 query_stock_basic and is_st must NOT swallow NetworkAccessDeniedError or OfflineTestGuardrailError."""
        from contextlib import contextmanager
        from tests.conftest import OfflineTestGuardrailError
        from tradingagents.dataflows.interface import NetworkAccessDeniedError
        from tradingagents.eval.v03_return_measure import VendorPriceDataProvider
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        @contextmanager
        def denied_session():
            raise NetworkAccessDeniedError("Refusal by offline security policy")
            yield  # pragma: no cover

        monkeypatch.setattr(bp, "baostock_session", denied_session)

        provider = VendorPriceDataProvider(Path("/tmp/nonexistent_db.sqlite"))
        provider._meta_cache.clear()
        provider._st_cache.clear()
        provider._ensure_global_metadata = lambda: {}

        # 1. _get_stock_metadata must re-raise NetworkAccessDeniedError, not return None
        with pytest.raises(NetworkAccessDeniedError):
            provider._get_stock_metadata("000001.SZ")

        # 2. is_st must re-raise NetworkAccessDeniedError, not return None
        provider._meta_cache["000001.SZ"] = {"name": "平安银行", "list_date": "1991-04-03"}
        with pytest.raises(NetworkAccessDeniedError):
            provider.is_st("000001.SZ", "2026-01-05")

        # 3. Also verify OfflineTestGuardrailError is re-raised
        @contextmanager
        def guardrail_session():
            raise OfflineTestGuardrailError("Audit hook blocked baostock socket")
            yield  # pragma: no cover

        monkeypatch.setattr(bp, "baostock_session", guardrail_session)
        provider._meta_cache.clear()
        with pytest.raises(OfflineTestGuardrailError):
            provider._get_stock_metadata("000001.SZ")
        provider._meta_cache["000001.SZ"] = {"name": "平安银行", "list_date": "1991-04-03"}
        with pytest.raises(OfflineTestGuardrailError):
            provider.is_st("000001.SZ", "2026-01-05")


class TestNetworkAccessDeniedPropagation:
    """Targeted tests for NetworkAccessDeniedError non-retry fast propagation (DAV-1009 B2)."""

    def test_exception_inheritance_hierarchy(self):
        """OfflineTestGuardrailError must inherit from production NetworkAccessDeniedError and RuntimeError."""
        from tests.conftest import OfflineTestGuardrailError
        from tradingagents.dataflows.interface import NetworkAccessDeniedError

        assert issubclass(NetworkAccessDeniedError, RuntimeError)
        assert issubclass(OfflineTestGuardrailError, NetworkAccessDeniedError)
        assert issubclass(OfflineTestGuardrailError, RuntimeError)

    def test_route_to_vendor_denied_error_immediate_propagation_no_retry_no_fallback(self, monkeypatch):
        """NetworkAccessDeniedError must immediately propagate with 1 attempt and 0 retries or vendor fallback."""
        from tradingagents.dataflows.interface import NetworkAccessDeniedError, route_to_vendor
        from tradingagents.dataflows.providers.registry import DataProviderRegistry
        from tradingagents.dataflows.providers.china_equity_provider import CnStubProvider
        import tradingagents.dataflows.interface as iface

        attempt_counts = {"count": 0}

        class MockDeniedProvider(CnStubProvider):
            @property
            def name(self) -> str:
                return "cn_baostock"

            def get_stock_data(self, *args, **kwargs):
                attempt_counts["count"] += 1
                raise NetworkAccessDeniedError("Access to exchange blocked by security policy")

        reg = DataProviderRegistry()
        reg.register(MockDeniedProvider())
        monkeypatch.setattr(iface, "_registry", reg)
        monkeypatch.setattr(iface, "get_vendor", lambda cat, meth=None: "cn_baostock")

        with pytest.raises(NetworkAccessDeniedError) as exc_info:
            route_to_vendor("get_stock_data", "600519", "2026-01-01", "2026-01-05")

        assert "blocked by security policy" in str(exc_info.value)
        # Rigid assertion: exactly 1 attempt, no retry, no fallback
        assert attempt_counts["count"] == 1

    def test_route_to_vendor_offline_guardrail_immediate_propagation(self, monkeypatch):
        """OfflineTestGuardrailError must be caught by NetworkAccessDeniedError branch and fail fast."""
        from tests.conftest import OfflineTestGuardrailError
        from tradingagents.dataflows.interface import route_to_vendor
        from tradingagents.dataflows.providers.registry import DataProviderRegistry
        from tradingagents.dataflows.providers.china_equity_provider import CnStubProvider
        import tradingagents.dataflows.interface as iface

        attempts = []

        class MockGuardrailProvider(CnStubProvider):
            @property
            def name(self) -> str:
                return "cn_baostock"

            def get_stock_data(self, *args, **kwargs):
                attempts.append("attempt")
                raise OfflineTestGuardrailError("Audit hook blocked outbound socket connect")

        reg = DataProviderRegistry()
        reg.register(MockGuardrailProvider())
        monkeypatch.setattr(iface, "_registry", reg)
        monkeypatch.setattr(iface, "get_vendor", lambda cat, meth=None: "cn_baostock")

        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            route_to_vendor("get_stock_data", "600519", "2026-01-01", "2026-01-05")

        assert "Audit hook blocked" in str(exc_info.value)
        assert len(attempts) == 1

    def test_normal_timeout_preserves_retry_and_fallback(self, monkeypatch):
        """Standard TimeoutError must preserve its retry budget and fallback semantics."""
        from tradingagents.dataflows.interface import route_to_vendor
        from tradingagents.dataflows.providers.registry import DataProviderRegistry
        from tradingagents.dataflows.providers.china_equity_provider import CnStubProvider
        from tradingagents.dataflows.providers import ProviderResourcePolicy
        import tradingagents.dataflows.interface as iface

        provider_calls = {"vendor_a": 0, "vendor_b": 0}

        class TimeoutProvider(CnStubProvider):
            @property
            def name(self) -> str:
                return "vendor_a"

            def get_stock_data(self, *args, **kwargs):
                provider_calls["vendor_a"] += 1
                raise TimeoutError("Slow upstream network response on vendor_a")

        class FallbackSuccessProvider(CnStubProvider):
            @property
            def name(self) -> str:
                return "vendor_b"

            def get_stock_data(self, *args, **kwargs):
                provider_calls["vendor_b"] += 1
                return "SUCCESS_DATA"

        reg = DataProviderRegistry()
        policy = ProviderResourcePolicy(timeout_seconds=1.0, max_retries=1, max_concurrency=1)
        reg.register(TimeoutProvider(), resource_policy=policy)
        reg.register(FallbackSuccessProvider(), resource_policy=policy)
        monkeypatch.setattr(iface, "_registry", reg)
        monkeypatch.setattr(iface, "get_vendor", lambda cat, meth=None: "vendor_a,vendor_b")

        result = route_to_vendor("get_stock_data", "600519", "2026-01-01", "2026-01-05")

        assert result == "SUCCESS_DATA"
        # vendor_a max_retries is 1, so vendor_a should have 2 attempts (initial + 1 retry)
        assert provider_calls["vendor_a"] == 2
        # Then fell back to vendor_b
        assert provider_calls["vendor_b"] == 1

    def test_baostock_login_failure_raises_notimplemented_for_zero_retry_fallback(self, monkeypatch):
        """Login failure must raise NotImplementedError to trigger immediate vendor fallback without retry."""
        import baostock as bs
        from tradingagents.dataflows.providers.cn_baostock_provider import baostock_session

        mock_lg = MagicMock()
        mock_lg.error_code = "1"
        mock_lg.error_msg = "invalid user or password"
        monkeypatch.setattr(bs, "login", lambda: mock_lg)

        with pytest.raises(NotImplementedError) as exc_info:
            with baostock_session():
                pass

        assert "baostock login failed: invalid user or password" in str(exc_info.value)

    def test_e2e_guardrail_rejection_fast_timing_and_cleanup(self, monkeypatch):
        """End-to-end under real test guardrail: connect rejected, 0 send, completes in <0.1s, socket is None."""
        import time
        from tests.conftest import OfflineTestGuardrailError
        from tradingagents.dataflows.interface import route_to_vendor
        import tradingagents.dataflows.interface as iface

        monkeypatch.setattr(iface, "get_config", lambda: {"core_stock_apis": "cn_baostock"})

        t0 = time.perf_counter()
        with pytest.raises(OfflineTestGuardrailError) as exc_info:
            route_to_vendor("get_stock_data", "600519", "2026-01-01", "2026-01-05")
        elapsed = time.perf_counter() - t0

        # Timing must be milliseconds, far below 45.0s
        assert elapsed < 0.2, f"Guardrail rejection took too long: {elapsed:.3f}s"
        assert "OfflineTestGuardrail" in str(exc_info.value)
        # Global default_socket must be None
        assert getattr(bs_context, "default_socket", None) is None


class TestScenarioIsolationCrossVerification:
    """Rigid verification of cross-test isolation for S1 and S2 (DAV-1009 Step 3).

    Verifies:
    1. S1 runs cleanly in an independent fresh subprocess.
    2. S2 runs cleanly in an independent fresh subprocess.
    3. Order reversal invariance: Running S1 -> S2 -> S2 -> S1 within the same process
       under explicit state resets never leaks or causes false greens.
    """

    def test_scenario_s1_in_isolated_subprocess(self):
        """Step 3.1: Scenario S1 executed in a completely fresh subprocess."""
        import subprocess

        cmd = [
            sys.executable,
            "-c",
            """
import baostock as bs
import baostock.util.socketutil as bs_sockutil
import baostock.common.context as bs_context
from unittest.mock import MagicMock
from tests.conftest import OfflineTestGuardrailError
from tradingagents.dataflows.providers.cn_baostock_provider import baostock_session, ensure_baostock_socket_hardening

ensure_baostock_socket_hardening()
login_spy = MagicMock(wraps=bs.login)
bs.login = login_spy
send_msg_spy = MagicMock(wraps=bs_sockutil.send_msg)
bs_sockutil.send_msg = send_msg_spy

assert getattr(bs_context, "default_socket", None) is None
assert bs_sockutil.SocketUtil.instance is None

try:
    with baostock_session():
        pass
except OfflineTestGuardrailError as exc:
    assert login_spy.call_count == 1
    assert send_msg_spy.call_count == 0
    assert getattr(bs_context, "default_socket", None) is None
    assert exc.__cause__ is None
    print("S1_SUBPROCESS_OK")
""",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "S1_SUBPROCESS_OK" in res.stdout

    def test_scenario_s2_in_isolated_subprocess(self):
        """Step 3.2: Scenario S2 executed in a completely fresh subprocess."""
        import subprocess

        cmd = [
            sys.executable,
            "-c",
            """
import baostock as bs
import baostock.util.socketutil as bs_sockutil
import baostock.common.context as bs_context
from unittest.mock import MagicMock
import tradingagents.dataflows.providers.cn_baostock_provider as bp
from tradingagents.dataflows.providers.cn_baostock_provider import baostock_session

# Reset to clean initial state
setattr(bs_context, "default_socket", None)
setattr(bs_sockutil.SocketUtil, "instance", None)
setattr(bs_sockutil.SocketUtil, "init_flag", False)
bp._HARDENING_INSTALLED = False
synthetic_error = AttributeError("Synthetic socketutil attribute error")
bp._HARDENING_ERROR = synthetic_error

login_mock = MagicMock()
bs.login = login_mock

assert getattr(bs_context, "default_socket", None) is None
assert bs_sockutil.SocketUtil.instance is None
assert bp._HARDENING_INSTALLED is False

try:
    with baostock_session():
        pass
except RuntimeError as exc:
    assert login_mock.call_count == 0
    assert exc.__cause__ is synthetic_error
    print("S2_SUBPROCESS_OK")
""",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "S2_SUBPROCESS_OK" in res.stdout

    def test_bidirectional_order_isolation_s1_s2_s2_s1(self):
        """Step 3.3: Order-reversal test (S1->S2->S2->S1) in same process under resets."""
        import subprocess

        cmd = [
            sys.executable,
            "-c",
            """
import baostock as bs
import baostock.util.socketutil as bs_sockutil
import baostock.common.context as bs_context
from unittest.mock import MagicMock
from tests.conftest import OfflineTestGuardrailError
import tradingagents.dataflows.providers.cn_baostock_provider as bp
from tradingagents.dataflows.providers.cn_baostock_provider import (
    baostock_session,
    ensure_baostock_socket_hardening,
    _cleanup_context_socket,
)

orig_login = bs.login
orig_send_msg = bs_sockutil.send_msg

def reset_all():
    _cleanup_context_socket()
    setattr(bs_context, "default_socket", None)
    setattr(bs_sockutil.SocketUtil, "instance", None)
    setattr(bs_sockutil.SocketUtil, "init_flag", False)
    bs.login = orig_login
    bs_sockutil.send_msg = orig_send_msg
    bp._HARDENING_INSTALLED = False
    bp._HARDENING_ERROR = None

def run_s1():
    reset_all()
    ensure_baostock_socket_hardening()

    login_spy = MagicMock(wraps=bs.login)
    bs.login = login_spy
    send_msg_spy = MagicMock(wraps=bs_sockutil.send_msg)
    bs_sockutil.send_msg = send_msg_spy

    try:
        with baostock_session():
            pass
    except OfflineTestGuardrailError as exc:
        assert login_spy.call_count == 1
        assert send_msg_spy.call_count == 0
        assert getattr(bs_context, "default_socket", None) is None
        assert exc.__cause__ is None
        reset_all()
        return True
    reset_all()
    return False

def run_s2():
    reset_all()
    synthetic_error = AttributeError("Synthetic socketutil attribute error")
    bp._HARDENING_ERROR = synthetic_error

    login_mock = MagicMock()
    bs.login = login_mock

    try:
        with baostock_session():
            pass
    except RuntimeError as exc:
        assert login_mock.call_count == 0
        assert exc.__cause__ is synthetic_error
        reset_all()
        return True
    reset_all()
    return False

# Execute order S1 -> S2 -> S2 -> S1
assert run_s1() is True
assert run_s2() is True
assert run_s2() is True
assert run_s1() is True

print("BIDIRECTIONAL_ISOLATION_OK")
""",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert "BIDIRECTIONAL_ISOLATION_OK" in res.stdout
