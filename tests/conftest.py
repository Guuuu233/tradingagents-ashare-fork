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
from urllib.parse import urlparse
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
# DAV-1818: a configured forward proxy is an egress channel, not a local peer
# --------------------------------------------------------------------------
# The loopback allow-list above is required for genuine local services (the
# guardrail sentinel, FastAPI TestClient, AF_UNIX IPC, test-local TCP servers).
# But a *forward proxy* listening on 127.0.0.1 is a different thing: with
# HTTP(S)_PROXY set, ``requests``/urllib3 rewrite the destination to that
# loopback endpoint, so the guardrail sees a "local" peer and admits traffic
# that then really leaves the machine. The denial never fires, and callers
# classify whatever the proxy answered instead of a denial -- measured as
# ``'403' == 'network_denied'`` (proxy answered 403) or ``'network_error'``
# (proxy refused). That is how a proxy defeats the offline guardrail.
#
# So: destinations matching a proxy endpoint configured in the environment are
# denied regardless of being loopback. Legitimate loopback traffic to any other
# port is unaffected.
_PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)

# Port to assume when a proxy value omits one (e.g. ``HTTP_PROXY=http://127.0.0.1``).
# urllib3/requests dial the scheme's default port in that case, so resolving it
# the same way keeps the denial aligned with what the transport would actually
# connect to. Without this, a portless value matched *every* port on that host,
# which denied the guardrail's own loopback sentinel and aborted the session
# with INTERNALERROR before a single test ran.
_PROXY_SCHEME_DEFAULT_PORT = {
    "http": 80,
    "https": 443,
    "socks": 1080,
    "socks4": 1080,
    "socks4a": 1080,
    "socks5": 1080,
    "socks5h": 1080,
}
_PROXY_FALLBACK_PORT = 80


def _proxy_endpoints() -> set:
    """Return ``{(host, port)}`` for proxies configured in the environment.

    Read live on every call (no caching): tests legitimately set and clear these
    with ``monkeypatch.setenv``/``delenv`` mid-session, so a cached snapshot
    would both miss newly configured proxies and deny a proxy that a test has
    since removed.
    """
    endpoints = set()
    for var in _PROXY_ENV_VARS:
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        try:
            # urlparse needs a scheme to populate .hostname for bare host:port.
            parsed = urlparse(raw if "://" in raw else f"//{raw}")
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            # Malformed value (e.g. out-of-range port) -- not a usable endpoint.
            continue
        if not host:
            continue
        if port is None:
            # Portless config: resolve to the port the transport would dial,
            # rather than treating it as "any port on this host".
            port = _PROXY_SCHEME_DEFAULT_PORT.get(
                (parsed.scheme or "").lower(), _PROXY_FALLBACK_PORT
            )
        endpoints.add((str(host).strip().lower().strip("[]"), port))
    return endpoints


def _is_proxy_endpoint(target: Any) -> bool:
    """True if ``target`` addresses a proxy endpoint configured in the environment."""
    if isinstance(target, (tuple, list)):
        if not target:
            return False
        host = target[0]
        port = target[1] if len(target) >= 2 else None
    else:
        host, port = target, None
    if host is None:
        return False
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", errors="replace")
        except Exception:
            return False
    host_str = str(host).strip().lower().strip("[]")
    if not host_str:
        return False
    try:
        want_port = int(port) if port is not None else None
    except (TypeError, ValueError):
        want_port = None
    for proxy_host, proxy_port in _proxy_endpoints():
        if proxy_host != host_str:
            continue
        # Only a genuinely unknown port (target given as a bare hostname, e.g.
        # the audit hook's ``socket.gethostbyname`` argument) falls back to a
        # host-only match. A portless *config* is already resolved to the
        # scheme default above, so it cannot swallow unrelated local ports.
        if proxy_port is None or want_port is None or proxy_port == want_port:
            return True
    return False


