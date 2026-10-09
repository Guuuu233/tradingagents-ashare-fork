"""Tests for scripts.cleanup_multica_workspaces (DAV-1754).

Pure offline unit tests: fake workspace roots under tmp_path, stubbed issue
status fetcher (never calls the real multica CLI), real local git repos for
the dirty/unpushed checks.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

import scripts.cleanup_multica_workspaces as cmw


def _run_git(repo: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return subprocess.run(
        ["git", *args], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60, check=True, text=True)


def _init_repo(path: str, with_commit: bool = False,
               dirty: bool = False) -> str:
    os.makedirs(path, exist_ok=True)
    _run_git(path, "init", "-q")
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "test")
    if with_commit or dirty:
        with open(os.path.join(path, "file.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("hello\n")
        _run_git(path, "add", "file.txt")
        _run_git(path, "-c", "user.email=test@example.com",
                 "-c", "user.name=test", "commit", "-qm", "init")
    if dirty:
        with open(os.path.join(path, "file.txt"), "a",
                  encoding="utf-8") as fh:
            fh.write("uncommitted\n")
    return path


def _make_task(root: str, name: str, issue_id: str | None = None,
               via_gc_meta: bool = False) -> str:
    task = os.path.join(root, name)
    workdir = os.path.join(task, "workdir")
    os.makedirs(os.path.join(workdir, ".multica"), exist_ok=True)
    if issue_id is not None and not via_gc_meta:
        with open(os.path.join(workdir, ".multica",
                               "daemon_task_context.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"managed_by": "multica-daemon-task",
                       "issue_id": issue_id}, fh)
    if issue_id is not None and via_gc_meta:
        with open(os.path.join(task, ".gc_meta.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"kind": "issue", "issue_id": issue_id}, fh)
    return task


def _stub_fetcher(mapping: dict):
    def fetch(issue_id: str):
        if issue_id in mapping:
            return mapping[issue_id], None
        return None, "fetch-failed:rc=1"
    return fetch


def _backdate_all(path: str, days: float = 2) -> None:
    """Set every file/dir mtime under path to ``days`` ago.

    DAV-1775 fixtures need mtimes outside the 24h protection window to
    reach a deletable verdict on the other conditions."""
    old = time.time() - days * 86400
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for name in dirnames + filenames:
            try:
                os.utime(os.path.join(dirpath, name), (old, old),
                         follow_symlinks=False)
            except OSError:
                pass
    try:
        os.utime(path, (old, old))
    except OSError:
        pass


def _attach_pushed_remote(repo: str, root: str,
                          name: str = "remote.git") -> None:
    """Point repo at a bare remote holding its HEAD (nothing unpushed)."""
    remote = os.path.join(root, name)
    if not os.path.isdir(remote):
        _run_git(root, "init", "--bare", "-q", remote)
    _run_git(repo, "remote", "add", "origin", remote)
    _run_git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_done_clean_no_git_is_deletable(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DELETABLE
    assert rec["issue_status"] == "done"


def test_done_clean_git_is_deletable(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = os.path.join(task, "workdir", "proj")
    _init_repo(repo, with_commit=True)
    # Attach a remote tracking the commit so nothing is "unpushed".
    _attach_pushed_remote(repo, root)
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DELETABLE, rec


def test_dirty_repo_is_dirty_even_when_done(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-2-bbb", issue_id="issue-done")
    _init_repo(os.path.join(task, "workdir", "proj"),
               with_commit=True, dirty=True)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DIRTY
    assert rec["dirty"]


def test_untracked_file_counts_as_dirty(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-2-bbb", issue_id="issue-done")
    repo = _init_repo(os.path.join(task, "workdir", "proj"),
                      with_commit=True)
    with open(os.path.join(repo, "notes.md"), "w",
              encoding="utf-8") as fh:
        fh.write("scratch\n")
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DIRTY


def test_unpushed_commits_are_retained(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-3-ccc", issue_id="issue-done")
    _init_repo(os.path.join(task, "workdir", "proj"), with_commit=True)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert any(r.startswith("unpushed-commits:") for r in rec["reasons"]), rec


def test_not_done_is_retained(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-4-ddd", issue_id="issue-open")
    rec = cmw.classify_task(task,
                            _stub_fetcher({"issue-open": "in_progress"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert any(r.startswith("not-done(") for r in rec["reasons"]), rec


def test_unknown_issue_is_retained(tmp_path):
    root = str(tmp_path / "ws")
    task = os.path.join(root, "dav-5-eee", )
    os.makedirs(os.path.join(task, "workdir"), exist_ok=True)
    rec = cmw.classify_task(task, _stub_fetcher({}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "issue-unknown" in rec["reasons"]


def test_gc_meta_issue_source_is_used(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-6-fff", issue_id="issue-done",
                      via_gc_meta=True)
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["issue_source"] == "gc_meta"
    assert rec["verdict"] == cmw.VERDICT_DELETABLE


def test_discover_finds_task_dirs_not_parents(tmp_path):
    root = str(tmp_path / "multica_workspaces_steer")
    ws = os.path.join(root, "someworks-id")
    _make_task(ws, "dav-1-aaa", issue_id="x")
    _make_task(ws, "dav-2-bbb", issue_id="y")
    found = cmw.discover_task_dirs([root])
    assert sorted(found) == sorted([os.path.realpath(os.path.join(ws, "dav-1-aaa")),
                                    os.path.realpath(os.path.join(ws, "dav-2-bbb"))])


def test_apply_deletes_only_deletable(tmp_path):
    root = str(tmp_path / "ws")
    good = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    bad = _make_task(root, "dav-2-bbb", issue_id="issue-done")
    _init_repo(os.path.join(bad, "workdir", "proj"),
               with_commit=True, dirty=True)
    open_ = _make_task(root, "dav-3-ccc", issue_id="issue-open")
    _backdate_all(good)  # DAV-1775: only old dirs pass the 24h guard
    report_path = str(tmp_path / "report.json")
    rc = cmw.main(
        ["--roots", root, "--apply", "--report", report_path],
        fetcher_factory=lambda timeout: _stub_fetcher(
            {"issue-done": "done", "issue-open": "todo"}))
    assert rc == 0
    assert not os.path.exists(good)
    assert os.path.isdir(bad)  # dirty: never deleted
    assert os.path.isdir(open_)  # not done: retained
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["summary"] == {"deletable": 1, "dirty": 1, "retained": 1}
    assert report["deleted"] == [os.path.realpath(good)]


def test_list_only_deletes_nothing(tmp_path):
    root = str(tmp_path / "ws")
    good = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    rc = cmw.main(
        ["--roots", root],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-done": "done"}))
    assert rc == 0
    assert os.path.isdir(good)


def test_safety_guard_refuses_outside_roots(tmp_path):
    root = str(tmp_path / "ws")
    os.makedirs(root, exist_ok=True)
    outside = str(tmp_path / "elsewhere")
    os.makedirs(outside, exist_ok=True)
    assert cmw.is_safe_to_delete(outside, [os.path.realpath(root)]) is False
    assert cmw.is_safe_to_delete(os.path.realpath(root),
                                 [os.path.realpath(root)]) is False
    inner = os.path.join(root, "dav-1-aaa")
    os.makedirs(inner, exist_ok=True)
    assert cmw.is_safe_to_delete(inner, [os.path.realpath(root)]) is True


def test_exclude_skips_task(tmp_path, capsys):
    root = str(tmp_path / "ws")
    good = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    rc = cmw.main(
        ["--roots", root, "--exclude", good],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-done": "done"}))
    assert rc == 0
    out = capsys.readouterr().out
    assert "deletable (0)" in out


def test_raising_fetcher_retains_dirs_and_completes(tmp_path, capsys):
    # DAV-1758 review finding (red): a raising fetcher must retain the dir,
    # never abort the whole run.
    root = str(tmp_path / "ws")
    t1 = _make_task(root, "dav-1-aaa", issue_id="issue-X")
    t2 = _make_task(root, "dav-2-bbb", issue_id="issue-Y")

    def boom(issue_id: str):
        raise RuntimeError("simulated fetch crash")

    rc = cmw.main(["--roots", root],
                  fetcher_factory=lambda timeout: boom)
    assert rc == 0
    out = capsys.readouterr().out
    assert "deletable (0)" in out
    assert "retained (2)" in out
    assert os.path.isdir(t1) and os.path.isdir(t2)


def test_unexpected_classify_error_is_retained(tmp_path, monkeypatch, capsys):
    # Belt-and-braces: even if classify_task itself raises, main retains it.
    root = str(tmp_path / "ws")
    t1 = _make_task(root, "dav-1-aaa", issue_id="issue-X")

    def flaky(task_dir, fetcher):
        raise RuntimeError("simulated classify crash")

    monkeypatch.setattr(cmw, "classify_task", flaky)
    rc = cmw.main(
        ["--roots", root],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-X": "done"}))
    assert rc == 0
    assert "retained (1)" in capsys.readouterr().out
    assert os.path.isdir(t1)


def test_report_contains_refused_by_guard(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _backdate_all(task)  # DAV-1775: only old dirs pass the 24h guard
    report_path = str(tmp_path / "report.json")
    rc = cmw.main(
        ["--roots", root, "--apply", "--report", report_path],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-done": "done"}))
    assert rc == 0
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["refused_by_guard"] == []
    assert len(report["deleted"]) == 1


def test_guard_refused_annotated_in_place_and_exit_nonzero(tmp_path, capsys):
    # DAV-1758 review finding (yellow): a guard-refused dir must not look
    # like an ordinary deletable entry, and the run must not exit 0.
    root = str(tmp_path / "ws")
    t1 = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _backdate_all(t1)  # DAV-1775: only old dirs pass the 24h guard
    rc = cmw.main(
        ["--roots", root, "--apply"],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-done": "done"}))
    assert rc == 0  # nothing to refuse: real dir under scanned root
    out = capsys.readouterr().out
    assert "[refused-by-guard" not in out

    # Force a refusal by making is_safe_to_delete say no.
    def unsafe(path, roots):
        return False

    t2 = _make_task(root, "dav-9-zzz", issue_id="issue-done")
    _backdate_all(t2)  # DAV-1775: must be deletable to reach the guard
    orig = cmw.is_safe_to_delete
    cmw.is_safe_to_delete = unsafe
    try:
        rc = cmw.main(
            ["--roots", root, "--apply"],
            fetcher_factory=lambda timeout: _stub_fetcher(
                {"issue-done": "done"}))
    finally:
        cmw.is_safe_to_delete = orig
    assert rc == 1
    out = capsys.readouterr().out
    assert "[refused-by-guard: NOT deleted]" in out
    assert "refused by guard 1" in out


def test_fetch_failure_count_reported_and_dirs_retained(tmp_path, capsys):
    # DAV-1758 review suggestion: fetch failures are counted in the report
    # and every affected dir is retained.
    root = str(tmp_path / "ws")
    _make_task(root, "dav-1-aaa", issue_id="issue-X")
    _make_task(root, "dav-2-bbb", issue_id="issue-Y")
    report_path = str(tmp_path / "report.json")
    rc = cmw.main(
        ["--roots", root, "--report", report_path],
        fetcher_factory=lambda timeout: _stub_fetcher({}))
    assert rc == 0
    out = capsys.readouterr().out
    assert "retained (2)" in out
    assert "fetch failures: 2" in out
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["fetch_failures"] == 2


def test_same_issue_fetched_once_per_run(tmp_path):
    # DAV-1758 review suggestion: shared issue lookups are cached.
    root = str(tmp_path / "ws")
    _make_task(root, "dav-1-aaa", issue_id="issue-same")
    _make_task(root, "dav-2-bbb", issue_id="issue-same")
    calls: list = []

    def counting(issue_id: str):
        calls.append(issue_id)
        return "done", None

    rc = cmw.main(
        ["--roots", root],
        fetcher_factory=lambda timeout: counting)
    assert rc == 0
    assert calls == ["issue-same"]


def test_unknown_verdict_fails_safe_to_retained(capsys):
    # DAV-1758 review suggestion: unknown verdicts must be retained,
    # never deletable, even if a future verdict string appears.
    recs = [{"path": "/tmp/x", "issue_id": "i",
             "verdict": "mystery-future", "reasons": ["r"],
             "dirty": []}]
    out = cmw.format_human(recs)
    assert "=== deletable (0) ===" in out
    assert "=== retained (1) ===" in out


# ---------- DAV-1775: ignored artefacts / non-checkout files / 24h guard ---

def _make_repo_ignored(root: str, task: str, ignore_rules: str,
                       ignored_files: dict) -> str:
    """Init a pushed repo with a committed .gitignore + ignored files."""
    repo = os.path.join(task, "workdir", "proj")
    _init_repo(repo, with_commit=True)
    with open(os.path.join(repo, ".gitignore"), "w",
              encoding="utf-8") as fh:
        fh.write(ignore_rules)
    _run_git(repo, "add", ".gitignore")
    _run_git(repo, "-c", "user.email=test@example.com",
             "-c", "user.name=test", "commit", "-qm", "ignore rules")
    for rel, content in ignored_files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    _attach_pushed_remote(repo, root)
    return repo


def test_pure_cache_ignored_is_deletable(tmp_path):
    # DAV-1775 boundary: only cache-like ignored files -> still deletable.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _make_repo_ignored(
        root, task,
        "__pycache__/\n.pytest_cache/\nnode_modules/\n.venv/\n*.egg-info\n"
        ".mypy_cache/\n.ruff_cache/\n",
        {"sub/__pycache__/a.pyc": "x",
         "sub/.pytest_cache/CACHEDIR.TAG": "x",
         "sub/node_modules/b.js": "x",
         os.path.join(".venv", "lib", "c.py"): "x",
         "pkg.egg-info/PKG-INFO": "x",
         ".mypy_cache/d.json": "x",
         ".ruff_cache/e": "x"})
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DELETABLE, rec
    assert rec["ignored_count"] == 0


def test_mixed_ignored_is_retained_with_top5(tmp_path, capsys):
    # DAV-1775: one non-cache ignored file retains; output shows first 5
    # paths plus the total size.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = _make_repo_ignored(
        root, task, "*.log\n__pycache__/\n",
        {"sub/__pycache__/a.pyc": "cache\n",
         **{"f%d.log" % i: "data-%d\n" % i for i in range(7)}})
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "ignored-artifacts" in rec["reasons"]
    assert rec["ignored_count"] == 7
    assert len(rec["ignored_sample"]) == 5
    expected_total = sum(len("data-%d\n" % i) for i in range(7))
    assert rec["ignored_total_bytes"] == expected_total
    assert all(p.startswith(repo) for p in rec["ignored_sample"])
    out = cmw.format_human([rec])
    assert "ignored-artifacts: 7 files, %d bytes" % expected_total in out
    assert "showing first 5" in out


def test_non_repo_files_over_1MB_retained(tmp_path):
    # DAV-1775: workdir files outside any checkout totalling >1MB retain.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = os.path.join(task, "workdir", "proj")
    _init_repo(repo, with_commit=True)
    _attach_pushed_remote(repo, root)
    with open(os.path.join(task, "workdir", "blob.bin"), "wb") as fh:
        fh.write(b"\0" * (cmw.NON_REPO_SIZE_LIMIT_BYTES + 1))
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "non-repo-files>1MB" in rec["reasons"]
    assert rec["non_repo_bytes"] == cmw.NON_REPO_SIZE_LIMIT_BYTES + 1


def test_non_repo_files_at_or_under_1MB_deletable(tmp_path):
    # DAV-1775 boundary: total <= 1MB (incl. exactly 1MB) stays deletable.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = os.path.join(task, "workdir", "proj")
    _init_repo(repo, with_commit=True)
    _attach_pushed_remote(repo, root)
    with open(os.path.join(task, "workdir", "blob.bin"), "wb") as fh:
        fh.write(b"\0" * cmw.NON_REPO_SIZE_LIMIT_BYTES)
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DELETABLE, rec
    assert "non-repo-files>1MB" not in rec["reasons"]


def test_marker_only_shell_is_deletable(tmp_path):
    # DAV-1775: a shell with only marker files counts no non-repo bytes.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    with open(os.path.join(task, ".task_owner"), "w",
              encoding="utf-8") as fh:
        fh.write("owner\n")
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["non_repo_bytes"] == 0
    assert rec["verdict"] == cmw.VERDICT_DELETABLE, rec


def test_recent_mtime_is_retained(tmp_path):
    # DAV-1775: a file touched right now retains the directory.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _backdate_all(task)
    fresh = os.path.join(task, "workdir", "fresh.txt")
    with open(fresh, "w", encoding="utf-8") as fh:
        fh.write("new\n")
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "recently-modified" in rec["reasons"]
    assert rec["recent_path"] == os.path.normpath(fresh)
    out = cmw.format_human([rec])
    assert "recently-modified: %s" % os.path.normpath(fresh) in out


def test_mtime_window_boundary(tmp_path):
    # DAV-1775 boundary: mtime exactly at the 24h cutoff is still recent;
    # 5s older is not (injected clock, no flakiness).
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    target = os.path.join(task, "workdir", "edge.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("edge\n")
    _backdate_all(task, days=3)
    fixed_now = time.time()
    window = cmw.RECENT_MTIME_WINDOW_SECONDS
    os.utime(target, (fixed_now - window, fixed_now - window))
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}),
                            now=fixed_now)
    assert "recently-modified" in rec["reasons"]
    os.utime(target, (fixed_now - window - 5, fixed_now - window - 5))
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}),
                            now=fixed_now)
    assert "recently-modified" not in rec["reasons"]
    assert rec["verdict"] == cmw.VERDICT_DELETABLE, rec


def test_cjk_ignored_filename_is_retained(tmp_path):
    # DAV-1782 red fix: a non-ASCII ignored path must not be silently
    # dropped (core.quotepath octal escapes); it retains the directory.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _make_repo_ignored(root, task, "*.log\n", {"\u65e5\u5fd7.log": "x" * 100})
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["ignored_count"] == 1, rec
    assert rec["ignored_total_bytes"] == 100, rec
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "ignored-artifacts" in rec["reasons"]


def test_arrow_filename_ignored_is_retained(tmp_path):
    # DAV-1782 round 2: a literal " -> " inside an ignored filename must
    # not be split as a rename arrow; the file counts and retains.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    _make_repo_ignored(root, task, "*.log\n", {"a -> b.log": "x" * 50})
    _backdate_all(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["ignored_count"] == 1, rec
    assert rec["ignored_total_bytes"] == 50, rec
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert "ignored-artifacts" in rec["reasons"]


def test_nested_checkout_unpushed_is_retained(tmp_path):
    # DAV-1782 yellow fix: a workdir/<sub>/<repo> checkout is discovered;
    # its unpushed commit retains the dir instead of being misread as
    # non-checkout bytes.
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = os.path.join(task, "workdir", "sub", "proj")
    _init_repo(repo, with_commit=True)
    _backdate_all(task)
    assert repo in cmw.find_git_repos(task)
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_RETAINED
    assert any(r.startswith("unpushed-commits:") for r in rec["reasons"]), rec
