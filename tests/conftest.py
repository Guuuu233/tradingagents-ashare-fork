"""Test fixtures for tests/ directory.

Provides unified date/trade-calendar isolation fixtures to ensure tests
run deterministically on any natural day (weekends, holidays, intraday),
and establishes the PEP 578 audit-hook double-layer offline network guardrail
(DAV-1007).
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
from datetime import date, datetime
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

import pytest

logger = logging.getLogger(__name__)

CN_TZ = ZoneInfo("Asia/Shanghai")

# A fixed known trading day: a historical Monday (2026-08-17) with post-close timestamp.
FROZEN_TRADE_DATE = "2026-08-17"
FROZEN_TRADE_DATE_OBJ = date(2026, 8, 17)
FROZEN_TRADE_DATETIME = datetime(2026, 8, 17, 16, 0, 0, tzinfo=CN_TZ)


# ============================================================================
# Offline Network Guardrail (Audit Hook Architecture - DAV-1007 / PEP 578)
# ============================================================================

class OfflineTestGuardrailError(RuntimeError):
    """Raised when an outbound network connection is attempted in an offline test suite."""
    pass


# Global tracking for active test context and log files
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


def is_offline_network_guard_active() -> bool:
    """Return True if the offline network guardrail is currently enforcing isolation."""
    return not is_network_enabled()


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
    """Format address / host target consistently for log lines."""
    if isinstance(target, (tuple, list)):
        if len(target) >= 2:
            return f"{target[0]}:{target[1]}"
        elif len(target) == 1:
            return str(target[0])
    return str(target)


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
    if family is not None and getattr(socket, "AF_UNIX", None) is not None:
        if family == socket.AF_UNIX:
            return True

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
    if host_lower in ("localhost", "testserver", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True
    if host_lower.endswith(".localhost"):
        return True

    try:
        ip = ipaddress.ip_address(host_lower)
        return ip.is_loopback or ip.is_unspecified
    except ValueError:
        return False


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

    # 2. Deny Hook positive control: real attempt to TEST-NET-1 IP (192.0.2.254:65534)
    if not is_network_enabled():
        _current_nodeid = "SENTINEL_DENY"
        _current_phase = "sentinel"
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect(("192.0.2.254", 65534))
        except OfflineTestGuardrailError:
            pass
        finally:
            s.close()

    _current_nodeid = saved_nodeid
    _current_phase = saved_phase
    _sentinel_executed = True


_CURL_CFFI_GUARD_STATUS: str = "uninitialized"


def get_curl_cffi_guard_status() -> str:
    """Return the status of curl_cffi guard installation."""
    global _CURL_CFFI_GUARD_STATUS
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


# Install hooks immediately upon conftest import so early imports/threads cannot leak
_install_guardrail_hooks()


# Pytest Hooks

def pytest_addoption(parser):
    """Add dedicated network authorization flag to pytest CLI."""
    parser.addoption(
        "--allow-network",
        action="store_true",
        default=False,
        help="Explicitly enable network access for tests decorated with @pytest.mark.network.",
    )


def pytest_configure(config):
    """Register CLI network switch."""
    if config.getoption("--allow-network", default=False):
        os.environ["RT_NETWORK_ENABLED"] = "1"


def pytest_sessionstart(session):
    """Run sentinel checks and reset baostock context at test session start."""
    _install_guardrail_hooks()
    _reset_baostock_context()
    run_guardrail_sentinel()


def pytest_sessionfinish(session, exitstatus):
    """Clean up baostock context at test session finish."""
    _reset_baostock_context()


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
