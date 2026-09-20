from __future__ import annotations

import faulthandler
import os
import signal
import sys
import time
from pathlib import Path


def _write(kind: str, text: str) -> None:
    path = Path(os.environ.get("DAV990_NODE_LOG", "/private/tmp/dav990-node.log"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(f"{time.time():.6f}\t{kind}\t{text}\n")
        fp.flush()


def pytest_configure(config):
    stack_path = os.environ.get("DAV990_STACK_LOG", "/private/tmp/dav990-stack.log")
    stack_fp = open(stack_path, "a", encoding="utf-8", buffering=1)
    config._dav990_stack_fp = stack_fp
    faulthandler.register(signal.SIGUSR1, file=stack_fp, all_threads=True, chain=False)
    _write("configure", f"pid={os.getpid()} argv={' '.join(sys.argv)}")


def pytest_unconfigure(config):
    fp = getattr(config, "_dav990_stack_fp", None)
    if fp is not None:
        fp.flush()
        fp.close()


def pytest_runtest_logstart(nodeid, location):
    _write("START", nodeid)


def pytest_runtest_logfinish(nodeid, location):
    _write("FINISH", nodeid)
