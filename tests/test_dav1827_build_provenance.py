"""DAV-1827: build_phase2_chunks stats.json provenance fields.

stats.json must record the source tree's git HEAD SHA and the script file's
sha256 so any chunk build is traceable to the exact code that produced it.

Pure offline tests; no cache, no network.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import scripts.phase2.build_phase2_chunks as bc


def test_script_sha256_matches_on_disk_file():
    prov = bc.build_provenance()
    script = Path(bc.__file__).resolve()
    assert prov["script_sha256"] == hashlib.sha256(
        script.read_bytes()).hexdigest()


def test_git_sha_matches_repo_head_and_dirty_flag_is_bool(repo_script_env):
    prov = bc.build_provenance()
    head = subprocess.run(["git", "rev-parse", "HEAD"],
                          cwd=Path(bc.__file__).resolve().parents[2],
                          capture_output=True, text=True,
                          env=repo_script_env)
    assert head.returncode == 0
    assert prov["git_sha"] == head.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", prov["git_sha"])
    assert isinstance(prov["git_dirty"], bool)


def test_provenance_degrades_outside_git(monkeypatch, tmp_path):
    """Outside a git checkout (or without git), fields must be None — never crash."""
    import scripts.phase2.build_phase2_chunks as m

    def _fail(*a, **kw):
        raise OSError("git not found")

    monkeypatch.setattr(m.subprocess, "run", _fail)
    prov = m.build_provenance()
    assert prov["git_sha"] is None
    assert prov["git_dirty"] is None
    assert prov["script_sha256"] == hashlib.sha256(
        Path(m.__file__).resolve().read_bytes()).hexdigest()
    assert set(prov) == {"script", "script_sha256", "git_sha", "git_dirty",
                         "recorded_at"}
