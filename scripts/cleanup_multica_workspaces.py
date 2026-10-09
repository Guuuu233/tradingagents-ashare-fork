#!/usr/bin/env python3
"""DAV-1754: multica_workspaces task-directory cleanup.

Scans ``~/multica_workspaces*`` for task directories and reports which ones
are safe to delete. A directory is deletable only when ALL of these hold:

1. its owning issue has status ``done`` (via ``multica issue get``);
2. every git checkout inside it is clean (``git status --porcelain`` empty,
   untracked files count as uncommitted changes);
3. no checkout has unpushed commits (``git rev-list HEAD --not --remotes``).
4. no checkout holds non-cache ignored files (``git status --porcelain
   --ignored`` minus cache-like paths such as ``__pycache__``);
5. files under the workdir that belong to no git checkout total at most
   1 MiB (marker-only shells are fine);
6. no file under the task directory was modified within the last 24 hours.

Default behaviour is list-only: deletable / dirty / retained are printed in
separate sections and nothing is removed. ``--apply`` deletes the deletable
directories with ``rm -rf`` semantics. Directories with uncommitted changes
are NEVER deleted, even with ``--apply``.

NOT in scope: installing any scheduled job (cron/launchd). This script only
lists and optionally deletes; scheduling is a separate decision.

Usage:
    python scripts/cleanup_multica_workspaces.py [--roots GLOB ...]
    python scripts/cleanup_multica_workspaces.py --apply --report /tmp/cleanup.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

VERDICT_DELETABLE = "deletable"
VERDICT_DIRTY = "dirty"  # uncommitted changes: listed separately, never deleted
VERDICT_RETAINED = "retained"

# A task directory is recognised by any of these markers. ``workdir/`` covers
# both daemon layouts (steer / classic / desktop); ``.task_owner`` and
# ``.gc_meta.json`` cover tasks whose workdir was already removed.
TASK_MARKER_FILES = (".task_owner", ".gc_meta.json",
                     ".multica_sidecar_manifest.json")

DAEMON_CONTEXT_RELPATH = os.path.join(
    "workdir", ".multica", "daemon_task_context.json")
GC_META_FILENAME = ".gc_meta.json"

# Directories never descended into during discovery (keeps the walk cheap).
_PRUNE_NAMES = {".git", "codex-home", "node_modules", "__pycache__",
                ".venv310", ".venv"}

# DAV-1782: checkout discovery descends this many levels below workdir, so
# ``workdir/<sub>/<repo>`` layouts are found (depth 2) with headroom.
_FIND_REPOS_MAX_DEPTH = 4

# Cap on how many porcelain lines are kept per repo in the report.
_DIRTY_SAMPLE_LINES = 5

# DAV-1775: ignored paths whose every component matches one of these are
# cache-like build artefacts and do NOT block deletion. ``.venv*`` and
# ``*.egg-info`` are prefix/suffix matches, the rest are exact names.
_CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", "node_modules",
                    ".mypy_cache", ".ruff_cache"}

# DAV-1775: cap on ignored / non-checkout sample paths kept per record.
_SAMPLE_LINES = 5

# DAV-1775: files under the workdir that belong to no git checkout retain
# the directory once their total size strictly exceeds this (1 MiB).
NON_REPO_SIZE_LIMIT_BYTES = 1024 * 1024

# DAV-1775: any file modified within this window retains the directory.
RECENT_MTIME_WINDOW_SECONDS = 24 * 3600

StatusFetcher = Callable[[str], Tuple[Optional[str], Optional[str]]]
# Returns (status, error). status is the raw ``status`` field of
# ``multica issue get`` (e.g. "done"); error is a short reason when the
# lookup failed.


def resolve_roots(patterns: List[str]) -> List[str]:
    """Expand root glob patterns to sorted, existing, real directories."""
    found: List[str] = []
    for pattern in patterns:
        expanded = os.path.expanduser(os.path.expandvars(pattern))
        for path in sorted(glob.glob(expanded)):
            real = os.path.realpath(path)
            if os.path.isdir(real) and real not in found:
                found.append(real)
    return found


def _is_task_dir(path: str) -> bool:
    """Check whether a directory looks like a multica task directory."""
    try:
        if os.path.isdir(os.path.join(path, "workdir")):
            return True
        for marker in TASK_MARKER_FILES:
            if os.path.isfile(os.path.join(path, marker)):
                return True
    except OSError:
        return False
    return False


def discover_task_dirs(roots: List[str], max_depth: int = 4) -> List[str]:
    """Find task directories up to ``max_depth`` below each root.

    Sequential ``os.scandir`` walk; task directories are not descended into.
    Memory use is O(depth), not O(tree size).
    """
    found: List[str] = []
    seen: set = set()
    for root in roots:
        stack: List[Tuple[str, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth >= max_depth:
                continue
            try:
                with os.scandir(current) as entries:
                    children = [e for e in entries if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for entry in children:
                if entry.name in _PRUNE_NAMES:
                    continue
                child = entry.path
                if _is_task_dir(child):
                    real = os.path.realpath(child)
                    if real not in seen:
                        seen.add(real)
                        found.append(real)
                    # Do not descend into task dirs.
                else:
                    stack.append((child, depth + 1))
    return sorted(found)


def read_issue_id(task_dir: str) -> Tuple[Optional[str], str]:
    """Extract the owning issue id for a task directory.

    Returns (issue_id, source). source is one of ``daemon_context``,
    ``gc_meta``, ``none``.
    """
    context_path = os.path.join(task_dir, DAEMON_CONTEXT_RELPATH)
    try:
        with open(context_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        issue_id = data.get("issue_id")
        if issue_id:
            return str(issue_id), "daemon_context"
    except (OSError, ValueError):
        pass
    gc_meta_path = os.path.join(task_dir, GC_META_FILENAME)
    try:
        with open(gc_meta_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        issue_id = data.get("issue_id")
        if issue_id:
            return str(issue_id), "gc_meta"
    except (OSError, ValueError):
        pass
    return None, "none"


def fetch_issue_status(issue_id: str, timeout: int = 60) -> Tuple[Optional[str], Optional[str]]:
    """Query issue status via the multica CLI (read-only)."""
    try:
        proc = subprocess.run(
            ["multica", "issue", "get", issue_id, "--output", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False, text=True)
    except FileNotFoundError:
        return None, "multicaCLI-not-found"
    except subprocess.TimeoutExpired:
        return None, "fetch-timeout"
    except OSError as exc:
        return None, "fetch-error:%s" % type(exc).__name__
    if proc.returncode != 0:
        return None, "fetch-failed:rc=%d" % proc.returncode
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "fetch-failed:bad-json"
    status = data.get("status")
    if not status:
        return None, "fetch-failed:no-status"
    return str(status), None


def find_git_repos(task_dir: str) -> List[str]:
    """Locate git checkouts belonging to a task directory.

    Walks ``workdir`` (depth-limited) so nested ``workdir/<sub>/<repo>``
    checkouts are found, not just the top level; ``_PRUNE_NAMES``
    directories are skipped and checkouts are never descended into.
    Also considers the task dir itself (some layouts keep a repo at top).
    Returns real paths of directories that are git work trees.
    """
    candidates = []
    workdir = os.path.join(task_dir, "workdir")
    if os.path.isdir(workdir):
        candidates.append(workdir)
        for dirpath, dirnames, _filenames in os.walk(
                workdir, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_NAMES]
            dotgit = os.path.join(dirpath, ".git")
            if os.path.isdir(dotgit) or os.path.isfile(dotgit):
                if dirpath != workdir:
                    candidates.append(dirpath)
                dirnames[:] = []  # never descend into a checkout
                continue
            rel = os.path.relpath(dirpath, workdir)
            depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
            if depth >= _FIND_REPOS_MAX_DEPTH:
                dirnames[:] = []
    # Also consider the task dir itself (some layouts keep a repo at top).
    candidates.append(task_dir)
    repos: List[str] = []
    seen: set = set()
    for path in candidates:
        try:
            proc = subprocess.run(
                ["git", "-C", path, "rev-parse", "--show-toplevel"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=30, check=False, text=True)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        top = os.path.realpath(proc.stdout.strip())
        if top and top not in seen:
            seen.add(top)
            repos.append(top)
    return sorted(repos)


def git_is_dirty(repo: str) -> Tuple[bool, List[str]]:
    """Check for uncommitted changes (staged, unstaged, or untracked)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain=v1",
             "--untracked-files=normal"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired):
        # Cannot prove clean: treat as dirty so the dir is never deleted.
        return True, ["git-status-unavailable"]
    if proc.returncode != 0:
        return True, ["git-status-unavailable"]
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    return (len(lines) > 0), lines[:_DIRTY_SAMPLE_LINES]


