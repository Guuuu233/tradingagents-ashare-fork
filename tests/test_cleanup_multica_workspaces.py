"""Tests for scripts.cleanup_multica_workspaces (DAV-1754).

Pure offline unit tests: fake workspace roots under tmp_path, stubbed issue
status fetcher (never calls the real multica CLI), real local git repos for
the dirty/unpushed checks.
"""

from __future__ import annotations

import json
import os
import subprocess

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


def test_done_clean_no_git_is_deletable(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    rec = cmw.classify_task(task, _stub_fetcher({"issue-done": "done"}))
    assert rec["verdict"] == cmw.VERDICT_DELETABLE
    assert rec["issue_status"] == "done"


def test_done_clean_git_is_deletable(tmp_path):
    root = str(tmp_path / "ws")
    task = _make_task(root, "dav-1-aaa", issue_id="issue-done")
    repo = os.path.join(task, "workdir", "proj")
    _init_repo(repo, with_commit=True)
    # Attach a remote tracking the commit so nothing is "unpushed".
    _run_git(repo, "remote", "add", "origin", os.path.join(root, "remote.git"))
    _run_git(root, "init", "--bare", "-q", os.path.join(root, "remote.git"))
    _run_git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    _run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
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
    _make_task(root, "dav-1-aaa", issue_id="issue-done")
    report_path = str(tmp_path / "report.json")
    rc = cmw.main(
        ["--roots", root, "--apply", "--report", report_path],
        fetcher_factory=lambda timeout: _stub_fetcher({"issue-done": "done"}))
    assert rc == 0
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["refused_by_guard"] == []
    assert len(report["deleted"]) == 1
