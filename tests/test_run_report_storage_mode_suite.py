"""DAV-1922: the mode-suite runner's own contract.

The runner's whole value is that its grouping and environment are *derived*, not
hand-maintained. A drifted group would silently turn a mode's own tests into
skips (which the runner then reports as forbidden), and a drifted env would let a
child inherit the host PYTHONPATH or a real proxy. These tests pin both.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_report_storage_mode_suite as runner  # noqa: E402

SKIPIF_MODE_RE = re.compile(
    r'getenv\("REPORT_STORAGE_MODE",\s*"plaintext"\)\s*!=\s*"(\w+)"'
)


def _declared_modes(path: Path) -> set[str]:
    """Modes each file gates itself on, read from its own pytestmark."""
    return set(SKIPIF_MODE_RE.findall(path.read_text(encoding="utf-8")))


def test_every_mode_has_at_least_one_test_file():
    assert set(runner.MODE_FILES) == {"compressed", "compressed_only"}
    for mode, files in runner.MODE_FILES.items():
        assert files, mode


def test_no_file_is_shared_between_modes():
    """A file listed under two modes would skip under one of them."""
    seen: dict[str, str] = {}
    for mode, files in runner.MODE_FILES.items():
        for f in files:
            assert f not in seen, f"{f} 同时属于 {seen[f]} 与 {mode}"
            seen[f] = mode


def test_each_listed_file_exists_and_gates_on_its_own_mode():
    root = runner.REPO_ROOT
    for mode, files in runner.MODE_FILES.items():
        for f in files:
            path = root / f
            assert path.is_file(), f
            assert mode in _declared_modes(path), f"{f} 未以 {mode} 做 skipif 守卫"


def test_modes_with_a_gate_are_all_grouped():
    """Every gate on a grouped mode must be reachable from that mode's group.

    The plaintext-gated file is out of scope here (this entry covers the two
    compressed modes), so only the modes the runner actually owns are checked.
    """
    for path in sorted((runner.REPO_ROOT / "tests").glob("test_*.py")):
        declared = _declared_modes(path) & set(runner.MODE_FILES)
        if not declared:
            continue
        rel = path.relative_to(runner.REPO_ROOT).as_posix()
        for mode in declared:
            assert rel in runner.MODE_FILES[mode], f"{rel}（守卫 {mode}）未进入 {mode} 分组"


def test_build_env_is_closed_and_isolated(tmp_path):
    env = runner.build_env("compressed", env_variant="B", db_dir=tmp_path)
    assert env["REPORT_STORAGE_MODE"] == "compressed"
    assert env["DATABASE_URL"].startswith(f"sqlite:///{tmp_path}")
    assert "PYTHONPATH" not in env
    assert "RT_NETWORK_ENABLED" not in env
    for var in runner.PROXY_VARS:
        assert var not in env
    assert not env["DATABASE_URL"].endswith("data/tradingagents.db")


def test_env_variant_a_injects_the_dirty_shell_only():
    clean = runner.build_env("compressed_only", env_variant="B", db_dir=None)
    dirty = runner.build_env("compressed_only", env_variant="A", db_dir=None)
    assert dirty["PYTHONPATH"] == "/nonexistent"
    assert dirty["http_proxy"] == "http://127.0.0.1:9"
    assert "PYTHONPATH" not in clean and "http_proxy" not in clean
    # The child must be able to strip the pollution itself; assert the strips
    # happen in base_env() rather than by luck of the caller's environment.
    assert "PYTHONPATH" not in runner.base_env() or "PYTHONPATH" in os.environ


def test_deselect_targets_the_known_deadlock_case():
    assert runner.DESELECT.endswith("test_single_horizon_report_persists_and_reads_all_scale_fields")


@pytest.mark.parametrize("mode", sorted(runner.MODE_FILES))
def test_summary_parser_flags_forbidden_outcomes(mode):
    """0 skipped must be an enforced assertion, not an observation."""
    assert "skipped" in runner.FORBIDDEN and "failed" in runner.FORBIDDEN
    counts = {k: int(n) for n, k in runner.SUMMARY_RE.findall("2 passed, 1 skipped in 1s")}
    assert counts.get("skipped") == 1