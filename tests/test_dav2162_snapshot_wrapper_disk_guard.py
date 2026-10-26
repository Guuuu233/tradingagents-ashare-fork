"""DAV-2162 — disk-guard threshold tests for the snapshot wrapper.

``work/ta-snapshot/run_snapshot.py`` skips the daily snapshot when free space
under the snapshot dir drops below ``MIN_FREE_GB`` (now **60 GiB**, aligned
with the 总控 10-16 disk floor; previously 100 GiB, which silently skipped
10-16's run at 80.8 GiB free).

Everything runs against a **sandboxed copy** of the wrapper in the deployment
layout. Data paths use explicit environment overrides, the output directory is
rewritten to a tmp path, and ``shutil.disk_usage`` is stubbed via monkeypatch.
The running interpreter executes a local script that prints canned JSON —
nothing touches the real DB, snapshot dir, or launchd state.

Covers the card's two required cases:

* ``free < 60`` GiB → wrapper writes ``ALERT-disk-<date>.log`` and exits 3
  without running the snapshot;
* ``free >= 60`` GiB → wrapper proceeds past the guard and runs the
  snapshot subprocess (stubbed), writing ``done-<date>.ok``.
"""
import importlib.util
import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "work" / "ta-snapshot" / "run_snapshot.py"


def test_wrapper_is_valid_python():
    assert WRAPPER.is_file()
    import py_compile
    py_compile.compile(str(WRAPPER), doraise=True)


def test_min_free_gb_is_60():
    """The guard threshold must match the 60 GiB disk floor (总控 10-16 令)."""
    import re
    m = re.search(r"^MIN_FREE_GB\s*=\s*(\d+)", WRAPPER.read_text(), re.M)
    assert m, "MIN_FREE_GB constant not found in wrapper"
    assert int(m.group(1)) == 60


# --------------------------------------------------------------------------- #
# Sandbox: rewritten copy of the wrapper, all machine paths → tmp_path        #
# --------------------------------------------------------------------------- #

_STUB_PY = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    \"\"\"Stub for scripts/ta_snapshot.py: print canned report JSON, exit 0.\"\"\"
    import json, sys
    print(json.dumps({"snapshot": "/tmp/x.db.zst", "skipped": "already_exists",
                      "bytes": 123}))
    sys.exit(0)
    """
)


def _load_sandboxed_wrapper(tmp_path, monkeypatch):
    """Load a temporary deployment with a local snapshot-script stub.

    Preserving work/ta-snapshot and scripts exercises code-path resolution.
    A fake summary for today's batch lets the wrapper reach the disk guard.
    """
    from datetime import date

    sb = tmp_path / "sb"
    root = sb / "repo"
    here = root / "work" / "ta-snapshot"
    out = sb / "ta-snapshot"
    batch_dir = sb / "batch" / date.today().isoformat()
    scripts = root / "scripts"
    for d in (here, out, batch_dir, scripts):
        d.mkdir(parents=True, exist_ok=True)
    (batch_dir / "summary.json").write_text("{}")
    (scripts / "ta_snapshot.py").write_text(_STUB_PY)
    monkeypatch.setenv("TA_SNAPSHOT_SOURCE_DB", str(sb / "data" / "db.sqlite"))
    monkeypatch.setenv("TA_SNAPSHOT_BATCH_DIR", str(batch_dir.parent))

    text = WRAPPER.read_text()
    old = 'OUT = os.path.expanduser("~/ta-snapshot")'
    assert old in text, "snapshot output path not found to rewrite"
    text = text.replace(old, f'OUT = {str(out)!r}')

    mod_path = here / "run_snapshot.py"
    mod_path.write_text(text)
    spec = importlib.util.spec_from_file_location(
        "run_snapshot_sandboxed", str(mod_path))
    mod = importlib.util.module_from_spec(spec)
    cwd = os.getcwd()
    try:
        spec.loader.exec_module(mod)  # module body: makedirs + chdir(ROOT=tmp)
    finally:
        os.chdir(cwd)
    return mod, {"here": here, "out": out, "batch_date": date.today().isoformat()}


def _set_free_gb(monkeypatch, mod, free_gb):
    import shutil
    real = shutil.disk_usage
    def fake(path):
        u = real(path)
        return type(u)(u.total, u.used, int(free_gb * 2**30))
    monkeypatch.setattr(shutil, "disk_usage", fake)


@pytest.mark.parametrize("free_gb,expect_rc,expect_alert,expect_done", [
    (59.9, 3, True, False),   # below the 60 GiB floor → skip + alert
    (60.0, 0, False, True),   # at the floor → snapshot proceeds
    (80.8, 0, False, True),   # the 10-16 incident value → must NOT skip now
])
def test_disk_guard_threshold(tmp_path, monkeypatch,
                              free_gb, expect_rc, expect_alert, expect_done):
    mod, env = _load_sandboxed_wrapper(tmp_path, monkeypatch)
    _set_free_gb(monkeypatch, mod, free_gb)
    rc = mod.main()
    assert rc == expect_rc
    alerts = list(env["here"].glob("ALERT-disk-*.log"))
    assert bool(alerts) == expect_alert
    if expect_alert:
        assert "60" in alerts[0].read_text()  # alert names the 60 GiB floor
    done = env["here"] / f"done-{env['batch_date']}.ok"
    assert done.exists() == expect_done
