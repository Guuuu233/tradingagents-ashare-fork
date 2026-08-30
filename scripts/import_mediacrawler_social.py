#!/usr/bin/env python3
"""CLI tool for importing MediaCrawler SQLite data into TradingAgents append-only social archive (Task 13 / §3.1 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 13, §3.1, §3.3, §4.1, §4.2, D-008
- Required arguments: --source-db, --archive-db, --platform, --query, --crawler-commit
- Invokes existing MediaCrawlerImporter without rewriting ingestion core.
- Does not print post or comment text to default log levels.
- Non-zero exit code on missing arguments, schema errors, or import failures.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.dataflows.social.mediacrawler_importer import (
    DEFAULT_CRAWLER_COMMIT,
    MediaCrawlerImporter,
)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import MediaCrawler raw data into TradingAgents append-only social archive.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source-db",
        required=True,
        help="Path to MediaCrawler source SQLite database file.",
    )
    parser.add_argument(
        "--archive-db",
        required=True,
        help="Path to TradingAgents social archive SQLite database file.",
    )
    parser.add_argument(
        "--platform",
        required=True,
        help="Platform to import ('xhs', 'dy', 'xhs,dy', or 'all').",
    )
    parser.add_argument(
        "--query",
        required=True,
        help="Query keyword or target topic for ingest audit tracking.",
    )
    parser.add_argument(
        "--crawler-commit",
        required=True,
        help="MediaCrawler Git commit SHA (e.g. d6f7c5bb906b6dac40ddf343ef9e26438a3de092).",
    )
    parser.add_argument(
        "--run-id",
        required=False,
        default=None,
        help="Optional custom UUID string for the ingest run.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    source_db_path = os.path.abspath(args.source_db)
    archive_db_path = os.path.abspath(args.archive_db)

    # Validate source DB exists
    if not os.path.exists(source_db_path):
        sys.stderr.write(f"Error: Source database does not exist: {source_db_path}\n")
        return 1

    if not os.path.isfile(source_db_path):
        sys.stderr.write(f"Error: Source database is not a file: {source_db_path}\n")
        return 1

    # Validate platform
    platform_arg = args.platform.strip().lower()
    valid_platforms = {"xhs", "dy", "all"}
    parsed_platforms = [p.strip() for p in platform_arg.split(",") if p.strip()]
    if not parsed_platforms:
        sys.stderr.write(f"Error: Invalid platform argument: {args.platform}\n")
        return 1

    for p in parsed_platforms:
        if p not in valid_platforms:
            sys.stderr.write(
                f"Error: Unsupported platform '{p}'. Supported platforms are 'xhs', 'dy', or 'all'.\n"
            )
            return 1

    # Ensure archive db parent directory exists
    archive_dir = os.path.dirname(archive_db_path)
    if archive_dir and not os.path.exists(archive_dir):
        try:
            os.makedirs(archive_dir, exist_ok=True)
        except OSError as e:
            sys.stderr.write(f"Error: Failed to create archive directory {archive_dir}: {e}\n")
            return 1

    try:
        importer = MediaCrawlerImporter(
            archive_db=archive_db_path,
            crawler_commit=args.crawler_commit.strip(),
        )
        summary = importer.import_records(
            source_db=source_db_path,
            platform=platform_arg,
            query_text=args.query.strip(),
            ingest_run_id=args.run_id,
        )
    except Exception as exc:
        sys.stderr.write(f"Error: Ingestion execution failed: {exc}\n")
        return 1

    # Print summary without exposing post/comment text
    print("=" * 60)
    print("MediaCrawler Social Ingest Summary")
    print("=" * 60)
    print(f"Run ID:         {summary.get('run_id')}")
    print(f"Status:         {summary.get('status')}")
    print(f"Platform:       {args.platform}")
    print(f"Query:          {args.query}")
    print(f"Crawler Commit: {args.crawler_commit}")
    print(f"Rows Read:      {summary.get('rows_read', 0)}")
    print(f"Rows Inserted:  {summary.get('rows_inserted', 0)}")
    print(f"Rows Rejected:  {summary.get('rows_rejected', 0)}")
    if summary.get("error_code"):
        print(f"Error Code:     {summary.get('error_code')}")
        print(f"Error Detail:   {summary.get('error_detail')}")
    print("=" * 60)

    if summary.get("status") not in ("completed", "success"):
        sys.stderr.write(
            f"Ingest finished with non-success status: {summary.get('status')} "
            f"({summary.get('error_code')}: {summary.get('error_detail')})\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
