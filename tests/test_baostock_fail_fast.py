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
    """Verification of fail-closed semantics on hardening installation."""

    def test_hardening_installation_failure_blocks_with_zero_login_calls(self, monkeypatch):
        """When hardening fails to install, bs.login() call count must be strictly ZERO."""
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        # Simulate prior installation failure state
        synthetic_error = RuntimeError("Synthetic socketutil patch failure")
        monkeypatch.setattr(bp, "_HARDENING_INSTALLED", False)
        monkeypatch.setattr(bp, "_HARDENING_ERROR", synthetic_error)

        # Mock baostock.login to spy on calls
        import baostock as bs
        login_mock = MagicMock()
        monkeypatch.setattr(bs, "login", login_mock)

        # Attempt to run baostock_session
        with pytest.raises(RuntimeError) as exc_info:
            with baostock_session():
                pass

        assert "fail-closed" in str(exc_info.value)
        assert "Synthetic socketutil patch failure" in str(exc_info.value)
        # Rigid assertion: bs.login() call count must be strictly 0
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


class TestBaoStockSocketOperationsHardened:
    """Verification of runtime socket behavior under hardening."""

    @pytest.fixture(autouse=True)
    def ensure_hardened_environment(self):
        """Ensure hardening is installed for test cases and context socket is cleaned up."""
        ensure_baostock_socket_hardening()
        _cleanup_context_socket()
        yield
        _cleanup_context_socket()

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
    """Verify v03 return measurement engine uses unified baostock_session."""

    def test_v03_paths_use_baostock_session(self, monkeypatch):
        """Verify v03 query_stock_basic invokes baostock_session."""
        from contextlib import contextmanager
        from tradingagents.eval.v03_return_measure import VendorPriceDataProvider
        import tradingagents.dataflows.providers.cn_baostock_provider as bp

        session_calls = []
        orig_session = bp.baostock_session

        @contextmanager
        def spy_session(*args, **kwargs):
            session_calls.append("called")
            with orig_session(*args, **kwargs) as bs:
                yield bs

        monkeypatch.setattr(bp, "baostock_session", spy_session)

        provider = VendorPriceDataProvider(Path("/tmp/nonexistent_db.sqlite"))
        provider._meta_cache.clear()
        provider._get_stock_metadata("000001.SZ")

        # Must have invoked baostock_session
        assert len(session_calls) >= 1
