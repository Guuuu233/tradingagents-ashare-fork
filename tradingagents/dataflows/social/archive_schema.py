"""TradingAgents social archive SQLite schema definitions and initialization (Task 2 / B2).

Specification:
- docs/social_data/implementation_plan.md §4.2, §3.4
- work/2026-08-27-unified-final-plan.md Phase 8 / B2
"""

from __future__ import annotations

import sqlite3
from typing import Dict, List, Set, Union


ARCHIVE_SCHEMA_VERSION = "1"

TABLES: List[str] = [
    "social_archive_meta",
    "social_ingest_runs",
    "social_record_snapshots",
    "social_entity_mentions",
]

INDEXES: List[str] = [
    "idx_social_snapshot_cutoff",
    "idx_social_record_history",
    "idx_social_entity_symbol",
    "idx_social_run_coverage",
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS social_archive_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_ingest_runs (
  run_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  platform TEXT NOT NULL,
  query_text TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL,
  crawler_commit TEXT NOT NULL,
  source_schema_fingerprint TEXT NOT NULL,
  source_max_first_seen_at TEXT,
  rows_read INTEGER NOT NULL DEFAULT 0,
  rows_inserted INTEGER NOT NULL DEFAULT 0,
  rows_rejected INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_detail TEXT
);

CREATE TABLE IF NOT EXISTS social_record_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  record_id TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  record_type TEXT NOT NULL CHECK(record_type IN ('post','comment')),
  platform TEXT NOT NULL CHECK(platform IN ('xhs','dy')),
  native_id TEXT NOT NULL,
  parent_record_id TEXT,
  root_post_record_id TEXT NOT NULL,
  published_at TEXT NOT NULL,
  source_updated_at TEXT,
  first_seen_at TEXT NOT NULL,
  snapshot_at TEXT NOT NULL,
  ingest_at TEXT NOT NULL,
  title TEXT,
  text TEXT NOT NULL DEFAULT '',
  canonical_url TEXT,
  author_id_hash TEXT,
  source_keyword TEXT,
  metrics_json TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  metrics_hash TEXT NOT NULL,
  ingest_run_id TEXT NOT NULL REFERENCES social_ingest_runs(run_id),
  source_table TEXT NOT NULL,
  source_row_id TEXT NOT NULL,
  UNIQUE(record_id, content_hash, metrics_hash)
);

CREATE TABLE IF NOT EXISTS social_entity_mentions (
  snapshot_id TEXT NOT NULL REFERENCES social_record_snapshots(snapshot_id),
  symbol TEXT NOT NULL,
  matched_text TEXT NOT NULL,
  match_method TEXT NOT NULL,
  confidence REAL NOT NULL,
  resolver_version TEXT NOT NULL,
  PRIMARY KEY(snapshot_id, symbol, resolver_version)
);

CREATE INDEX IF NOT EXISTS idx_social_snapshot_cutoff
  ON social_record_snapshots(platform, published_at, first_seen_at, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_social_record_history
  ON social_record_snapshots(record_id, snapshot_at);
CREATE INDEX IF NOT EXISTS idx_social_entity_symbol
  ON social_entity_mentions(symbol, snapshot_id);
CREATE INDEX IF NOT EXISTS idx_social_run_coverage
  ON social_ingest_runs(platform, query_text, completed_at, status);
"""


def init_archive_db(db: Union[sqlite3.Connection, str]) -> sqlite3.Connection:
    """Initialize an archive SQLite database with schema tables, indexes, and default metadata.

    Sets:
    - schema_version = '1'
    - xhs_last_update_time_trusted = 'false' (per §3.4 / §4.2 until verification conclusion)
    """
    if isinstance(db, str):
        conn = sqlite3.connect(db)
    else:
        conn = db

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)

    # Insert default metadata if not present
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO social_archive_meta (key, value) VALUES (?, ?)", ("schema_version", ARCHIVE_SCHEMA_VERSION))
    cursor.execute("INSERT OR IGNORE INTO social_archive_meta (key, value) VALUES (?, ?)", ("xhs_last_update_time_trusted", "false"))
    conn.commit()

    return conn


def verify_archive_schema(conn: sqlite3.Connection) -> bool:
    """Verify that all required tables and indexes exist in the archive database."""
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables: Set[str] = {row[0] for row in cursor.fetchall()}
    for tbl in TABLES:
        if tbl not in existing_tables:
            return False

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    existing_indexes: Set[str] = {row[0] for row in cursor.fetchall()}
    for idx in INDEXES:
        if idx not in existing_indexes:
            return False

    return True
