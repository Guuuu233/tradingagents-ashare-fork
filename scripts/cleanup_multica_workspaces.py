#!/usr/bin/env python3
"""DAV-1754: multica_workspaces task-directory cleanup.

Scans ``~/multica_workspaces*`` for task directories and reports which ones
are safe to delete. A directory is deletable only when ALL of these hold:

1. its owning issue has status ``done`` (via ``multica issue get``);
2. every git checkout inside it is clean (``git status --porcelain`` empty,
   untracked files count as uncommitted changes);
3. no checkout has unpushed commits (``git rev-list HEAD --not --remotes``).

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

# Cap on how many porcelain lines are kept per repo in the report.
_DIRTY_SAMPLE_LINES = 5

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

    Checks ``workdir`` itself plus its immediate subdirectories (the usual
    ``workdir/<repo>`` checkout layout). Returns real paths of directories
    that are git work trees.
    """
    candidates = []
    workdir = os.path.join(task_dir, "workdir")
    if os.path.isdir(workdir):
        candidates.append(workdir)
        try:
            with os.scandir(workdir) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False) and entry.name not in _PRUNE_NAMES:
                        candidates.append(entry.path)
        except OSError:
            pass
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


def classify_task(task_dir: str,
                  fetcher: StatusFetcher = fetch_issue_status) -> Dict:
    """Classify one task directory into deletable / dirty / retained."""
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
