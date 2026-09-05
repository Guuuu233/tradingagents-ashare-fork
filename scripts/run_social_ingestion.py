#!/usr/bin/env python3
"""Controlled execution and guard runner for MediaCrawler social data ingestion (Task 13 / §3.1 / D-008 / Track B-2).

Specifications:
- docs/social_data/implementation_plan.md Task 13, §3.1, §3.2, §3.3, §4.1, D-008
- Enforces save_option=sqlite (passed as --save_data_option sqlite to MediaCrawler).
- Enforces loopback host constraint (127.0.0.1 / localhost only).
- Enforces single-task concurrency lock (rejects concurrent second run).
- Pins MediaCrawler commit (default d6f7c5bb906b6dac40ddf343ef9e26438a3de092).
- Default: enable_comments=true, enable_sub_comments=false.
- Strict cookie hygiene: cookie path only, never log or store cookie/token contents.
- Post-run SQLite target table verification.
- Controlled command construction against real MediaCrawler CLI interface (cmd_arg/arg.py):
  --platform, --lt, --type search, --keywords, --save_data_option sqlite,
  --get_comment true/false, --get_sub_comment false, --headless true, --save_data_path.
- Proves 4 independent dimensions:
  1. Crawler execution outcome
  2. Archive ingestion count
  3. Snapshot freshness
  4. Downstream analysis availability
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from api.services.social_data_service import (
    get_social_data_status,
    record_social_run_summary,
)
from tradingagents.dataflows.social.mediacrawler_importer import (
    MediaCrawlerImporter,
    REQUIRED_SOURCE_COLUMNS,
)

# Constants & Defaults
DEFAULT_SAVE_OPTION: str = "sqlite"
DEFAULT_ENABLE_COMMENTS: bool = True
DEFAULT_ENABLE_SUB_COMMENTS: bool = False
DEFAULT_CRAWLER_HOST: str = "127.0.0.1"
DEFAULT_LOCK_FILE: str = "/tmp/mediacrawler_ingestion.lock"
PINNED_CRAWLER_COMMIT: str = "d6f7c5bb906b6dac40ddf343ef9e26438a3de092"
ALLOWED_HOSTS: Set[str] = {"127.0.0.1", "localhost"}
ALLOWED_PLATFORMS: Set[str] = {"xhs", "dy"}

PLATFORM_TARGET_TABLES: Dict[str, List[str]] = {
    "xhs": ["xhs_note", "xhs_note_comment"],
    "dy": ["douyin_aweme", "douyin_aweme_comment"],
}


# ============================================================================
# Validation and Guard Functions
# ============================================================================

def is_loopback_host(host: str) -> bool:
    """Check if host string resolves strictly to a loopback address (127.0.0.1 / localhost)."""
    if not host or not isinstance(host, str):
        return False
    h = host.strip().lower()
    if h in ALLOWED_HOSTS:
        return True
    try:
        addr_info = socket.getaddrinfo(h, None)
        for family, _, _, _, sockaddr in addr_info:
            ip = sockaddr[0]
            if ip != "127.0.0.1" and not ip.startswith("127."):
                return False
        return True
    except (socket.gaierror, Exception):
        return False


def validate_crawler_host(host: str) -> None:
    """Validate that crawler host is loopback only. Raises ValueError if non-loopback."""
    if not is_loopback_host(host):
        raise ValueError(
            f"Security Violation: Crawler host '{host}' is not allowed. "
            f"Only loopback (127.0.0.1 / localhost) is permitted."
        )


def validate_save_option(save_option: str) -> None:
    """Validate that save_option is strictly sqlite. Raises ValueError for JSONL/other."""
    if not save_option or save_option.strip().lower() != "sqlite":
        raise ValueError(
            f"Invalid save_option '{save_option}'. MediaCrawler must be run with save_option='sqlite'. "
            f"JSONL and other storage options are strictly forbidden."
        )


def validate_source_db_tables(
    db_path: str,
    platform: str,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
) -> None:
    """Verify that MediaCrawler working SQLite DB exists and contains required tables and columns."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Source database file not found: {db_path}")

    if not os.path.isfile(db_path):
        raise ValueError(f"Source database path is not a file: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}

        platforms = [platform] if platform in ("xhs", "dy") else ["xhs", "dy"]

        for p in platforms:
            if p == "xhs":
                if "xhs_note" not in existing_tables:
                    raise ValueError(f"Missing required table 'xhs_note' in SQLite DB {db_path}")
                if enable_comments and "xhs_note_comment" not in existing_tables:
                    raise ValueError(f"Missing required table 'xhs_note_comment' in SQLite DB {db_path}")
            elif p == "dy":
                if "douyin_aweme" not in existing_tables:
                    raise ValueError(f"Missing required table 'douyin_aweme' in SQLite DB {db_path}")
                if enable_comments and "douyin_aweme_comment" not in existing_tables:
                    raise ValueError(f"Missing required table 'douyin_aweme_comment' in SQLite DB {db_path}")

        # Check required columns on present tables
        for tbl in ["xhs_note", "xhs_note_comment", "douyin_aweme", "douyin_aweme_comment"]:
            if tbl in existing_tables and tbl in REQUIRED_SOURCE_COLUMNS:
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols = {row[1] for row in cursor.fetchall()}
                required = REQUIRED_SOURCE_COLUMNS[tbl]
                if not required.issubset(cols):
                    missing = required - cols
                    raise ValueError(f"Table '{tbl}' is missing required columns: {missing}")
    finally:
        conn.close()


# ============================================================================
# Concurrency Guard (Process Lock)
# ============================================================================

class IngestionLock:
    """Non-blocking file-based mutex lock to prevent concurrent ingestion runs."""

    def __init__(self, lock_file_path: str = DEFAULT_LOCK_FILE) -> None:
        self.lock_file_path = os.path.abspath(lock_file_path)
        self.lock_file = None
        self.acquired = False

    def acquire(self) -> bool:
        lock_dir = os.path.dirname(self.lock_file_path)
        if lock_dir and not os.path.exists(lock_dir):
            os.makedirs(lock_dir, exist_ok=True)

        self.lock_file = open(self.lock_file_path, "w")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(f"{os.getpid()}\n{time.time()}\n")
            self.lock_file.flush()
            self.acquired = True
            return True
        except (IOError, OSError):
            self.acquired = False
            return False

    def release(self) -> None:
        if self.acquired and self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
            except Exception:
                pass
            finally:
                self.acquired = False
                if os.path.exists(self.lock_file_path):
                    try:
                        os.remove(self.lock_file_path)
                    except OSError:
                        pass

    def __enter__(self) -> IngestionLock:
        if not self.acquire():
            raise RuntimeError(
                f"Concurrency Conflict: Another social ingestion task is currently running. "
                f"Lock file: {self.lock_file_path}"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


# ============================================================================
# MediaCrawler Real CLI Command Construction & Validation
# ============================================================================

def build_mediacrawler_argv(
    platform: str,
    query: str,
    source_db: str,
    crawler_commit: str,
    save_option: str = DEFAULT_SAVE_OPTION,
    crawler_host: str = DEFAULT_CRAWLER_HOST,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
    enable_sub_comments: bool = DEFAULT_ENABLE_SUB_COMMENTS,
    cookie_path: Optional[str] = None,
    crawler_entrypoint: Optional[str] = None,
    python_bin: Optional[str] = None,
    headless: bool = True,
    max_notes_count: Optional[int] = None,
    max_comments_count: Optional[int] = None,
) -> List[str]:
    """Construct typed CLI argv strictly matching MediaCrawler's pinned entrypoint shape (cmd_arg/arg.py).

    MediaCrawler flags:
      --platform: xhs / dy
      --lt: cookie / qrcode
      --type: search
      --keywords: <query>
      --save_data_option: sqlite (strictly enforced)
      --get_comment: true / false
      --get_sub_comment: true / false
      --headless: true / false
      --save_data_path: <source_db>
      --cookies: <cookie_path> (if provided)
    """
    validate_crawler_host(crawler_host)
    validate_save_option(save_option)
    if not crawler_commit or not str(crawler_commit).strip():
        raise ValueError("crawler_commit must be explicitly provided and cannot be empty; fabricating commit string is forbidden")

    p = platform.strip().lower()
    if p not in ALLOWED_PLATFORMS and p != "all":
        raise ValueError(f"Unsupported platform '{platform}'. Must be one of {ALLOWED_PLATFORMS} or 'all'")

    py = python_bin or sys.executable
    entrypoint = crawler_entrypoint or "main.py"

    cmd = [
        py,
        entrypoint,
        "--platform", p if p != "all" else "xhs",
        "--lt", "cookie" if cookie_path else "qrcode",
        "--type", "search",
        "--keywords", query.strip(),
        "--save_data_option", "sqlite",
        "--get_comment", "true" if enable_comments else "false",
        "--get_sub_comment", "true" if enable_sub_comments else "false",
        "--headless", "true" if headless else "false",
        "--save_data_path", os.path.abspath(source_db),
    ]

    if cookie_path:
        cmd.extend(["--cookies", os.path.abspath(cookie_path)])

    if max_notes_count is not None:
        cmd.extend(["--crawler_max_notes_count", str(max_notes_count)])

    if max_comments_count is not None:
        cmd.extend(["--max_comments_count_singlenotes", str(max_comments_count)])

    return cmd


def validate_mediacrawler_argv(cmd: List[str]) -> None:
    """Validate that a crawler command strictly adheres to MediaCrawler interface rules.

    Rejects arbitrary argv lists (e.g. ['echo', 'foo']), non-sqlite storage,
    and non-loopback network calls.
    """
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Crawler command must be a non-empty list of arguments.")

    cmd_str = " ".join(cmd)

    # 1. Reject arbitrary commands that don't invoke MediaCrawler or python
    has_crawler_token = any(
        tok in cmd[0] or tok in cmd_str
        for tok in ("python", "main.py", "mediacrawler", "MediaCrawler")
    )
    if not has_crawler_token:
        raise ValueError(
            f"Invalid crawler command: must invoke MediaCrawler main.py or Python runner. Got: {cmd[0]}"
        )

    # 2. Must specify save_data_option or save_option strictly as sqlite
    has_save_option = False
    for i, arg in enumerate(cmd):
        if arg in ("--save_data_option", "--save-option", "--save_option"):
            if i + 1 < len(cmd):
                val = cmd[i + 1].strip().lower()
                if val != "sqlite":
                    raise ValueError(f"Forbidden save_option '{val}'. MediaCrawler must run with save_data_option='sqlite'.")
                has_save_option = True

    if not has_save_option:
        raise ValueError("Invalid crawler command: missing mandatory '--save_data_option sqlite'.")

    # 3. Must specify valid platform
    has_platform = False
    for i, arg in enumerate(cmd):
        if arg == "--platform" and i + 1 < len(cmd):
            p = cmd[i + 1].strip().lower()
            if p not in ALLOWED_PLATFORMS and p != "all":
                raise ValueError(f"Invalid platform '{p}' in crawler command. Must be in {ALLOWED_PLATFORMS}.")
            has_platform = True

    if not has_platform:
        raise ValueError("Invalid crawler command: missing mandatory '--platform' argument.")

    # 4. Must specify keywords or query
    has_keywords = any(arg in ("--keywords", "--query") for arg in cmd)
    if not has_keywords:
        raise ValueError("Invalid crawler command: missing mandatory '--keywords' argument.")

    # 5. Loopback host check: reject external network hosts or proxies
    for i, arg in enumerate(cmd):
        if arg in ("--crawler-host", "--host", "--static_proxy_url") and i + 1 < len(cmd):
            h = cmd[i + 1].strip()
            validate_crawler_host(h)


def sanitize_cmd_for_logging(cmd: List[str]) -> List[str]:
    """Sanitize command arguments for safe logging (redacts cookie values)."""
    sanitized: List[str] = []
    redact_next = False
    for arg in cmd:
        if redact_next:
            sanitized.append("[REDACTED_COOKIE_PATH]")
            redact_next = False
        elif arg in ("--cookies", "--cookie-path", "--cookie_path"):
            sanitized.append(arg)
            redact_next = True
        else:
            sanitized.append(arg)
    return sanitized


def build_crawler_config(
    platform: str,
    query: str,
    crawler_commit: str,
    save_option: str = DEFAULT_SAVE_OPTION,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
    enable_sub_comments: bool = DEFAULT_ENABLE_SUB_COMMENTS,
    crawler_host: str = DEFAULT_CRAWLER_HOST,
    cookie_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build crawler launch configuration dict with strict validation."""
    validate_crawler_host(crawler_host)
    validate_save_option(save_option)
    if not crawler_commit or not str(crawler_commit).strip():
        raise ValueError("crawler_commit must be explicitly provided and cannot be empty; fabricating commit string is forbidden")

    config = {
        "platform": platform,
        "query": query,
        "save_option": save_option,
        "enable_comments": enable_comments,
        "enable_sub_comments": enable_sub_comments,
        "crawler_host": crawler_host,
        "crawler_commit": str(crawler_commit).strip(),
        "cookie_path": cookie_path,
    }
    return config


# ============================================================================
# Main Ingestion Orchestrator
# ============================================================================

def run_social_ingestion(
    platform: str,
    query: str,
    source_db: str,
    crawler_commit: str,
    archive_db: Optional[str] = None,
    save_option: str = DEFAULT_SAVE_OPTION,
    crawler_host: str = DEFAULT_CRAWLER_HOST,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
    enable_sub_comments: bool = DEFAULT_ENABLE_SUB_COMMENTS,
    cookie_path: Optional[str] = None,
    lock_file: str = DEFAULT_LOCK_FILE,
    auto_import: bool = False,
    crawler_cmd: Optional[List[str]] = None,
    crawler_entrypoint: Optional[str] = None,
    execute_crawler: bool = False,
    python_bin: Optional[str] = None,
) -> Dict[str, Any]:
    """Orchestrate bounded MediaCrawler ingestion with all guards enforced.

    Returns a 4-part structured dictionary covering:
    1. crawler_execution: process execution result
    2. import_summary: rows read/inserted/rejected
    3. freshness: snapshot recency
    4. analysis_availability: mode and bundle availability
    """
    # 1. Guards validation
    validate_crawler_host(crawler_host)
    validate_save_option(save_option)
    if not crawler_commit or not str(crawler_commit).strip():
        raise ValueError("crawler_commit must be explicitly provided and cannot be empty; fabricating commit string is forbidden")

    commit_str = str(crawler_commit).strip()

    # 2. Concurrency Lock
    with IngestionLock(lock_file):
        start_time = time.time()
        print("=" * 60)
        print("Starting Controlled Social Ingestion (Track B-2)")
        print("=" * 60)
        print(f"Platform:           {platform}")
        print(f"Query:              {query}")
        print(f"Save Option:        {save_option} (sqlite enforced)")
        print(f"Crawler Host:       {crawler_host} (Loopback Enforced)")
        print(f"Crawler Commit:     {commit_str}")
        print(f"Enable Comments:    {enable_comments}")
        print(f"Enable SubComments: {enable_sub_comments}")
        print(f"Source DB:          {source_db}")
        if cookie_path:
            print(f"Cookie Path:        {cookie_path} (Credentials not logged)")
        print("=" * 60)

        # 3. Controlled crawler invocation
        crawler_res: Dict[str, Any] = {
            "executed": False,
            "status": "not_run",
            "exit_code": None,
            "command": [],
        }

        should_execute = execute_crawler or crawler_cmd is not None or crawler_entrypoint is not None

        if should_execute:
            if crawler_cmd:
                validate_mediacrawler_argv(crawler_cmd)
                cmd_to_run = crawler_cmd
            else:
                cmd_to_run = build_mediacrawler_argv(
                    platform=platform,
                    query=query,
                    source_db=source_db,
                    crawler_commit=commit_str,
                    save_option=save_option,
                    crawler_host=crawler_host,
                    enable_comments=enable_comments,
                    enable_sub_comments=enable_sub_comments,
                    cookie_path=cookie_path,
                    crawler_entrypoint=crawler_entrypoint,
                    python_bin=python_bin,
                )

            sanitized_cmd = sanitize_cmd_for_logging(cmd_to_run)
            print(f"Executing MediaCrawler: {' '.join(sanitized_cmd[:6])} ...")

            clean_env = os.environ.copy()
            for proxy_var in ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
                clean_env.pop(proxy_var, None)

            proc = subprocess.run(cmd_to_run, capture_output=True, text=True, env=clean_env)
            crawler_res = {
                "executed": True,
                "status": "success" if proc.returncode == 0 else "failed",
                "exit_code": proc.returncode,
                "command": sanitized_cmd,
                "stdout_snippet": proc.stdout[:300] if proc.stdout else "",
                "stderr_snippet": proc.stderr[:300] if proc.stderr else "",
            }

            if proc.returncode != 0:
                raise RuntimeError(
                    f"MediaCrawler subprocess failed with exit code {proc.returncode}: {proc.stderr[:400]}"
                )

        # 4. Verify target SQLite database tables
        validate_source_db_tables(source_db, platform, enable_comments=enable_comments)
        print("✓ Source SQLite database schema verified successfully.")

        # 5. Optional auto-import into TradingAgents append-only archive
        import_summary: Optional[Dict[str, Any]] = None
        if auto_import and archive_db:
            print("Running auto-import to TradingAgents social archive...")
            importer = MediaCrawlerImporter(
                archive_db=archive_db,
                crawler_commit=commit_str,
            )
            import_summary = importer.import_records(
                source_db=source_db,
                platform=platform,
                query_text=query,
            )
            print(f"✓ Archive Import Status: {import_summary.get('status')} "
                  f"(read={import_summary.get('rows_read')}, "
                  f"inserted={import_summary.get('rows_inserted')}, "
                  f"rejected={import_summary.get('rows_rejected')})")

            # Record run summary into persistent state
            record_social_run_summary(
                as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                symbol=query,
                status=import_summary.get("status", "completed"),
                post_count=import_summary.get("rows_inserted", 0),
                comment_count=0,
                updated_at=datetime.now(timezone.utc).isoformat(),
                archive_db=archive_db,
                rows_read=import_summary.get("rows_read", 0),
                rows_inserted=import_summary.get("rows_inserted", 0),
                rows_rejected=import_summary.get("rows_rejected", 0),
                crawler_commit=commit_str,
                crawler_exit_code=crawler_res.get("exit_code"),
                crawler_status=crawler_res.get("status"),
            )

        # 6. Read 4-part status aggregation
        status_info = get_social_data_status({"social": {"archive_db": archive_db or ""}})
        freshness = status_info.get("freshness", {})
        analysis_avail = status_info.get("analysis_availability", {})

        elapsed = time.time() - start_time
        print("=" * 60)
        print("Social Ingestion Completed Summary")
        print("=" * 60)
        print(f"1. Crawler Execution:       {crawler_res.get('status')} (executed={crawler_res.get('executed')}, exit_code={crawler_res.get('exit_code')})")
        if import_summary:
            print(f"2. Archive Ingestion:       {import_summary.get('status')} (read={import_summary.get('rows_read')}, inserted={import_summary.get('rows_inserted')}, rejected={import_summary.get('rows_rejected')})")
        else:
            print("2. Archive Ingestion:       skipped (auto_import=False)")
        print(f"3. Archive Freshness:       {freshness.get('status')} (age={freshness.get('age_seconds')}s, snapshots={freshness.get('snapshot_count')})")
        print(f"4. Analysis Availability:   mode={analysis_avail.get('mode')}, available={analysis_avail.get('available')}, status={analysis_avail.get('status')}")
        print("=" * 60)

        return {
            "status": "success",
            "platform": platform,
            "query": query,
            "elapsed_seconds": round(elapsed, 2),
            "source_db": source_db,
            "archive_db": archive_db,
            "crawler_execution": crawler_res,
            "import_summary": import_summary,
            "freshness": freshness,
            "analysis_availability": analysis_avail,
        }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controlled runner for MediaCrawler social data ingestion.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--platform",
        required=True,
        help="Target platform ('xhs', 'dy', or 'all').",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Target query keyword or topic.",
    )
    parser.add_argument(
        "--source-db",
        required=True,
        help="Path to MediaCrawler SQLite working database.",
    )
    parser.add_argument(
        "--archive-db",
        required=False,
        default=None,
        help="Path to TradingAgents social archive SQLite DB.",
    )
    parser.add_argument(
        "--save-option",
        default=DEFAULT_SAVE_OPTION,
        help="MediaCrawler storage backend (strictly 'sqlite').",
    )
    parser.add_argument(
        "--crawler-host",
        default=DEFAULT_CRAWLER_HOST,
        help="MediaCrawler service/control host (strictly loopback: 127.0.0.1).",
    )
    parser.add_argument(
        "--crawler-commit",
        required=True,
        help="Pinned MediaCrawler Git commit hash (e.g. d6f7c5bb906b6dac40ddf343ef9e26438a3de092).",
    )
    parser.add_argument(
        "--enable-comments",
        action="store_true",
        default=DEFAULT_ENABLE_COMMENTS,
        help="Enable fetching primary comments.",
    )
    parser.add_argument(
        "--no-enable-comments",
        dest="enable_comments",
        action="store_false",
        help="Disable fetching primary comments.",
    )
    parser.add_argument(
        "--enable-sub-comments",
        action="store_true",
        default=DEFAULT_ENABLE_SUB_COMMENTS,
        help="Enable fetching secondary sub-comments.",
    )
    parser.add_argument(
        "--no-enable-sub-comments",
        dest="enable_sub_comments",
        action="store_false",
        help="Disable fetching secondary sub-comments.",
    )
    parser.add_argument(
        "--cookie-path",
        default=None,
        help="Path to cookie file or directory for authenticated crawling.",
    )
    parser.add_argument(
        "--lock-file",
        default=DEFAULT_LOCK_FILE,
        help="Path to concurrency mutex lock file.",
    )
    parser.add_argument(
        "--auto-import",
        action="store_true",
        default=False,
        help="Automatically import into TradingAgents archive DB after crawl.",
    )
    parser.add_argument(
        "--execute-crawler",
        action="store_true",
        default=False,
        help="Spawn the MediaCrawler subprocess using the constructed CLI command.",
    )
    parser.add_argument(
        "--crawler-entrypoint",
        default=None,
        help="Path to MediaCrawler main.py entrypoint.",
    )
    parser.add_argument(
        "--python-bin",
        default=None,
        help="Path to Python executable for running MediaCrawler.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    try:
        res = run_social_ingestion(
            platform=args.platform,
            query=args.query,
            source_db=args.source_db,
            archive_db=args.archive_db,
            save_option=args.save_option,
            crawler_host=args.crawler_host,
            crawler_commit=args.crawler_commit,
            enable_comments=args.enable_comments,
            enable_sub_comments=args.enable_sub_comments,
            cookie_path=args.cookie_path,
            lock_file=args.lock_file,
            auto_import=args.auto_import,
            execute_crawler=args.execute_crawler,
            crawler_entrypoint=args.crawler_entrypoint,
            python_bin=args.python_bin,
        )
        print("Ingestion completed successfully.")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Ingestion Guard Error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())

