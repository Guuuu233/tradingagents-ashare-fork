"""Test fixtures for tests/ directory.

Provides unified date/trade-calendar isolation fixtures to ensure tests
run deterministically on any natural day (weekends, holidays, intraday).
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

CN_TZ = ZoneInfo("Asia/Shanghai")

# A fixed known trading day: a historical Monday (2026-08-17) with post-close timestamp.
FROZEN_TRADE_DATE = "2026-08-17"
FROZEN_TRADE_DATE_OBJ = date(2026, 8, 17)
FROZEN_TRADE_DATETIME = datetime(2026, 8, 17, 16, 0, 0, tzinfo=CN_TZ)


# ============================================================================
# Offline Network Guardrail & Socket Isolation (DAV-979 / DAV-995)
# ============================================================================

class OfflineTestGuardrailError(RuntimeError):
    """Raised when an outbound network connection is attempted in an offline test suite."""
    pass


# Preserve pristine genuine socket callables at conftest import time
_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIG_SOCKET_SENDTO = socket.socket.sendto
_ORIG_CREATE_CONNECTION = socket.create_connection
_ORIG_SOCKET_SEND = socket.socket.send
_ORIG_SOCKET_SENDALL = socket.socket.sendall
_ORIG_SOCKET_RECV = socket.socket.recv
_ORIG_SOCKET_RECV_INTO = socket.socket.recv_into

_guard_active_depth = 0


def _is_local_or_loopback(address, family=None) -> bool:
    """Return True if address represents local/loopback/IPC communication."""
    # Allow AF_UNIX domain sockets unconditionally
    if family is not None and getattr(socket, "AF_UNIX", None) is not None:
        if family == socket.AF_UNIX:
            return True

    # AF_UNIX addresses can be str/bytes/Path filesystem paths
    if isinstance(address, (str, bytes, os.PathLike)):
        return True

    if not isinstance(address, (tuple, list)) or not address:
        return True

    host = address[0]
    if host is None:
        return True

    if isinstance(host, bytes):
        host = host.decode("utf-8", errors="replace")

    host_str = str(host).strip()
    if not host_str:
        return True

    host_lower = host_str.lower()
    # Fast path: well-known local names / loopback / unspecified
    if host_lower in ("localhost", "testserver", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    if host_lower.endswith(".localhost"):
        return True

    # IP address parsing (loopback or unspecified)
    try:
        ip = ipaddress.ip_address(host_str.strip("[]"))
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        # Non-loopback hostname (e.g. baostock.com, api.openai.com, external domain)
        return False


def _format_address(address) -> str:
    """Format an address tuple or object for readable error messages."""
    if isinstance(address, (tuple, list)):
        if len(address) >= 2:
            return f"{address[0]}:{address[1]}"
        elif len(address) == 1:
            return str(address[0])
    return str(address)


def _assert_network_allowed(address, family=None):
    """Enforce offline isolation rule; raise OfflineTestGuardrailError if external."""
    if not _is_local_or_loopback(address, family):
        dest = _format_address(address)
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: outbound network connection to {dest} is disallowed in offline test suite. "
            "Mark with @pytest.mark.network or mock the provider."
        )


def _check_socket_peer(sock):
    """Ensure existing socket is not communicating with a non-local external peer."""
    if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", None):
        return
    try:
        peer = sock.getpeername()
    except OSError:
        return
    if peer is not None:
        _assert_network_allowed(peer, getattr(sock, "family", None))


def _guarded_connect(self, *args, **kwargs):
    addr = kwargs.get("address") if "address" in kwargs else (args[0] if args else None)
    if addr is not None:
        _assert_network_allowed(addr, getattr(self, "family", None))
    return _ORIG_SOCKET_CONNECT(self, *args, **kwargs)


def _guarded_connect_ex(self, *args, **kwargs):
    addr = kwargs.get("address") if "address" in kwargs else (args[0] if args else None)
    if addr is not None:
        _assert_network_allowed(addr, getattr(self, "family", None))
    return _ORIG_SOCKET_CONNECT_EX(self, *args, **kwargs)


def _guarded_sendto(self, *args, **kwargs):
    addr = kwargs.get("address")
    if addr is None:
        if len(args) == 2:
            addr = args[1]
        elif len(args) >= 3:
            addr = args[2]
    if addr is not None:
        _assert_network_allowed(addr, getattr(self, "family", None))
    return _ORIG_SOCKET_SENDTO(self, *args, **kwargs)


def _guarded_create_connection(address, *args, **kwargs):
    _assert_network_allowed(address)
    return _ORIG_CREATE_CONNECTION(address, *args, **kwargs)


def _guarded_send(self, *args, **kwargs):
    _check_socket_peer(self)
    return _ORIG_SOCKET_SEND(self, *args, **kwargs)


def _guarded_sendall(self, *args, **kwargs):
    _check_socket_peer(self)
    return _ORIG_SOCKET_SENDALL(self, *args, **kwargs)


def _guarded_recv(self, *args, **kwargs):
    _check_socket_peer(self)
    return _ORIG_SOCKET_RECV(self, *args, **kwargs)


def _guarded_recv_into(self, *args, **kwargs):
    _check_socket_peer(self)
    return _ORIG_SOCKET_RECV_INTO(self, *args, **kwargs)


def _reset_baostock_context():
    """Reset and close baostock module-level context socket and instance."""
    for mod_name in ("baostock.common.context", "baostock.util.socketutil"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            ctx = getattr(mod, "context", None) if mod_name.endswith("socketutil") else mod
            if ctx is not None:
                sock = getattr(ctx, "default_socket", None)
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    setattr(ctx, "default_socket", None)
            if mod_name.endswith("socketutil"):
                sockutil = getattr(mod, "SocketUtil", None)
                if sockutil is not None:
                    sockutil.instance = None


def enable_offline_network_guard():
    """Install socket interceptors to block outbound non-local network traffic."""
    global _guard_active_depth
    if _guard_active_depth == 0:
        socket.socket.connect = _guarded_connect
        socket.socket.connect_ex = _guarded_connect_ex
        socket.socket.sendto = _guarded_sendto
        socket.create_connection = _guarded_create_connection
        socket.socket.send = _guarded_send
        socket.socket.sendall = _guarded_sendall
        socket.socket.recv = _guarded_recv
        socket.socket.recv_into = _guarded_recv_into
    _guard_active_depth += 1


def disable_offline_network_guard(force: bool = False):
    """Restore original socket callables."""
    global _guard_active_depth
    if force or _guard_active_depth <= 1:
        _guard_active_depth = 0
        socket.socket.connect = _ORIG_SOCKET_CONNECT
        socket.socket.connect_ex = _ORIG_SOCKET_CONNECT_EX
        socket.socket.sendto = _ORIG_SOCKET_SENDTO
        socket.create_connection = _ORIG_CREATE_CONNECTION
        socket.socket.send = _ORIG_SOCKET_SEND
        socket.socket.sendall = _ORIG_SOCKET_SENDALL
        socket.socket.recv = _ORIG_SOCKET_RECV
        socket.socket.recv_into = _ORIG_SOCKET_RECV_INTO
    else:
        _guard_active_depth -= 1


def is_offline_network_guard_active() -> bool:
    """Return whether the offline network guardrail is currently active."""
    return _guard_active_depth > 0


@contextmanager
def offline_guard_disabled():
    """Context manager to temporarily disable offline guardrail within a block."""
    global _guard_active_depth
    saved_depth = _guard_active_depth
    if saved_depth > 0:
        disable_offline_network_guard(force=True)
    try:
        yield
    finally:
        if saved_depth > 0:
            for _ in range(saved_depth):
                enable_offline_network_guard()


@contextmanager
def offline_guard_enabled():
    """Context manager to explicitly enable offline guardrail within a block."""
    global _guard_active_depth
    enable_offline_network_guard()
    try:
        yield
    finally:
        disable_offline_network_guard()


@pytest.fixture(autouse=True)
def offline_network_guard(request):
    """Pytest autouse fixture enforcing offline socket isolation for all tests.

    Intercepts all non-local outbound socket connect/send/recv attempts to prevent
    tests from hanging or unexpectedly depending on real external network services.
    Tests decorated with @pytest.mark.network are exempted and allowed full network access.
    """
    _reset_baostock_context()
    if request.node.get_closest_marker("network") is not None:
        with offline_guard_disabled():
            yield
        _reset_baostock_context()
        return

    if not is_offline_network_guard_active():
        enable_offline_network_guard()
    try:
        yield
    finally:
        _reset_baostock_context()


def pytest_sessionstart(session):
    """Activate offline network guard at the very beginning of the test session."""
    _reset_baostock_context()
    enable_offline_network_guard()


def pytest_sessionfinish(session, exitstatus):
    """Restore pristine socket methods when pytest session concludes."""
    _reset_baostock_context()
    disable_offline_network_guard(force=True)


# Activate immediately upon conftest import so early imports and background threads cannot leak
enable_offline_network_guard()





def _apply_frozen_trade_date(monkeypatch):
    """Patch all trade_calendar time/date entrypoints and module imports."""
    from tradingagents.dataflows import trade_calendar as tc

    monkeypatch.setattr(tc, "now_cn", lambda: FROZEN_TRADE_DATETIME)
    monkeypatch.setattr(tc, "cn_today_str", lambda: FROZEN_TRADE_DATE)

    # Patch any already-imported modules across tradingagents, api, tests, and conftest
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if mod_name.startswith(("tradingagents", "api", "tests", "conftest")):
            if hasattr(mod, "cn_today_str"):
                monkeypatch.setattr(mod, "cn_today_str", lambda: FROZEN_TRADE_DATE)
            if hasattr(mod, "now_cn"):
                monkeypatch.setattr(mod, "now_cn", lambda: FROZEN_TRADE_DATETIME)


@pytest.fixture
def frozen_trade_date(monkeypatch):
    """Pytest fixture that stubs today and now_cn to a fixed known trading day (Monday 2026-08-17 16:00:00).

    Guarantees that:
    - cn_today_str() == "2026-08-17"
    - now_cn() == datetime(2026, 8, 17, 16, 0, 0, tzinfo=CN_TZ)
    - is_cn_trading_day("2026-08-17") == True
    - cn_market_phase() == "post_close"
    - is_historical_analysis_date("2026-08-17") == False
    - is_historical_analysis_date("2026-08-14") == True
    - unavailable_analysis_date_reason("2026-08-17") == None
    """
    _apply_frozen_trade_date(monkeypatch)
    return FROZEN_TRADE_DATE