def git_has_unpushed(repo: str) -> Tuple[bool, Optional[str]]:
    """Check for local commits unreachable from any remote."""
    try:
        head = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--verify", "--quiet", "HEAD"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return True, "git-head-unavailable"
    if head.returncode != 0:
        return False, None  # No commits at all: nothing to push.
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "rev-list", "--count", "HEAD",
             "--not", "--remotes"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return True, "git-rev-list-unavailable"
    if proc.returncode != 0:
        return True, "git-rev-list-unavailable"
    try:
        return (int(proc.stdout.strip()) > 0), None
    except ValueError:
        return True, "git-rev-list-unavailable"


def _is_cache_ignored_path(rel_path: str) -> bool:
    """Check whether an ignored path is a cache-like build artefact."""
    for part in rel_path.replace("\\", "/").split("/"):
        if not part:
            continue
        if part in _CACHE_DIR_NAMES:
            return True
        if part.startswith(".venv"):
            return True
        if part.endswith(".egg-info"):
            return True
    return False


def git_ignored_artifacts(repo: str) -> Tuple[List[str], int, Optional[str]]:
    """List non-cache ignored files under a repo.

    Runs ``git status --porcelain --ignored`` and drops cache-like paths
    (``__pycache__`` etc.). Ignored directories (``!! logs/``) are expanded
    so every file is individually filtered and sized. Returns
    (sorted_absolute_paths, total_bytes, error).
    """
    try:
        # DAV-1782: core.quotepath=false keeps non-ASCII paths as raw
        # UTF-8 (otherwise C-style octal escapes leave a bogus literal
        # path that lexists rejects, silently dropping the file); -z
        # gives NUL-separated records so no unquoting is needed at all.
        proc = subprocess.run(
            ["git", "-C", repo, "-c", "core.quotepath=false",
             "status", "--porcelain", "--ignored", "-z"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=60, check=False, text=True)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return [], 0, "ignored-check-unavailable"
    if proc.returncode != 0:
        return [], 0, "ignored-check-unavailable"
    rels: List[str] = []
    for entry in proc.stdout.split("\0"):
        if len(entry) < 4 or not entry.startswith("!!"):
            continue
        # "-z" layout is "XY <path>" with no quoting; rename arrows
        # cannot occur for ignored entries but take the target defensively.
        path = entry[3:]
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        path = path.strip()
        if path:
            rels.append(path)
    paths: List[str] = []
    total = 0

    def _add_file(abs_path: str) -> None:
        nonlocal total
        rel = os.path.relpath(abs_path, repo)
        if _is_cache_ignored_path(rel):
            return
        paths.append(os.path.normpath(abs_path))
        try:
            total += os.path.getsize(abs_path)
        except OSError:
            pass

    for rel in rels:
        if _is_cache_ignored_path(rel):
            continue
        abs_path = os.path.join(repo, rel)
        try:
            if os.path.isdir(abs_path) and not os.path.islink(abs_path):
                for dirpath, _dirnames, filenames in os.walk(
                        abs_path, followlinks=False):
                    for name in filenames:
                        _add_file(os.path.join(dirpath, name))
            elif os.path.lexists(abs_path):
                _add_file(abs_path)
        except OSError:
            continue
    return sorted(paths), total, None


def _inside_any_repo(path_real: str, repo_roots: List[str]) -> bool:
    for root in repo_roots:
        if path_real == root or path_real.startswith(root + os.sep):
            return True
    return False


def non_repo_files(task_dir: str,
                   repos: List[str]) -> Tuple[List[str], int, int]:
    """Size up files that belong to no git checkout.

    Scope is ``task_dir/workdir`` when present, else ``task_dir`` itself.
    Task markers (``.task_owner`` etc.) and ``.multica`` control metadata
    are shells, not user data, and never count. Returns
    (sorted_sample_paths, total_bytes, file_count).
    """
    scope = os.path.join(task_dir, "workdir")
    if not os.path.isdir(scope):
        scope = task_dir
    repo_roots = [os.path.realpath(r) for r in repos]
    sample: List[str] = []
    total = 0
    count = 0
    stack = [scope]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        for entry in children:
            try:
                if entry.name == ".multica":
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if _inside_any_repo(os.path.realpath(entry.path),
                                        repo_roots):
                        continue
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    if entry.name in TASK_MARKER_FILES:
                        continue
                    if _inside_any_repo(os.path.realpath(entry.path),
                                        repo_roots):
                        continue
                    count += 1
                    try:
                        total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                    if len(sample) < _SAMPLE_LINES:
                        sample.append(os.path.normpath(entry.path))
            except OSError:
                continue
    return sorted(sample), total, count


def find_recent_file(task_dir: str, now: float,
                     window: int = RECENT_MTIME_WINDOW_SECONDS
                     ) -> Optional[Tuple[str, float]]:
    """Find a file modified within ``window`` seconds of ``now``.

    Covers every file under the task directory (fail-safe: any recent
    activity retains it), except ``.git/index``: read-only ``git status``
    rewrites the index (stat refresh), so counting it would make the
    guard self-trigger on every scan. Other ``.git`` internals (refs,
    objects) are only written by real repo mutations and still count.
    Returns (path, mtime) or None.
    """
    cutoff = now - window
    stack = [task_dir]
    while stack:
        current = stack.pop()
        in_git_dir = os.path.basename(current) == ".git"
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            continue
        for entry in children:
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    if in_git_dir and entry.name == "index":
                        continue
                    try:
                        mtime = entry.stat(
                            follow_symlinks=False).st_mtime
                    except OSError:
                        continue
                    if mtime >= cutoff:
                        return os.path.normpath(entry.path), mtime
            except OSError:
                continue
    return None


def classify_task(task_dir: str,
                  fetcher: StatusFetcher = fetch_issue_status,
                  now: Optional[float] = None) -> Dict:
    """Classify one task directory into deletable / dirty / retained."""
    if now is None:
        now = time.time()
    record: Dict = {"path": task_dir, "issue_id": None,
                    "issue_source": "none", "issue_status": None,
                    "git_repos": [], "dirty": [],
                    "unpushed": [], "verdict": VERDICT_RETAINED,
                    "reasons": []}
    issue_id, source = read_issue_id(task_dir)
    record["issue_id"] = issue_id
    record["issue_source"] = source
    if issue_id is None:
        record["reasons"].append("issue-unknown")
    else:
        try:
            status, error = fetcher(issue_id)
        except Exception as exc:  # fail-safe: one bad lookup retains the dir
            record["reasons"].append("fetch-error:%s" % type(exc).__name__)
        else:
            if error is not None:
                record["reasons"].append(error)
            else:
                record["issue_status"] = status
                if status != "done":
                    record["reasons"].append("not-done(%s)" % status)

    repos = find_git_repos(task_dir)
    record["git_repos"] = repos
    dirty_hits: List[str] = []
    unpushed_hits: List[str] = []
    for repo in repos:
        dirty, sample = git_is_dirty(repo)
        if dirty:
            detail = "%s%s" % (repo, (": " + "; ".join(sample)) if sample else "")
            dirty_hits.append(detail)
            continue  # A dirty repo's push state is irrelevant.
        has_unpushed, err = git_has_unpushed(repo)
        if has_unpushed:
            unpushed_hits.append("%s%s" % (repo, ("(%s)" % err) if err else ""))
    record["dirty"] = dirty_hits
    record["unpushed"] = unpushed_hits
    # DAV-1775: ignored artefacts keep the directory, whatever else holds.
    ignored_all: List[str] = []
    ignored_total = 0
    for repo in repos:
        paths, size, err = git_ignored_artifacts(repo)
        if err is not None:
            # Fail-safe: an unreadable ignored-state retains the dir.
            record["reasons"].append(err)
        else:
            ignored_all.extend(paths)
            ignored_total += size
    ignored_all.sort()
    record["ignored_sample"] = ignored_all[:_SAMPLE_LINES]
    record["ignored_count"] = len(ignored_all)
    record["ignored_total_bytes"] = ignored_total
    if ignored_all:
        record["reasons"].append("ignored-artifacts")
    # DAV-1775: files outside any checkout above 1 MiB keep the directory.
    non_repo_sample, non_repo_bytes, non_repo_count = non_repo_files(
        task_dir, repos)
    record["non_repo_sample"] = non_repo_sample
    record["non_repo_bytes"] = non_repo_bytes
    record["non_repo_count"] = non_repo_count
    if non_repo_bytes > NON_REPO_SIZE_LIMIT_BYTES:
        record["reasons"].append("non-repo-files>1MB")
    # DAV-1775: any file touched within 24h keeps the directory.
    recent = find_recent_file(task_dir, now)
    record["recent_path"] = recent[0] if recent else None
    record["recent_mtime"] = recent[1] if recent else None
    if recent is not None:
        record["reasons"].append("recently-modified")
    if dirty_hits:
        record["verdict"] = VERDICT_DIRTY
        record["reasons"].append("uncommitted-changes")
    elif record["reasons"] or unpushed_hits:
        record["verdict"] = VERDICT_RETAINED
        record["reasons"].extend("unpushed-commits:%s" % h for h in unpushed_hits)
    else:
        record["verdict"] = VERDICT_DELETABLE
        record["reasons"].append("done+clean+no-unpushed")
    return record


def is_safe_to_delete(path: str, roots: List[str]) -> bool:
    """Guard: only delete real directories strictly below a scan root."""
    try:
        real = os.path.realpath(path)
        if not os.path.isdir(real):
            return False
        for root in roots:
            rreal = os.path.realpath(root)
            if real != rreal and real.startswith(rreal + os.sep):
                return True
    except OSError:
        return False
    return False


def delete_task_dir(path: str) -> Optional[str]:
    """Remove a task directory (rm -rf semantics). Returns error or None."""
    try:
        shutil.rmtree(path, ignore_errors=False)
        return None
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, exc)


