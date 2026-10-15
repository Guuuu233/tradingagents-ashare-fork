"""Interpreter-startup hook for repo-rooted child processes (DAV-2118).

``site`` imports this module automatically at every interpreter startup when
the current directory (repo root) is on ``sys.path`` — which is exactly the
case for every test-spawned child run with ``env=repo_script_env`` and
``cwd`` at the repository root (the DAV-1905 pattern). PEP 578 audit hooks
do not propagate to children, so this file is how the production-port guard
reaches subprocesses that never import ``tests/conftest.py``.

Arming: the child only acts when ``TA_TEST_PORT_GUARD=1`` is present in its
environment, which ``tests/conftest.py::repo_script_env`` exports. A
non-test Python launched from the repo root (e.g. an operator running a
script) sees no flag and installs nothing — production workflows are
unaffected. The guard is fail-closed for test children only by design.

The hook denies outbound connects to the production API listener
(127.0.0.1:8000 / localhost:8000 and loopback aliases) unconditionally in
an armed child — children run for their whole lifetime inside a test, so
there is no legitimate "network-enabled" phase to honor.
"""
from __future__ import annotations

import os

_FLAG = "TA_TEST_PORT_GUARD"


def _install() -> None:
    if os.environ.get(_FLAG) != "1":
        return
    try:
        from tests.production_port_guard import install_child_hook
    except Exception:
        # Repo not importable from this interpreter (different cwd layout or
        # trimmed checkout): stay silent — import-time hooks must never break
        # interpreter startup.
        return
    install_child_hook()


_install()
