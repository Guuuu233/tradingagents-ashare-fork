"""Production-port test guard (DAV-2118).

DAV-2111 incident: a stub test reached the real production API on
``127.0.0.1:8000`` and triggered two real full analyses. The offline
network guardrail deliberately treats every loopback destination as local
(needed by the guardrail sentinel, FastAPI TestClient and test-local TCP
servers), so port 8000 needs its own guard.

This module is the single source of truth for both layers:

1. In-process socket callables (``connect`` / ``connect_ex`` /
   ``sendto`` / ``create_connection`` and the peer-check used by
   ``send``/``sendall``/``recv``/``recv_into``) — installed by
   ``install_inprocess_guard()`` from ``tests/conftest.py``, and covered by
   the same depth-counted enable/disable plus tamper-attribution machinery
   as the existing offline guardrail.
2. Child processes — ``tests/sitecustomize.py`` installs an independent
   PEP 578 audit hook in every interpreter that has the repo root on
   ``sys.path`` (i.e. every child run with ``env=repo_script_env`` and
   ``cwd`` at the repo root, the DAV-1905 pattern). Parent audit hooks do
   not propagate to children, so ``repo_script_env`` in
   ``tests/conftest.py`` also exports ``TA_TEST_PORT_GUARD=1`` as the
   explicit arming flag; ``sitecustomize.py`` denies connections to the
   forbidden endpoints unconditionally in a test-spawned child.

Forbidden endpoints: ``127.0.0.1:8000`` and ``localhost:8000`` — exactly
the production API listener. Other loopback ports remain usable for
test-local listeners.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any

ENV_FLAG = "TA_TEST_PORT_GUARD"
BLOCKED_PORT = 8000

# Loopback aliases that resolve to the same local listener as 127.0.0.1.
# Only port-8000 connections via these aliases are denied; a non-loopback
# host on 8000 is already covered by the offline guardrail, and other
# loopback ports stay open for test-local listeners.
_LOOPBACK_HOSTNAMES = ("localhost", "testserver")


def guard_enabled() -> bool:
    """True when the env flag arms the guard for this process."""
    return os.environ.get(ENV_FLAG) == "1"


def _host_port(target: Any):
    """Return ``(host_str, port|None)`` for a connect/sendto-style target."""
    if isinstance(target, (tuple, list)):
        if not target:
            return None, None
        host = target[0]
        port = target[1] if len(target) >= 2 else None
    else:
        host, port = target, None
    if host is None:
        return None, None
    if isinstance(host, bytes):
        try:
            host = host.decode("utf-8", errors="replace")
        except Exception:
            return None, None
    host_str = str(host).strip().lower().strip("[]")
    if not host_str:
        return None, None
    try:
        want_port = int(port) if port is not None else None
    except (TypeError, ValueError):
        want_port = None
    return host_str, want_port


def is_blocked_target(target: Any) -> bool:
    """True only for connections to the production API listener.

    Matches ``127.0.0.1:8000``, ``localhost:8000`` (incl. ``*.localhost``
    and ``testserver`` aliases) and ``::1:8000``. ``0.0.0.0``/``::`` are
    unspecified bind addresses, not dialable production endpoints, so they
    are not blocked here.
    """
    host_str, port = _host_port(target)
    if host_str is None or port != BLOCKED_PORT:
        return False
    if host_str in _LOOPBACK_HOSTNAMES or host_str.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host_str)
        return ip.is_loopback
    except ValueError:
        return False


def format_target(target: Any) -> str:
    if isinstance(target, (tuple, list)):
        if len(target) >= 2:
            return f"{target[0]}:{target[1]}"
        if len(target) == 1:
            return str(target[0])
    return str(target)


def _node_phase() -> str:
    """Best-effort ``[nodeid=…, phase=…]`` attribution from tests/conftest."""
    try:
        import tests.conftest as _tc
    except Exception:
        try:
            import conftest as _tc  # type: ignore
        except Exception:
            _tc = None
    nodeid = (getattr(_tc, "_current_nodeid", None) or "INIT") if _tc else "INIT"
    phase = (getattr(_tc, "_current_phase", None) or "init") if _tc else "init"
    return f"[nodeid={nodeid}, phase={phase}]"


# Error class used by the in-process layer: resolved lazily on first raise
# so production_port_guard stays importable standalone (child interpreters
# never hit this path — see _CHILD_ERROR below).
_GUARD_ERR_CLS = None


def guardrail_error(target: Any) -> Exception:
    """Build the denial error, preferring the suite's OfflineTestGuardrailError."""
    global _GUARD_ERR_CLS
    if _GUARD_ERR_CLS is None:
        try:
            from tests.conftest import OfflineTestGuardrailError as _Err
        except Exception:
            _Err = RuntimeError
        _GUARD_ERR_CLS = _Err
    return _GUARD_ERR_CLS(
        "OfflineTestGuardrail: connection to production service "
        f"{format_target(target)} is forbidden in the test suite (DAV-2118). "
        "Point the code under test at a mock or a test-local listener instead. "
        + _node_phase()
    )


def assert_not_blocked(target: Any) -> None:
    """Raise when ``target`` is a forbidden production endpoint."""
    if is_blocked_target(target):
        raise guardrail_error(target)


# --------------------------------------------------------------------------
# In-process socket interception
# --------------------------------------------------------------------------

_ORIG = {}