def format_human(records: List[Dict],
                 refused: Optional[List[str]] = None) -> str:
    """Render the three sections: deletable / dirty / retained.

    ``refused`` holds paths judged deletable but NOT deleted because the
    safety guard rejected them (``--apply`` only); they are annotated in
    place so the deletable section never implies they were removed.
    Records with an unknown verdict are retained (fail-safe), never deleted.
    """
    refused_set = set(refused or [])
    by_verdict: Dict[str, List[Dict]] = {
        VERDICT_DELETABLE: [], VERDICT_DIRTY: [], VERDICT_RETAINED: []}
    for rec in records:
        verdict = rec.get("verdict")
        if verdict in by_verdict:
            by_verdict[verdict].append(rec)
        else:
            # Fail-safe default: unknown verdicts are retained, never deleted.
            by_verdict[VERDICT_RETAINED].append(rec)
    out: List[str] = []
    deletable = by_verdict[VERDICT_DELETABLE]
    out.append("=== deletable (%d) ===" % len(deletable))
    for rec in deletable:
        if rec["path"] in refused_set:
            out.append("  %s  [issue %s done] [refused-by-guard: NOT deleted]"
                       % (rec["path"], rec["issue_id"]))
        else:
            out.append("  %s  [issue %s done]" % (rec["path"], rec["issue_id"]))
    if not deletable:
        out.append("  (none)")
    dirty = by_verdict[VERDICT_DIRTY]
    out.append("=== uncommitted-changes, NEVER deleted (%d) ===" % len(dirty))
    for rec in dirty:
        out.append("  %s" % rec["path"])
        for hit in rec["dirty"]:
            out.append("    dirty: %s" % hit)
    if not dirty:
        out.append("  (none)")
    retained = by_verdict[VERDICT_RETAINED]
    out.append("=== retained (%d) ===" % len(retained))
    for rec in retained:
        out.append("  %s  [reason: %s]" % (rec["path"], "; ".join(rec["reasons"])))
        if rec.get("ignored_count"):
            out.append("    ignored-artifacts: %d files, %d bytes; "
                       "showing first %d" % (
                           rec["ignored_count"],
                           rec.get("ignored_total_bytes", 0),
                           len(rec.get("ignored_sample", []))))
            for path in rec.get("ignored_sample", []):
                out.append("      %s" % path)
        if any(r == "non-repo-files>1MB" for r in rec["reasons"]):
            out.append("    non-repo-files: %d files, %d bytes (>1MB); "
                       "showing first %d" % (
                           rec.get("non_repo_count", 0),
                           rec.get("non_repo_bytes", 0),
                           len(rec.get("non_repo_sample", []))))
            for path in rec.get("non_repo_sample", []):
                out.append("      %s" % path)
        if rec.get("recent_path"):
            out.append("    recently-modified: %s" % rec["recent_path"])
    if not retained:
        out.append("  (none)")
    return "\n".join(out) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List (default) or delete (--apply) removable "
                    "multica_workspaces task directories.")
    parser.add_argument("--roots", nargs="+",
                        default=[os.path.join("~", "multica_workspaces*")],
                        help="Glob patterns for workspace roots "
                             "(default: ~/multica_workspaces*).")
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Task directories to skip (exact paths).")
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete deletable directories. "
                             "Without it, only a list is printed.")
    parser.add_argument("--json", action="store_true",
                        help="Print the full JSON report to stdout "
                             "instead of the human-readable list.")
    parser.add_argument("--report", default=None,
                        help="Write the full JSON report to this file.")
    parser.add_argument("--issue-timeout", type=int, default=60,
                        help="Seconds per 'multica issue get' call.")
    return parser