def _is_allowed_target(target: Any, family: Optional[int] = None) -> bool:
    """True only for targets the offline guardrail may admit.

    Single source of truth for both the interception hook and the socket-patch
    layer, so the two layers cannot drift apart.
    """
    if _is_proxy_endpoint(target):
        return False
    return _is_local_or_loopback(target, family)


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
    if not _is_allowed_target(address, family):
        dest = _format_address(address)
        nodeid = _current_nodeid or "INIT"
        phase = _current_phase or "init"
        via = " (forward proxy configured in env)" if _is_proxy_endpoint(address) else ""
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: outbound network connection to {dest} is disallowed in offline test suite{via}. "
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
        is_local = _is_allowed_target(target, family=family)
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
    if not _is_allowed_target(target, family=family):
        _log_deny(event, target)
        nodeid = _current_nodeid or "INIT"
        phase = _current_phase or "init"
        target_str = _format_target(target)
        via = " (forward proxy configured in env)" if _is_proxy_endpoint(target) else ""
        raise OfflineTestGuardrailError(
            f"OfflineTestGuardrail: outbound network connection to {target_str} is disallowed in offline test suite{via}. "
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
def isolated_stock_map_archive(tmp_path):
    """Never read/write the operator's persistent name archive from tests.

    Do not depend on monkeypatch here: that would make its teardown follow the
    network guard's integrity check, leaving socket test doubles active there.
    """
    key = "TA_STOCK_MAP_ARCHIVE_PATH"
    previous = os.environ.get(key)
    os.environ[key] = str(tmp_path / "stock-map.json")
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


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


def _offline_cn_stock_name_stub(symbol):
    """Deterministic offline stand-in for ``get_cn_stock_name``.

    Mirrors the production fallback (return the numeric code / symbol itself)
    without attempting the eastmoney/sina name lookups.
    """
    import re as _re

    m = _re.search(r"(\d{6})", str(symbol or ""))
    return m.group(1) if m else str(symbol or "")


@pytest.fixture(autouse=True)
def offline_historical_case_seam(request):
    """DAV-1040: deterministic local stubs for the historical-case chain.

    Offline runs must never enter provider routing via the report-completion
    historical-case path (record/backfill -> calculate_t1_return ->
    _load_cn_trade_dates (akshare/fuyao) / route_to_vendor), nor spend time on
    bare TCP / HTTP name lookups. While the offline guard enforces, stub:

    - ``historical_cases._load_cn_trade_dates`` -> deterministic empty
      calendar, never fetching akshare/fuyao. Offline the real loader
      degrades to empty anyway (both fetchers are guardrail-denied), so
      production semantics are unchanged; tests needing a calendar or the
      downstream vendor chain must seed an explicit local stub (see
      test_offline_network_guardrail baostock fail-closed test).
    - ``context_utils.get_cn_stock_name`` -> local stub returning the code.
      Covers call sites resolved through the module (e.g.
      ``infer_instrument_context`` used by report_service). Analyst modules
      that imported the symbol directly are patched per-test where needed.

    Tests that explicitly patch these names keep their own stubs (patch
    nesting composes). Network-marked tests (guard exempt) are untouched.
    """
    if request.node.get_closest_marker("network") is not None or is_network_enabled():
        yield
        return
    if not is_offline_network_guard_active():
        yield
        return
    try:
        from unittest.mock import patch as _patch

        with _patch(
            "tradingagents.knowledge.historical_cases._load_cn_trade_dates",
            return_value=([], set()),
        ), _patch(
            "tradingagents.agents.utils.context_utils.get_cn_stock_name",
            side_effect=_offline_cn_stock_name_stub,
        ):
            yield
    except (ImportError, AttributeError):
        # Historical-case stack or context utils not importable/installed in
        # this environment; nothing to stub.
        yield


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


@pytest.fixture
def offline_tushare_gateway_stub(monkeypatch):
    """DAV-1818: stub the Tushare gateway seams so tests never fan out to them.

    ``DataCollector`` fan-out reaches two Tushare gateways whenever the host
    carries credentials (``.env`` / environment variables):

    - ``tushare_global_news.TushareNewsClient`` — news pool: 7-day window x 4
      sources, bisecting each unresponsive segment down to 30-minute slices and
      retrying each request up to 5 times;
    - ``industry_linkage_provider._query_tushare_api`` — financial statements
      and global indices (imported *by value* into ``tushare_provider`` and
      ``sw_industry``, so every holder is patched).

    The offline guardrail normally denies those attempts, but a test that
    replaces ``requests.post`` with a Mock defeats the socket layer and lets the
    retry/bisection amplification run (measured before this fixture: 468 + 16
    POSTs and ~180s, i.e. the ``test_zero_real_network_guard`` failure). This
    fixture replaces both seams with an immediate denial, mirroring what the
    real guardrail raises, so the collector is exercised with zero gateway
    traffic. Denial is terminal: no retry, no bisection.

    Opt-in (not autouse) because suites such as
    ``test_industry_linkage_provider.py`` deliberately drive the real client
    with ``patch("requests.post")`` and assert on call counts.
    """
    from tradingagents.dataflows import tushare_global_news as _tgn
    from tradingagents.dataflows.providers import industry_linkage_provider as _ilp
    from tradingagents.dataflows.providers import tushare_provider as _tp
    from tradingagents.dataflows import sw_industry as _swi

    def _deny(*_args, **_kwargs):
        raise OfflineTestGuardrailError(
            "OfflineTestGuardrail: Tushare gateway stubbed offline (DAV-1818). "
            "Mark with @pytest.mark.network and run with RT_NETWORK_ENABLED=1, "
            "or mock the provider."
        )

    monkeypatch.setattr(_tgn.TushareNewsClient, "_query_once", _deny)
    monkeypatch.setattr(_ilp, "_query_tushare_api", _deny)
    monkeypatch.setattr(_tp, "_query_tushare_api", _deny)
    monkeypatch.setattr(_swi, "_query_tushare_api", _deny)
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


@pytest.fixture(scope="session")
def repo_script_env():
    """Explicit, hermetic environment for child processes that run repo scripts (DAV-1905).

    Tests that shell out to ``scripts/*.py`` used to inherit whatever the parent
    shell happened to export. Three of those variables silently changed results:

    - ``PYTHONPATH``: the caller's interpreter may inject a foreign
      ``site-packages`` shadow, so the child can import a different dependency
      set than the test process (pandas 3.x vs 2.x, numpy 2.5 vs 2.2, ...).
      ``scripts/slim_result_data_storage.py`` already strips it for its own
      child; every other fork site must do the same.
    - ``DATABASE_URL``: without this, ``api.database`` would default to the real
      checkout DB (``data/tradingagents.db``). Root ``conftest.py`` already
      points it at a per-session temp SQLite file, so we pass the live value
      through unchanged — the child then resolves exactly the same isolated DB
      the test process uses. A caller-supplied isolated URL is preserved.

    ``cwd`` is set to the repository root because scripts resolve relative paths
    (``data/``, ``scripts/``) against the process cwd.

    Session-scoped: it derives a dict from ``os.environ`` and has no per-test
    state, and a module-scoped fixture (e.g. the dav1846 ``two_runs``) must be
    able to consume it.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    db_url = env.get("DATABASE_URL", "")
    if not db_url:
        # Fail closed: never let a child fall back to the checkout DB.
        raise AssertionError(
            "repo_script_env requires an isolated DATABASE_URL; the root "
            "conftest sets it before collection, so reaching this point means "
            "the test was run outside the project's conftest chain"
        )
    if not db_url.startswith("sqlite:///"):
        raise AssertionError(
            f"repo_script_env only forwards isolated SQLite URLs, got {db_url!r}"
        )
    env.setdefault("REPORT_STORAGE_MODE", "dual")
    # The offline guardrail is fail-closed by default; a child must not be
    # handed a stale switch value from the parent shell.
    env.pop("RT_NETWORK_ENABLED", None)
    return env


@pytest.fixture
def offline_vendor_router(monkeypatch):
    """Deterministic offline vendor router for tests that must not depend on
    ambient network availability (DAV-1293 rework).

    Stubs ``tradingagents.dataflows.interface.route_to_vendor`` to return an
    empty payload, so T+1 Open / T+5 价格等供应商解析路径一律落到
    「vendor 无数据」分支，与被断网守卫拦截的效果一致——无论运行环境是否
    有真实 akshare/网络可达，结果都确定。

    测试内部若自行 patch ``route_to_vendor``（如 CSV fixture），其作用域内
    后生效的 patch 覆盖本存根，互不影响。

    注意：多个模块以 ``from interface import route_to_vendor`` 顶层绑定
    （如 game_theory_tools / data_collector），仅 patch interface 模块属性
    覆盖不到这些已绑定引用，因此一并扫荡已加载模块中的同名属性。
    """
    stub = lambda *args, **kwargs: ""  # noqa: E731
    monkeypatch.setattr(
        "tradingagents.dataflows.interface.route_to_vendor", stub
    )
    import sys

    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", "") or ""
        if (
            mod is not None
            and name.startswith("tradingagents")
            and getattr(mod, "route_to_vendor", None) is not None
        ):
            monkeypatch.setattr(mod, "route_to_vendor", stub)
    yield