def install_inprocess_guard() -> None:
    """Wrap the pristine socket callables with the production-port check.

    Called once at ``tests/conftest.py`` import, BEFORE the offline
    guardrail installs its own interceptors, so these wrappers sit
    underneath: when the offline guard is active a denied external call
    raises there first and production-port calls raise here; with
    ``RT_NETWORK_ENABLED=1`` / ``@pytest.mark.network`` the offline layer
    is bypassed while this guard still fails closed on port 8000.
    """
    import socket as _s

    if _ORIG:
        return
    _ORIG.update(
        connect=_s.socket.connect,
        connect_ex=_s.socket.connect_ex,
        sendto=_s.socket.sendto,
        create_connection=_s.create_connection,
        send=_s.socket.send,
        sendall=_s.socket.sendall,
        recv=_s.socket.recv,
        recv_into=_s.socket.recv_into,
    )

    def _guarded_connect(self, *args, **kwargs):
        addr = kwargs.get("address") if "address" in kwargs else (
            args[0] if args else None)
        if addr is not None:
            assert_not_blocked(addr)
        return _ORIG["connect"](self, *args, **kwargs)

    def _guarded_connect_ex(self, *args, **kwargs):
        addr = kwargs.get("address") if "address" in kwargs else (
            args[0] if args else None)
        if addr is not None:
            assert_not_blocked(addr)
        return _ORIG["connect_ex"](self, *args, **kwargs)

    def _guarded_sendto(self, *args, **kwargs):
        addr = kwargs.get("address")
        if addr is None:
            if len(args) == 2:
                addr = args[1]
            elif len(args) >= 3:
                addr = args[2]
        if addr is not None:
            assert_not_blocked(addr)
        return _ORIG["sendto"](self, *args, **kwargs)

    def _guarded_create_connection(address, *args, **kwargs):
        assert_not_blocked(address)
        return _ORIG["create_connection"](address, *args, **kwargs)

    def _check_peer(self):
        if getattr(self, "family", None) == getattr(_s, "AF_UNIX", None):
            return
        try:
            peer = self.getpeername()
        except OSError:
            return
        if peer is not None:
            assert_not_blocked(peer)

    def _guarded_send(self, *args, **kwargs):
        _check_peer(self)
        return _ORIG["send"](self, *args, **kwargs)

    def _guarded_sendall(self, *args, **kwargs):
        _check_peer(self)
        return _ORIG["sendall"](self, *args, **kwargs)

    def _guarded_recv(self, *args, **kwargs):
        _check_peer(self)
        return _ORIG["recv"](self, *args, **kwargs)

    def _guarded_recv_into(self, *args, **kwargs):
        _check_peer(self)
        return _ORIG["recv_into"](self, *args, **kwargs)

    _ORIG["guarded"] = {
        "connect": _guarded_connect,
        "connect_ex": _guarded_connect_ex,
        "sendto": _guarded_sendto,
        "create_connection": _guarded_create_connection,
        "send": _guarded_send,
        "sendall": _guarded_sendall,
        "recv": _guarded_recv,
        "recv_into": _guarded_recv_into,
    }

    _s.socket.connect = _guarded_connect
    _s.socket.connect_ex = _guarded_connect_ex
    _s.socket.sendto = _guarded_sendto
    _s.create_connection = _guarded_create_connection
    _s.socket.send = _guarded_send
    _s.socket.sendall = _guarded_sendall
    _s.socket.recv = _guarded_recv
    _s.socket.recv_into = _guarded_recv_into


def pristine(name: str):
    """Return the pre-guard callable captured at install time."""
    return _ORIG[name]


def guarded(name: str):
    """Return the guarding callable that replaced the pristine one."""
    return _ORIG["guarded"][name]


# --------------------------------------------------------------------------
# Child-process audit hook (installed by tests/sitecustomize.py)
# --------------------------------------------------------------------------

_AUDIT_EVENTS = {"socket.connect", "socket.sendto"}

# Audit hooks must never trigger audited events re-entrantly: importing
# socket inside the hook would re-enter the audit system and CPython aborts
# the interpreter silently. Bind the family constant once at module level.
import socket as _socket_for_hook
_AF_UNIX = getattr(_socket_for_hook, "AF_UNIX", None)


def _audit_target_family(event: str, args: tuple):
    target, family = None, None
    if event in ("socket.connect", "socket.sendto") and len(args) >= 2:
        sock, target = args[0], args[1]
        family = getattr(sock, "family", None)
    return target, family


class ChildGuardrailError(RuntimeError):
    """Denial raised by the child-process audit hook.

    A plain RuntimeError subclass defined in THIS module: raising it inside
    the audit hook needs no import (import is an audited event and would
    re-enter the audit system, aborting the interpreter). tests.conftest's
    OfflineTestGuardrailError cannot be imported here for the same reason.
    """


def child_audit_hook(event: str, args: tuple) -> None:
    """PEP 578 hook denying production-port connects in a child process."""
    if event not in _AUDIT_EVENTS:
        return
    target, family = _audit_target_family(event, args)
    if family is not None and _AF_UNIX is not None and family == _AF_UNIX:
        return
    if is_blocked_target(target):
        raise ChildGuardrailError(
            "OfflineTestGuardrail: connection to production service "
            f"{format_target(target)} is forbidden in the test suite "
            "(DAV-2118, child-process audit hook)."
        )


def install_child_hook() -> None:
    """Install the child-process audit hook (called by tests/sitecustomize.py)."""
    import sys

    sys.addaudithook(child_audit_hook)