def main(argv: Optional[List[str]] = None,
         fetcher_factory: Optional[Callable[[int], StatusFetcher]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    roots = resolve_roots(args.roots)
    if not roots:
        print("no workspace roots matched: %s" % (" ".join(args.roots)),
              file=sys.stderr)
        return 2
    excluded = {os.path.realpath(os.path.expanduser(p)) for p in args.exclude}

    def default_factory(timeout: int) -> StatusFetcher:
        return lambda issue_id: fetch_issue_status(issue_id, timeout=timeout)

    base_fetcher = (fetcher_factory or default_factory)(args.issue_timeout)
    # Per-run cache (DAV-1758 review suggestion): several task dirs may belong
    # to the same issue; one lookup per issue_id keeps long scans fast and the
    # verdicts within a run consistent. Failures are cached as well so one bad
    # issue cannot stall the run with a timeout per directory.
    fetch_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}

    def fetcher(issue_id: str) -> Tuple[Optional[str], Optional[str]]:
        if issue_id not in fetch_cache:
            fetch_cache[issue_id] = base_fetcher(issue_id)
        return fetch_cache[issue_id]
    task_dirs = discover_task_dirs(roots)
    records: List[Dict] = []
    for index, task_dir in enumerate(task_dirs, 1):
        if os.path.realpath(task_dir) in excluded:
            continue
        print("  [%d/%d] %s" % (index, len(task_dirs), task_dir),
              file=sys.stderr, flush=True)
        try:
            records.append(classify_task(task_dir, fetcher))
        except Exception as exc:  # fail-safe: single-dir failure retains it
            print("  classify failed for %s (%s); retaining"
                  % (task_dir, type(exc).__name__),
                  file=sys.stderr, flush=True)
            records.append({"path": task_dir, "issue_id": None,
                            "issue_source": "none", "issue_status": None,
                            "git_repos": [], "dirty": [],
                            "unpushed": [], "verdict": VERDICT_RETAINED,
                            "reasons": ["classify-error:%s"
                                          % type(exc).__name__]})
    records.sort(key=lambda r: r["path"])

    summary = {
        "deletable": sum(1 for r in records if r["verdict"] == VERDICT_DELETABLE),
        "dirty": sum(1 for r in records if r["verdict"] == VERDICT_DIRTY),
        "retained": sum(1 for r in records if r["verdict"] == VERDICT_RETAINED),
    }
    fetch_failures = sum(
        1 for r in records
        if any(reason.startswith(("fetch-", "classify-"))
               or reason == "multicaCLI-not-found"
               for reason in r["reasons"]))
    deleted: List[str] = []
    delete_errors: List[str] = []
    refused: List[str] = []
    if args.apply:
        for rec in records:
            if rec["verdict"] != VERDICT_DELETABLE:
                continue
            if not is_safe_to_delete(rec["path"], roots):
                refused.append(rec["path"])
                print("refused by safety guard %s" % rec["path"],
                      file=sys.stderr, flush=True)
                continue
            err = delete_task_dir(rec["path"])
            if err is None:
                deleted.append(rec["path"])
                print("deleted %s" % rec["path"], flush=True)
            else:
                delete_errors.append("%s: %s" % (rec["path"], err))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "roots": roots,
        "apply": bool(args.apply),
        "summary": summary,
        "fetch_failures": fetch_failures,
        "deleted": deleted,
        "delete_errors": delete_errors,
        "refused_by_guard": refused,
        "results": records,
    }
    if args.report:
        try:
            with open(os.path.expanduser(args.report), "w", encoding="utf-8") as fh:
                json.dump(report, fh, ensure_ascii=False, indent=2)
            print("report written to %s" % args.report, file=sys.stderr)
        except OSError as exc:
            print("cannot write report: %s" % exc, file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(format_human(records, refused))
        sys.stdout.write("summary: %(deletable)d deletable, "
                         "%(dirty)d with uncommitted changes (never deleted), "
                         "%(retained)d retained\n" % summary)
        if fetch_failures:
            sys.stdout.write("fetch failures: %d "
                             "(affected dirs retained, see reasons)\n"
                             % fetch_failures)
        if args.apply:
            sys.stdout.write("deleted %d, errors %d, refused by guard %d\n"
                             % (len(deleted), len(delete_errors),
                                len(refused)))
            for err in delete_errors:
                sys.stdout.write("  ERROR %s\n" % err)
            for path in refused:
                sys.stdout.write("  REFUSED (still listed deletable, "
                                 "not deleted) %s\n" % path)
    return 0 if not (delete_errors or refused) else 1


if __name__ == "__main__":
    sys.exit(main())
