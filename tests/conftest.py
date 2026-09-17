"""Test fixtures for tests/ directory.

Provides unified date/trade-calendar isolation fixtures to ensure tests
run deterministically on any natural day (weekends, holidays, intraday),
and establishes a two-layer offline network guardrail:

1. Socket-patch interception layer (DAV-979 / DAV-995 rework in 4355ca3):
   composable depth-counted enable/disable plus per-test tamper attribution.
2. PEP 578 audit-hook layer (DAV-1007): irreversible observation hook
   (logs every audited network event) + interception hook (denies non-local
   events), deny/observation log pairing, positive-control sentinels, and a
   curl_cffi C-level guard for calls that bypass Python socket callables.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

import pytest

from tradingagents.dataflows.interface import NetworkAccessDeniedError

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")

# A fixed known trading day: a historical Monday (2026-08-17) with post-close timestamp.
FROZEN_TRADE_DATE = "2026-08-17"
FROZEN_TRADE_DATE_OBJ = date(2026, 8, 17)
FROZEN_TRADE_DATETIME = datetime(2026, 8, 17, 16, 0, 0, tzinfo=CN_TZ)


# ============================================================================
# Offline Network Guardrail & Socket Isolation (DAV-979 / DAV-995 / DAV-1007)
# ============================================================================

class OfflineTestGuardrailError(NetworkAccessDeniedError):
    """Raised when an outbound network connection is attempted in an offline test suite.

    Inherits NetworkAccessDeniedError so production code that must propagate
    network-denial (e.g. v03_return_measure) treats guardrail denial the same
    as a production-side refusal.
    """
    pass


# --------------------------------------------------------------------------
# Explicit network authorization switch (fail-closed) and audit log plumbing
# --------------------------------------------------------------------------

_log_lock = threading.Lock()
_current_nodeid: Optional[str] = None
_current_phase: str = "init"
_hooks_installed: bool = False
_sentinel_executed: bool = False

# Unique log file paths per run (isolated by timestamp, pid, and uuid)
_DEFAULT_RUN_ID = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
RT_OBSERVATION_LOG: str = os.getenv("RT_OBSERVATION_LOG") or f"/tmp/rt_observe_{_DEFAULT_RUN_ID}.log"
RT_DENY_LOG: str = os.getenv("RT_DENY_LOG") or f"/tmp/rt_deny_{_DEFAULT_RUN_ID}.log"

if "RT_OBSERVATION_LOG" not in os.environ:
    os.environ["RT_OBSERVATION_LOG"] = RT_OBSERVATION_LOG
if "RT_DENY_LOG" not in os.environ:
    os.environ["RT_DENY_LOG"] = RT_DENY_LOG


def is_network_enabled() -> bool:
    """Return True if explicit network authorization is granted.

    Fail-closed: returns False if RT_NETWORK_ENABLED is missing, unset, empty,
    or anything other than exactly '1'.
    """
    return os.getenv("RT_NETWORK_ENABLED") == "1"


def get_observation_log_path() -> str:
    """Return the absolute path of the observation log."""
    return RT_OBSERVATION_LOG


def get_deny_log_path() -> str:
    """Return the absolute path of the deny log."""
    return RT_DENY_LOG


def _append_log(filepath: str, line: str) -> None:
    """Append a log line with thread-safety and explicit flush."""
    try:
        with _log_lock:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception:
        pass


def _format_target(target: Any) -> str:
    """Format address / host target consistently for log lines and errors."""
    if isinstance(target, (tuple, list)):
        if len(target) >= 2:
            return f"{target[0]}:{target[1]}"
        elif len(target) == 1:
            return str(target[0])
    return str(target)


# Alias retained for the socket-patch layer error messages.
_format_address = _format_target


def _log_observation(event: str, target: Any, is_local: bool) -> None:
    ts = datetime.now().isoformat()
    pid = os.getpid()
    nodeid = _current_nodeid or "INIT"
    phase = _current_phase or "init"
    target_str = _format_target(target)
    line = (
        f"{ts} | PID:{pid} | NODEID:{nodeid} | PHASE:{phase} | "
        f"EVENT:{event} | LOCAL:{is_local} | TARGET:{target_str}\n"
    )
    _append_log(RT_OBSERVATION_LOG, line)


def _log_deny(event: str, target: Any) -> None:
    ts = datetime.now().isoformat()
    pid = os.getpid()
    nodeid = _current_nodeid or "INIT"
    phase = _current_phase or "init"
    target_str = _format_target(target)
    line = (
        f"{ts} | PID:{pid} | NODEID:{nodeid} | PHASE:{phase} | "
        f"EVENT:{event} | ACTION:DENY | TARGET:{target_str}\n"
    )
    _append_log(RT_DENY_LOG, line)


def _is_local_or_loopback(target: Any, family: Optional[int] = None) -> bool:
    """Return True if target represents a local/loopback/IPC communication channel."""
    # Allow AF_UNIX domain sockets unconditionally
    if family is not None and getattr(socket, "AF_UNIX", None) is not None:
        if family == socket.AF_UNIX:
            return True

    # AF_UNIX addresses can be str/bytes/Path filesystem paths
    if isinstance(target, (bytes, os.PathLike)):
        return True
    if isinstance(target, str):
        if "/" in target or target.startswith("\x00"):
            return True

    host = target
    if isinstance(target, (tuple, list)):
        if not target:
            return True
        host = target[0]

    if host is None:
        return True

    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", errors="replace")
        except Exception:
            return False

    host_str = str(host).strip()
    if not host_str:
        return True

    host_lower = host_str.lower().strip("[]")
    # Fast path: well-known local names / loopback / unspecified
    if host_lower in ("localhost", "testserver", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    if host_lower.endswith(".localhost"):
        return True

    # IP address parsing (loopback or unspecified)
    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        # Non-loopback hostname (e.g. baostock.com, api.openai.com, external domain)
        return False


# --------------------------------------------------------------------------
# Layer 1: socket-patch interception (composable + tamper-attributable)
# --------------------------------------------------------------------------

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


def _assert_network_allowed(address, family=None):
    """Enforce offline isolation rule; raise OfflineTestGuardrailError if external."""
    if not _is_local_or_loopback(address, family):
        dest = _format_address(address)
        nodeid = _current_nodeid or "INIT"
        phase = _current_phase or "init"
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: outbound network connection to {dest} is disallowed in offline test suite. "
            "Mark with @pytest.mark.network and run with RT_NETWORK_ENABLED=1, or mock the provider. "
            f"[nodeid={nodeid}, phase={phase}]"
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


def _patch_socket_class(attr, value):
    """Best-effort attribute assignment on socket.socket, tolerant of tests that
    have temporarily replaced the class object itself (e.g. monkeypatch with a
    plain function). If the class is unavailable the write is skipped; integrity
    is verified separately by _verify_guardrail_integrity at test boundaries."""
    target = getattr(socket, "socket", None)
    if target is None:
        return
    try:
        setattr(target, attr, value)
    except (AttributeError, TypeError):
        pass


def enable_offline_network_guard():
    """Install socket interceptors to block outbound non-local network traffic.

    No-op when explicit network authorization (RT_NETWORK_ENABLED=1) is granted.
    """
    global _guard_active_depth
    if is_network_enabled():
        return
    if _guard_active_depth == 0:
        _patch_socket_class("connect", _guarded_connect)
        _patch_socket_class("connect_ex", _guarded_connect_ex)
        _patch_socket_class("sendto", _guarded_sendto)
        socket.create_connection = _guarded_create_connection
        _patch_socket_class("send", _guarded_send)
        _patch_socket_class("sendall", _guarded_sendall)
        _patch_socket_class("recv", _guarded_recv)
        _patch_socket_class("recv_into", _guarded_recv_into)
    _guard_active_depth += 1


def disable_offline_network_guard(force: bool = False):
    """Restore original socket callables."""
    global _guard_active_depth
    if force or _guard_active_depth <= 1:
        _guard_active_depth = 0
        _patch_socket_class("connect", _ORIG_SOCKET_CONNECT)
        _patch_socket_class("connect_ex", _ORIG_SOCKET_CONNECT_EX)
        _patch_socket_class("sendto", _ORIG_SOCKET_SENDTO)
        socket.create_connection = _ORIG_CREATE_CONNECTION
        _patch_socket_class("send", _ORIG_SOCKET_SEND)
        _patch_socket_class("sendall", _ORIG_SOCKET_SENDALL)
        _patch_socket_class("recv", _ORIG_SOCKET_RECV)
        _patch_socket_class("recv_into", _ORIG_SOCKET_RECV_INTO)
    else:
        _guard_active_depth -= 1


def is_offline_network_guard_active() -> bool:
    """Return whether the offline network guardrail is currently enforcing isolation."""
    return _guard_active_depth > 0 and not is_network_enabled()


def _expected_guarded_callables():
    """Mapping of guarded attribute -> (expected callable, owner)."""
    return {
        "socket.socket.connect": (_guarded_connect, "socket.socket"),
        "socket.socket.connect_ex": (_guarded_connect_ex, "socket.socket"),
        "socket.socket.sendto": (_guarded_sendto, "socket.socket"),
        "socket.create_connection": (_guarded_create_connection, "socket"),
        "socket.socket.send": (_guarded_send, "socket.socket"),
        "socket.socket.sendall": (_guarded_sendall, "socket.socket"),
        "socket.socket.recv": (_guarded_recv, "socket.socket"),
        "socket.socket.recv_into": (_guarded_recv_into, "socket.socket"),
    }


def _verify_guardrail_integrity(nodeid: str, phase: str) -> None:
    """Fail with attribution if a test permanently replaced guardrail callables.

    Per-test fixtures/monkeypatches that temporarily override socket callables
    (e.g. patch('socket.socket.connect')) compose fine: they are restored before
    this check runs at test boundaries. A failure here means the override leaked
    past the test, or socket.socket itself was left in a non-class state.
    """
    if not is_offline_network_guard_active():
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: guardrail is inactive at {phase} of {nodeid}; "
            "a previous test disabled it without restoring."
        )
    drifted = []
    for name, (expected, owner) in _expected_guarded_callables().items():
        try:
            if owner == "socket.socket":
                cls = getattr(socket, "socket", None)
                if not isinstance(cls, type):
                    drifted.append(f"{name} (socket.socket is {cls!r}, not a class)")
                    continue
                current = getattr(cls, name.rsplit(".", 1)[-1])
            else:
                current = getattr(socket, name.rsplit(".", 1)[-1])
        except AttributeError:
            drifted.append(f"{name} (attribute unreadable)")
            continue
        if current is not expected:
            drifted.append(f"{name} is {current!r}")
    if drifted:
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: callable identity drift detected at {phase} of {nodeid}: "
            + "; ".join(drifted)
        )


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
    enable_offline_network_guard()
    try:
        yield
    finally:
        disable_offline_network_guard()


# --------------------------------------------------------------------------
# Layer 2: PEP 578 audit hooks — observation (never denies) + interception
# --------------------------------------------------------------------------

_COVERED_AUDIT_EVENTS = {
    "socket.connect",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.sendto",
}


def _extract_target_and_family(event: str, args: tuple) -> Tuple[Any, Optional[int]]:
    """Extract destination address/host and socket family from audit hook arguments."""
    family = None
    if event in ("socket.connect", "socket.sendto"):
        if len(args) >= 2:
            sock, target = args[0], args[1]
            family = getattr(sock, "family", None)
            return target, family
    elif event == "socket.getaddrinfo":
        if len(args) >= 1:
            host = args[0]
            port = args[1] if len(args) > 1 else None
            family = args[2] if len(args) > 2 else None
            return ((host, port) if port is not None else host), family
    elif event == "socket.gethostbyname":
        if len(args) >= 1:
            host = args[0]
            return host, None
    return None, None


def _observation_hook(event: str, args: tuple) -> None:
    """Observation hook (installed FIRST): unconditionally records all network events, NEVER denies."""
    try:
        if event not in _COVERED_AUDIT_EVENTS:
            return
        target, family = _extract_target_and_family(event, args)
        is_local = _is_local_or_loopback(target, family=family)
        _log_observation(event, target, is_local)
    except Exception:
        pass


def _interception_hook(event: str, args: tuple) -> None:
    """Interception hook (installed SECOND): only responsible for denying unauthorized external calls."""
    if event not in _COVERED_AUDIT_EVENTS:
        return
    if is_network_enabled():
        return
    target, family = _extract_target_and_family(event, args)
    if not _is_local_or_loopback(target, family=family):
        _log_deny(event, target)
        nodeid = _current_nodeid or "INIT"
        phase = _current_phase or "init"
        target_str = _format_target(target)
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: outbound network connection to {target_str} is disallowed in offline test suite. "
            f"Mark with @pytest.mark.network and run with RT_NETWORK_ENABLED=1, or mock the provider. "
            f"[nodeid={nodeid}, phase={phase}]"
        )


def check_socket_health(nodeid: Optional[str] = None, phase: Optional[str] = None) -> Tuple[bool, str]:
    """Inspect socket health safely without raising AttributeError if socket is mocked/replaced."""
    nid = nodeid or _current_nodeid or "unknown"
    ph = phase or _current_phase or "unknown"
    sock_cls = getattr(socket, "socket", None)
    if sock_cls is None:
        return False, f"socket.socket is None [nodeid={nid}, phase={ph}]"
    if not isinstance(sock_cls, type):
        return False, f"socket.socket is not a class ({type(sock_cls).__name__}: {sock_cls!r}) [nodeid={nid}, phase={ph}]"
    if not hasattr(sock_cls, "connect"):
        return False, f"socket.socket lacks 'connect' attribute [nodeid={nid}, phase={ph}]"
    if not callable(getattr(sock_cls, "connect", None)):
        return False, f"socket.socket.connect is not callable [nodeid={nid}, phase={ph}]"
    return True, f"socket healthy [nodeid={nid}, phase={ph}]"


def run_guardrail_sentinel() -> None:
    """Execute positive-control sentinels in current process for both observation and deny logs."""
    global _current_nodeid, _current_phase, _sentinel_executed
    saved_nodeid = _current_nodeid
    saved_phase = _current_phase

    # 1. Observation Hook positive control: real connect to local loopback listener
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        _current_nodeid = "SENTINEL_ALLOW"
        _current_phase = "sentinel"
        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            cli.connect(("127.0.0.1", port))
            conn, _ = srv.accept()
            conn.close()
        finally:
            cli.close()
    finally:
        srv.close()

    # 2. Deny Hook positive control: audited event against TEST-NET-1 IP.
    # Uses getaddrinfo so the deny is produced by the audit-hook layer even
    # when the socket-patch layer is active (the patch would raise before the
    # real syscall and the audit hook would never fire).
    if not is_network_enabled():
        _current_nodeid = "SENTINEL_DENY"
        _current_phase = "sentinel"
        try:
            socket.getaddrinfo("192.0.2.254", 65534)
        except OfflineTestGuardrailError:
            pass
        except OSError:
            pass

    _current_nodeid = saved_nodeid
    _current_phase = saved_phase
    _sentinel_executed = True


_CURL_CFFI_GUARD_STATUS: str = "uninitialized"


def get_curl_cffi_guard_status() -> str:
    """Return the status of curl_cffi guard installation."""
    return _CURL_CFFI_GUARD_STATUS


def _install_guardrail_hooks() -> None:
    """Install dual audit hooks and CFFI guards. Hook order: Observation FIRST, Interception SECOND."""
    global _hooks_installed
    if not _hooks_installed:
        sys.addaudithook(_observation_hook)
        sys.addaudithook(_interception_hook)
        _install_curl_cffi_guard()
        _hooks_installed = True


def _install_curl_cffi_guard() -> None:
    """Guard C-level curl_cffi requests which bypass Python socket audit hooks."""
    global _CURL_CFFI_GUARD_STATUS
    try:
        import curl_cffi.curl
        _orig_perform = getattr(curl_cffi.curl.Curl, "perform", None)
        if _orig_perform is None:
            _CURL_CFFI_GUARD_STATUS = "unsupported_missing_perform"
            _log_observation("guard.curl_cffi", "unsupported_missing_perform", True)
            return
        if getattr(_orig_perform, "_is_guarded", False):
            _CURL_CFFI_GUARD_STATUS = "installed:Curl.perform"
            return

        def _guarded_perform(self, *args, **kwargs):
            if is_network_enabled():
                return _orig_perform(self, *args, **kwargs)
            _log_observation("curl_cffi.perform", "external_curl_cffi", False)
            _log_deny("curl_cffi.perform", "external_curl_cffi")
            nodeid = _current_nodeid or "INIT"
            phase = _current_phase or "init"
            raise OfflineTestGuardrailError(
                f"OfflineTestGuardrail: outbound curl_cffi connection is disallowed in offline test suite. "
                f"[nodeid={nodeid}, phase={phase}]"
            )

        _guarded_perform._is_guarded = True
        curl_cffi.curl.Curl.perform = _guarded_perform
        _CURL_CFFI_GUARD_STATUS = "installed:Curl.perform"
        _log_observation("guard.curl_cffi", "installed:Curl.perform", True)
    except ImportError as e:
        _CURL_CFFI_GUARD_STATUS = f"not_installed:{e}"
        _log_observation("guard.curl_cffi", f"not_installed:{e}", True)
    except Exception as e:
        _CURL_CFFI_GUARD_STATUS = f"error:{type(e).__name__}:{e}"
        _log_observation("guard.curl_cffi", f"error:{type(e).__name__}:{e}", True)


def _reset_baostock_context() -> None:
    """Safely reset baostock module-level context socket if baostock was loaded."""
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


# --------------------------------------------------------------------------
# Pytest wiring
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def offline_network_guard(request):
    """Pytest autouse fixture enforcing offline socket isolation for all tests.

    Intercepts all non-local outbound socket connect/send/recv attempts to prevent
    tests from hanging or unexpectedly depending on real external network services.
    Tests decorated with @pytest.mark.network are exempted and allowed full network access.
    """
    _reset_baostock_context()
    if request.node.get_closest_marker("network") is not None or is_network_enabled():
        with offline_guard_disabled():
            yield
        _reset_baostock_context()
        return

    if _guard_active_depth == 0:
        enable_offline_network_guard()
    else:
        # Guard is on, but a previous test may have left the callables replaced.
        _verify_guardrail_integrity(request.node.nodeid, "setup")
    try:
        yield
    finally:
        try:
            _verify_guardrail_integrity(request.node.nodeid, "teardown")
        finally:
            _reset_baostock_context()


@pytest.fixture
def forbid_external_network():
    """Reusable per-test entry point to the unified offline guardrail.

    Tests that previously layered ad-hoc ``patch("socket.socket.connect")``
    blocks should request this fixture instead. The session-wide guardrail
    already intercepts outbound non-local traffic; this fixture fail-closes
    if enforcement is not active, so dropping the ad-hoc patch does not
    weaken denial strength.
    """
    if not is_offline_network_guard_active():
        raise OfflineTestGuardrailError(
            "OfflineTestGuardrail: forbid_external_network requested but the "
            "unified offline guardrail is not enforcing."
        )
    yield


def pytest_addoption(parser):
    """Add dedicated network authorization flag to pytest CLI."""
    parser.addoption(
        "--allow-network",
        action="store_true",
        default=False,
        help="Explicitly enable network access for tests decorated with @pytest.mark.network.",
    )


def pytest_configure(config):
    """Register CLI network switch and the 'network' marker."""
    if config.getoption("--allow-network", default=False):
        os.environ["RT_NETWORK_ENABLED"] = "1"
        # Socket-patch layer may already be installed from conftest import;
        # restore pristine callables so authorized traffic passes through.
        disable_offline_network_guard(force=True)


def pytest_sessionstart(session):
    """Activate offline network guard, run sentinels, reset baostock context."""
    _install_guardrail_hooks()
    _reset_baostock_context()
    run_guardrail_sentinel()
    enable_offline_network_guard()


def pytest_sessionfinish(session, exitstatus):
    """Restore pristine socket methods when pytest session concludes."""
    _reset_baostock_context()
    disable_offline_network_guard(force=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Track nodeid and phase during test execution; safely inspect socket health post-teardown."""
    global _current_nodeid, _current_phase
    _current_nodeid = item.nodeid
    _current_phase = "setup"
    try:
        yield
    finally:
        healthy, msg = check_socket_health(item.nodeid, "post_teardown")
        if not healthy:
            logger.warning("Guardrail detected socket tampering after test teardown: %s", msg)
        _current_nodeid = None
        _current_phase = "idle"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    global _current_phase
    _current_phase = "call"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    global _current_phase
    _current_phase = "teardown"


# Activate both layers immediately upon conftest import so early imports and
# background threads cannot leak.
_install_guardrail_hooks()
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
