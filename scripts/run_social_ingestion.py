#!/usr/bin/env python3
"""Controlled execution and guard runner for MediaCrawler social data ingestion (Task 13 / §3.1 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 13, §3.1, §3.2, §3.3, §4.1, D-008
- Enforces save_option=sqlite (rejects JSONL / others with non-zero exit).
- Enforces loopback host constraint (127.0.0.1 / localhost only).
- Enforces single-task concurrency lock (rejects concurrent second run).
- Pins MediaCrawler commit (default d6f7c5bb906b6dac40ddf343ef9e26438a3de092).
- Default: enable_comments=true, enable_sub_comments=false.
- Strict cookie hygiene: cookie path only, never log or store cookie/token contents.
- Post-run SQLite target table verification.
"""

from __future__ import annotations

import argparse
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

from tradingagents.dataflows.social.mediacrawler_importer import (
    DEFAULT_CRAWLER_COMMIT,
    MediaCrawlerImporter,
    REQUIRED_SOURCE_COLUMNS,
)

# Constants & Defaults
DEFAULT_SAVE_OPTION: str = "sqlite"
DEFAULT_ENABLE_COMMENTS: bool = True
DEFAULT_ENABLE_SUB_COMMENTS: bool = False
DEFAULT_CRAWLER_HOST: str = "127.0.0.1"
DEFAULT_LOCK_FILE: str = "/tmp/mediacrawler_ingestion.lock"
ALLOWED_HOSTS: Set[str] = {"127.0.0.1", "localhost"}

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
        # Resolve hostname to IP address
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
# Crawler Configuration & Ingestion Runner
# ============================================================================

def build_crawler_config(
    platform: str,
    query: str,
    save_option: str = DEFAULT_SAVE_OPTION,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
    enable_sub_comments: bool = DEFAULT_ENABLE_SUB_COMMENTS,
    crawler_host: str = DEFAULT_CRAWLER_HOST,
    crawler_commit: str = DEFAULT_CRAWLER_COMMIT,
    cookie_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build crawler launch configuration dict with strict validation."""
    validate_crawler_host(crawler_host)
    validate_save_option(save_option)

    config = {
        "platform": platform,
        "query": query,
        "save_option": save_option,
        "enable_comments": enable_comments,
        "enable_sub_comments": enable_sub_comments,
        "crawler_host": crawler_host,
        "crawler_commit": crawler_commit,
        "cookie_path": cookie_path,
    }
    return config


def run_social_ingestion(
    platform: str,
    query: str,
    source_db: str,
    archive_db: Optional[str] = None,
    save_option: str = DEFAULT_SAVE_OPTION,
    crawler_host: str = DEFAULT_CRAWLER_HOST,
    crawler_commit: str = DEFAULT_CRAWLER_COMMIT,
    enable_comments: bool = DEFAULT_ENABLE_COMMENTS,
    enable_sub_comments: bool = DEFAULT_ENABLE_SUB_COMMENTS,
    cookie_path: Optional[str] = None,
    lock_file: str = DEFAULT_LOCK_FILE,
    auto_import: bool = False,
    crawler_cmd: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Orchestrate bounded MediaCrawler ingestion with all guards enforced."""
    # 1. Guards validation
    validate_crawler_host(crawler_host)
    validate_save_option(save_option)

    # 2. Concurrency Lock
    with IngestionLock(lock_file):
        start_time = time.time()
        print("=" * 60)
        print("Starting Controlled Social Ingestion")
        print("=" * 60)
        print(f"Platform:           {platform}")
        print(f"Query:              {query}")
        print(f"Save Option:        {save_option}")
        print(f"Crawler Host:       {crawler_host} (Loopback Enforced)")
        print(f"Crawler Commit:     {crawler_commit}")
        print(f"Enable Comments:    {enable_comments}")
        print(f"Enable SubComments: {enable_sub_comments}")
        print(f"Source DB:          {source_db}")
        if cookie_path:
            print(f"Cookie Path:        {cookie_path} (Credentials not logged)")
        print("=" * 60)

        # 3. Optional crawler invocation
        if crawler_cmd:
            print(f"Executing crawler command: {crawler_cmd[0]} ...")
            res = subprocess.run(crawler_cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Crawler subprocess failed with exit code {res.returncode}: {res.stderr}")

        # 4. Verify target SQLite database tables
        validate_source_db_tables(source_db, platform, enable_comments=enable_comments)
        print("✓ Source SQLite database schema verified successfully.")

        # 5. Optional auto-import into TradingAgents append-only archive
        import_summary = None
        if auto_import and archive_db:
            print("Running auto-import to TradingAgents social archive...")
            importer = MediaCrawlerImporter(
                archive_db=archive_db,
                crawler_commit=crawler_commit,
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

        elapsed = time.time() - start_time
        return {
            "status": "success",
            "platform": platform,
            "query": query,
            "elapsed_seconds": round(elapsed, 2),
            "source_db": source_db,
            "archive_db": archive_db,
            "import_summary": import_summary,
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
        default=DEFAULT_CRAWLER_COMMIT,
        help="Pinned MediaCrawler Git commit hash.",
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
        )
        print("Ingestion completed successfully.")
        return 0
    except Exception as exc:
        sys.stderr.write(f"Ingestion Guard Error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
