"""MediaCrawler raw data importer into TradingAgents append-only social archive (Task 3 / B3).

Specifications:
- docs/social_data/implementation_plan.md §3.2, §3.3, §4.1, §4.2, Task 3, D-008
- work/2026-08-27-unified-final-plan.md Phase 8 / B3

Time Semantics:
- published_at: Platform source creation timestamp (XHS note time / DY create_time).
- source_updated_at: Platform source update timestamp (XHS last_update_time; null for DY / comments).
- first_seen_at: MediaCrawler crawler row creation timestamp (add_ts).
- snapshot_at: MediaCrawler crawler row modification timestamp (last_modify_ts).
- ingest_at: TradingAgents archive ingestion clock (UTC). For audit only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

from tradingagents.dataflows.social.archive_schema import (
    ARCHIVE_SCHEMA_VERSION,
    init_archive_db,
    verify_archive_schema,
)
from tradingagents.dataflows.social.contracts import (
    SocialMetrics,
    SocialRawRecordV1,
    SourceRef,
    compute_content_hash,
    compute_metrics_hash,
)
from tradingagents.dataflows.social.entity_resolver import (
    EntityResolver,
)

# Required columns per table in MediaCrawler SQLite database
REQUIRED_SOURCE_COLUMNS: Dict[str, Set[str]] = {
    "xhs_note": {"note_id", "time", "add_ts", "last_modify_ts"},
    "xhs_note_comment": {"comment_id", "note_id", "create_time", "add_ts", "last_modify_ts"},
    "douyin_aweme": {"aweme_id", "create_time", "add_ts", "last_modify_ts"},
    "douyin_aweme_comment": {"comment_id", "aweme_id", "create_time", "add_ts", "last_modify_ts"},
}


# ============================================================================
# Helper Parsing Functions
# ============================================================================

def parse_crawler_timestamp(val: Any) -> Optional[str]:
    """Parse MediaCrawler integer/string timestamp into UTC ISO 8601 string (%Y-%m-%dT%H:%M:%SZ).

    Rules (§3.3):
    - abs(val) >= 10^12: treated as milliseconds.
    - abs(val) < 10^12: treated as seconds.
    - 0, negative, invalid, or year > 2100 are treated as missing (None).
    - ISO 8601 strings are normalized to UTC %Y-%m-%dT%H:%M:%SZ.
    """
    if val is None:
        return None

    if isinstance(val, (int, float)):
        num = float(val)
        if num <= 0:
            return None
        if abs(num) >= 10**12:
            num = num / 1000.0
        # Check reasonable range (year 1970 to 2100: 0 to 4102444800)
        if num < 0 or num >= 4102444800:
            return None
        try:
            return datetime.fromtimestamp(num, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(val, str):
        s = val.strip()
        if not s or s == "0":
            return None
        # Try numeric parse
        try:
            num = float(s)
            if num <= 0:
                return None
            if abs(num) >= 10**12:
                num = num / 1000.0
            if num < 0 or num >= 4102444800:
                return None
            return datetime.fromtimestamp(num, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

        # Try ISO format parse
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            pass

    return None


def parse_metric_number(val: Any) -> Optional[int]:
    """Parse interaction metric count into optional non-negative integer (§4.1).

    Rules:
    - Literal '0' or 0 -> 0.
    - Missing, empty, None, negative, or non-numeric -> None.
    """
    if val is None:
        return None

    if isinstance(val, int):
        return val if val >= 0 else None

    if isinstance(val, float):
        return int(val) if val >= 0 else None

    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            n = int(s)
            return n if n >= 0 else None
        except ValueError:
            try:
                n_f = float(s)
                return int(n_f) if n_f >= 0 else None
            except ValueError:
                return None

    return None


def clean_canonical_url(url: Optional[str]) -> Optional[str]:
    """Clean URL by removing tracking tokens and query parameters (§4.1)."""
    if not url:
        return None
    s = url.strip()
    if not s or not (s.startswith("http://") or s.startswith("https://")):
        return None

    try:
        parsed = urllib.parse.urlparse(s)
        # Rebuild without query string or fragment
        cleaned = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return cleaned
    except Exception:
        return None


def compute_author_id_hash(author_id: Optional[str]) -> Optional[str]:
    """Compute one-way SHA-256 hash of author_id (§4.1)."""
    if author_id is None:
        return None
    s = str(author_id).strip()
    if not s:
        return None
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_schema_fingerprint(conn: sqlite3.Connection, table_name: str) -> str:
    """Compute deterministic SHA-256 fingerprint of table schema."""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    cols = sorted([(row[1], row[2].upper()) for row in cursor.fetchall()])
    canonical_json = json.dumps(cols, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ============================================================================
# MediaCrawlerImporter Class
# ============================================================================

class MediaCrawlerImporter:
    """Imports raw records from MediaCrawler SQLite DB into TradingAgents archive DB."""

    def __init__(
        self,
        archive_db: Optional[Union[sqlite3.Connection, str]] = None,
        crawler_commit: Optional[str] = None,
        entity_resolver: Optional[EntityResolver] = None,
        archive_conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        target_db = archive_conn if archive_conn is not None else archive_db
        if target_db is None:
            raise ValueError("archive_db or archive_conn must be provided")

        if crawler_commit is None:
            raise ValueError("crawler_commit must be explicitly provided; default fabrication is forbidden")

        commit_str = str(crawler_commit).strip()
        if not commit_str:
            raise ValueError("crawler_commit cannot be empty; fabricating commit string is forbidden")
        self.crawler_commit = commit_str

        if isinstance(target_db, str):
            self.archive_conn = init_archive_db(target_db)
            self._owns_archive_conn = True
        else:
            self.archive_conn = target_db
            init_archive_db(self.archive_conn)
            self._owns_archive_conn = False

        self.entity_resolver = entity_resolver or EntityResolver()

    def import_records(
        self,
        source_db: Union[sqlite3.Connection, str],
        platform: Optional[Union[str, List[str]]] = None,
        platforms: Optional[List[str]] = None,
        query_text: Optional[str] = None,
        ingest_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convenience alias for import_from_db."""
        actual_platforms: Optional[List[str]] = platforms
        if actual_platforms is None and platform is not None:
            if isinstance(platform, str):
                actual_platforms = None if platform == "all" else [p.strip() for p in platform.split(",")]
            else:
                actual_platforms = list(platform)
        return self.import_from_db(
            source_db=source_db,
            platforms=actual_platforms,
            query_text=query_text,
            ingest_run_id=ingest_run_id,
        )

    def import_from_db(
        self,
        source_db: Union[sqlite3.Connection, str],
        platforms: Optional[List[str]] = None,
        query_text: Optional[str] = None,
        ingest_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Import all relevant tables from a source MediaCrawler SQLite database.

        Parameters:
        - source_db: SQLite connection or file path to MediaCrawler DB.
        - platforms: list of platforms to import ('xhs', 'dy', or both). Defaults to both.
        - query_text: optional query/keyword label for audit.
        - ingest_run_id: optional UUID string for the ingest run.

        Returns summary dictionary with keys:
        - run_id, status, rows_read, rows_inserted, rows_rejected, error_code, error_detail
        """
        if isinstance(source_db, str):
            source_conn = sqlite3.connect(source_db)
            owns_source_conn = True
        else:
            source_conn = source_db
            owns_source_conn = False

        run_id = ingest_run_id or str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ingest_at = started_at
        q_text = query_text or ""
        platform_str = ",".join(platforms) if platforms else "all"

        target_platforms = set(platforms) if platforms else {"xhs", "dy"}

        # Determine existing tables in source DB
        cursor = source_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        source_tables = {row[0] for row in cursor.fetchall()}

        # Verify required table schemas and compute fingerprint
        fingerprints: Dict[str, str] = {}
        for tbl in ["xhs_note", "xhs_note_comment", "douyin_aweme", "douyin_aweme_comment"]:
            if tbl in source_tables:
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols = {row[1] for row in cursor.fetchall()}
                required = REQUIRED_SOURCE_COLUMNS[tbl]
                if not required.issubset(cols):
                    # Schema mismatch: missing required columns
                    err_msg = f"Table {tbl} is missing required columns: {required - cols}"
                    self._record_failed_run(
                        run_id=run_id,
                        platform=platform_str,
                        query_text=q_text,
                        started_at=started_at,
                        fingerprint="invalid",
                        error_code="social_schema_mismatch",
                        error_detail=err_msg,
                    )
                    if owns_source_conn:
                        source_conn.close()
                    return {
                        "run_id": run_id,
                        "status": "failed",
                        "rows_read": 0,
                        "rows_inserted": 0,
                        "rows_rejected": 0,
                        "error_code": "social_schema_mismatch",
                        "error_detail": err_msg,
                    }
                fingerprints[tbl] = compute_schema_fingerprint(source_conn, tbl)

        combined_fingerprint = hashlib.sha256(
            json.dumps(fingerprints, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Insert initial ingest run record
        arch_cursor = self.archive_conn.cursor()
        arch_cursor.execute(
            """
            INSERT INTO social_ingest_runs (
                run_id, provider, platform, query_text, started_at, status,
                crawler_commit, source_schema_fingerprint, rows_read, rows_inserted, rows_rejected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, "mediacrawler", platform_str, q_text, started_at, "running",
                self.crawler_commit, combined_fingerprint, 0, 0, 0,
            ),
        )
        self.archive_conn.commit()

        total_read = 0
        total_inserted = 0
        total_rejected = 0
        max_first_seen: Optional[str] = None

        try:
            # 1. Import XHS Notes
            if "xhs" in target_platforms and "xhs_note" in source_tables:
                r_read, r_ins, r_rej, m_seen = self._import_xhs_notes(source_conn, run_id, ingest_at)
                total_read += r_read
                total_inserted += r_ins
                total_rejected += r_rej
                if m_seen and (max_first_seen is None or m_seen > max_first_seen):
                    max_first_seen = m_seen

            # 2. Import XHS Comments
            if "xhs" in target_platforms and "xhs_note_comment" in source_tables:
                r_read, r_ins, r_rej, m_seen = self._import_xhs_comments(source_conn, run_id, ingest_at)
                total_read += r_read
                total_inserted += r_ins
                total_rejected += r_rej
                if m_seen and (max_first_seen is None or m_seen > max_first_seen):
                    max_first_seen = m_seen

            # 3. Import Douyin Awemes
            if "dy" in target_platforms and "douyin_aweme" in source_tables:
                r_read, r_ins, r_rej, m_seen = self._import_douyin_awemes(source_conn, run_id, ingest_at)
                total_read += r_read
                total_inserted += r_ins
                total_rejected += r_rej
                if m_seen and (max_first_seen is None or m_seen > max_first_seen):
                    max_first_seen = m_seen

            # 4. Import Douyin Comments
            if "dy" in target_platforms and "douyin_aweme_comment" in source_tables:
                r_read, r_ins, r_rej, m_seen = self._import_douyin_comments(source_conn, run_id, ingest_at)
                total_read += r_read
                total_inserted += r_ins
                total_rejected += r_rej
                if m_seen and (max_first_seen is None or m_seen > max_first_seen):
                    max_first_seen = m_seen

            # Update ingest run status to completed
            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            arch_cursor.execute(
                """
                UPDATE social_ingest_runs
                SET completed_at = ?, status = 'completed', source_max_first_seen_at = ?,
                    rows_read = ?, rows_inserted = ?, rows_rejected = ?
                WHERE run_id = ?
                """,
                (completed_at, max_first_seen, total_read, total_inserted, total_rejected, run_id),
            )
            self.archive_conn.commit()

            return {
                "run_id": run_id,
                "status": "completed",
                "rows_read": total_read,
                "rows_inserted": total_inserted,
                "rows_rejected": total_rejected,
                "error_code": None,
                "error_detail": None,
            }

        except Exception as e:
            self.archive_conn.rollback()
            completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            arch_cursor.execute(
                """
                UPDATE social_ingest_runs
                SET completed_at = ?, status = 'failed', error_code = 'social_import_error',
                    error_detail = ?, rows_read = ?, rows_inserted = ?, rows_rejected = ?
                WHERE run_id = ?
                """,
                (completed_at, str(e), total_read, total_inserted, total_rejected, run_id),
            )
            self.archive_conn.commit()
            raise e
        finally:
            if owns_source_conn:
                source_conn.close()

    def _record_failed_run(
        self,
        run_id: str,
        platform: str,
        query_text: str,
        started_at: str,
        fingerprint: str,
        error_code: str,
        error_detail: str,
    ) -> None:
        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        arch_cursor = self.archive_conn.cursor()
        arch_cursor.execute(
            """
            INSERT INTO social_ingest_runs (
                run_id, provider, platform, query_text, started_at, completed_at, status,
                crawler_commit, source_schema_fingerprint, rows_read, rows_inserted,
                rows_rejected, error_code, error_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, "mediacrawler", platform, query_text, started_at, completed_at, "failed",
                self.crawler_commit, fingerprint, 0, 0, 0, error_code, error_detail,
            ),
        )
        self.archive_conn.commit()

    def _insert_snapshot_row(
        self,
        record_id: str,
        record_type: str,
        platform: str,
        native_id: str,
        parent_record_id: Optional[str],
        root_post_record_id: str,
        published_at: str,
        source_updated_at: Optional[str],
        first_seen_at: str,
        snapshot_at: str,
        ingest_at: str,
        title: Optional[str],
        text: str,
        canonical_url: Optional[str],
        author_id_hash: Optional[str],
        source_keyword: Optional[str],
        metrics: SocialMetrics,
        ingest_run_id: str,
        source_table: str,
        source_row_id: str,
    ) -> bool:
        """Insert a snapshot row into social_record_snapshots if hash tuple is fresh.

        Returns True if a new row was inserted, False if already exists (idempotent).
        """
        c_hash = compute_content_hash(title, text)
        m_hash = compute_metrics_hash(metrics)

        arch_cursor = self.archive_conn.cursor()

        # Check if identical snapshot exists
        arch_cursor.execute(
            "SELECT 1 FROM social_record_snapshots WHERE record_id = ? AND content_hash = ? AND metrics_hash = ?",
            (record_id, c_hash, m_hash),
        )
        if arch_cursor.fetchone() is not None:
            # Already exists: append-only rule forbids UPDATE, do not insert duplicate
            return False

        # Generate deterministic unique snapshot_id
        snap_id_payload = f"{record_id}:{c_hash}:{m_hash}:{snapshot_at}".encode("utf-8")
        snapshot_id = f"sha256:{hashlib.sha256(snap_id_payload).hexdigest()}"
        metrics_json = json.dumps(metrics.to_dict(), sort_keys=True, separators=(",", ":"))

        arch_cursor.execute(
            """
            INSERT INTO social_record_snapshots (
                snapshot_id, record_id, schema_version, record_type, platform,
                native_id, parent_record_id, root_post_record_id,
                published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at,
                title, text, canonical_url, author_id_hash, source_keyword,
                metrics_json, content_hash, metrics_hash, ingest_run_id,
                source_table, source_row_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, record_id, "social.raw_record.v1", record_type, platform,
                native_id, parent_record_id, root_post_record_id,
                published_at, source_updated_at, first_seen_at, snapshot_at, ingest_at,
                title, text, canonical_url, author_id_hash, source_keyword,
                metrics_json, c_hash, m_hash, ingest_run_id,
                source_table, source_row_id,
            ),
        )

        # Resolve entity mentions and insert into social_entity_mentions
        if self.entity_resolver:
            mentions = self.entity_resolver.resolve(
                text=text,
                title=title,
                source_keyword=source_keyword,
            )
            for mention in mentions:
                arch_cursor.execute(
                    """
                    INSERT OR IGNORE INTO social_entity_mentions (
                        snapshot_id, symbol, matched_text, match_method, confidence, resolver_version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        mention.symbol,
                        mention.matched_text,
                        mention.match_method,
                        mention.confidence,
                        mention.resolver_version,
                    ),
                )

        return True

    def _import_xhs_notes(
        self, source_conn: sqlite3.Connection, run_id: str, ingest_at: str
    ) -> Tuple[int, int, int, Optional[str]]:
        cursor = source_conn.cursor()
        cursor.row_factory = sqlite3.Row
        cursor.execute("SELECT * FROM xhs_note")
        rows = cursor.fetchall()

        rows_read = len(rows)
        rows_inserted = 0
        rows_rejected = 0
        max_first_seen: Optional[str] = None

        for row in rows:
            row_dict = dict(row)
            note_id = str(row_dict.get("note_id") or "").strip()
            if not note_id:
                rows_rejected += 1
                continue

            published_at = parse_crawler_timestamp(row_dict.get("time"))
            first_seen_at = parse_crawler_timestamp(row_dict.get("add_ts"))
            snapshot_at = parse_crawler_timestamp(row_dict.get("last_modify_ts"))

            # §3.3: Missing published_at / first_seen_at / snapshot_at -> reject
            if not published_at or not first_seen_at or not snapshot_at:
                rows_rejected += 1
                continue

            source_updated_at = parse_crawler_timestamp(row_dict.get("last_update_time"))
            if first_seen_at and (max_first_seen is None or first_seen_at > max_first_seen):
                max_first_seen = first_seen_at

            title = row_dict.get("title")
            text = row_dict.get("desc") or ""
            canonical_url = clean_canonical_url(row_dict.get("note_url"))
            author_id_hash = compute_author_id_hash(row_dict.get("user_id"))
            source_keyword = row_dict.get("source_keyword") or row_dict.get("keyword")

            metrics = SocialMetrics(
                likes=parse_metric_number(row_dict.get("liked_count")),
                comments=parse_metric_number(row_dict.get("comment_count")),
                shares=parse_metric_number(row_dict.get("share_count")),
                collects=parse_metric_number(row_dict.get("collected_count")),
                views=None,
            )

            record_id = f"xhs:post:{note_id}"
            inserted = self._insert_snapshot_row(
                record_id=record_id,
                record_type="post",
                platform="xhs",
                native_id=note_id,
                parent_record_id=None,
                root_post_record_id=record_id,
                published_at=published_at,
                source_updated_at=source_updated_at,
                first_seen_at=first_seen_at,
                snapshot_at=snapshot_at,
                ingest_at=ingest_at,
                title=title,
                text=text,
                canonical_url=canonical_url,
                author_id_hash=author_id_hash,
                source_keyword=source_keyword,
                metrics=metrics,
                ingest_run_id=run_id,
                source_table="xhs_note",
                source_row_id=str(row_dict.get("id") or note_id),
            )
            if inserted:
                rows_inserted += 1

        self.archive_conn.commit()
        return rows_read, rows_inserted, rows_rejected, max_first_seen

    def _import_xhs_comments(
        self, source_conn: sqlite3.Connection, run_id: str, ingest_at: str
    ) -> Tuple[int, int, int, Optional[str]]:
        cursor = source_conn.cursor()
        cursor.row_factory = sqlite3.Row
        cursor.execute("SELECT * FROM xhs_note_comment")
        rows = cursor.fetchall()

        rows_read = len(rows)
        rows_inserted = 0
        rows_rejected = 0
        max_first_seen: Optional[str] = None

        for row in rows:
            row_dict = dict(row)
            comment_id = str(row_dict.get("comment_id") or "").strip()
            note_id = str(row_dict.get("note_id") or "").strip()
            if not comment_id or not note_id:
                rows_rejected += 1
                continue

            published_at = parse_crawler_timestamp(row_dict.get("create_time"))
            first_seen_at = parse_crawler_timestamp(row_dict.get("add_ts"))
            snapshot_at = parse_crawler_timestamp(row_dict.get("last_modify_ts"))

            if not published_at or not first_seen_at or not snapshot_at:
                rows_rejected += 1
                continue

            if first_seen_at and (max_first_seen is None or first_seen_at > max_first_seen):
                max_first_seen = first_seen_at

            text = row_dict.get("content") or ""
            author_id_hash = compute_author_id_hash(row_dict.get("user_id"))

            parent_cid = row_dict.get("parent_comment_id")
            parent_record_id = f"xhs:comment:{parent_cid}" if parent_cid and str(parent_cid).strip() and str(parent_cid).strip() != "0" else None

            metrics = SocialMetrics(
                likes=parse_metric_number(row_dict.get("like_count")),
                comments=parse_metric_number(row_dict.get("sub_comment_count")),
                shares=None,
                collects=None,
                views=None,
            )

            record_id = f"xhs:comment:{comment_id}"
            inserted = self._insert_snapshot_row(
                record_id=record_id,
                record_type="comment",
                platform="xhs",
                native_id=comment_id,
                parent_record_id=parent_record_id,
                root_post_record_id=f"xhs:post:{note_id}",
                published_at=published_at,
                source_updated_at=None,
                first_seen_at=first_seen_at,
                snapshot_at=snapshot_at,
                ingest_at=ingest_at,
                title=None,
                text=text,
                canonical_url=None,
                author_id_hash=author_id_hash,
                source_keyword=None,
                metrics=metrics,
                ingest_run_id=run_id,
                source_table="xhs_note_comment",
                source_row_id=str(row_dict.get("id") or comment_id),
            )
            if inserted:
                rows_inserted += 1

        self.archive_conn.commit()
        return rows_read, rows_inserted, rows_rejected, max_first_seen

    def _import_douyin_awemes(
        self, source_conn: sqlite3.Connection, run_id: str, ingest_at: str
    ) -> Tuple[int, int, int, Optional[str]]:
        cursor = source_conn.cursor()
        cursor.row_factory = sqlite3.Row
        cursor.execute("SELECT * FROM douyin_aweme")
        rows = cursor.fetchall()

        rows_read = len(rows)
        rows_inserted = 0
        rows_rejected = 0
        max_first_seen: Optional[str] = None

        for row in rows:
            row_dict = dict(row)
            aweme_id = str(row_dict.get("aweme_id") or "").strip()
            if not aweme_id:
                rows_rejected += 1
                continue

            published_at = parse_crawler_timestamp(row_dict.get("create_time"))
            first_seen_at = parse_crawler_timestamp(row_dict.get("add_ts"))
            snapshot_at = parse_crawler_timestamp(row_dict.get("last_modify_ts"))

            if not published_at or not first_seen_at or not snapshot_at:
                rows_rejected += 1
                continue

            if first_seen_at and (max_first_seen is None or first_seen_at > max_first_seen):
                max_first_seen = first_seen_at

            title = row_dict.get("title")
            text = row_dict.get("desc") or ""
            canonical_url = clean_canonical_url(row_dict.get("aweme_url"))
            author_id_hash = compute_author_id_hash(row_dict.get("sec_uid") or row_dict.get("user_id"))
            source_keyword = row_dict.get("source_keyword") or row_dict.get("keyword")

            metrics = SocialMetrics(
                likes=parse_metric_number(row_dict.get("liked_count")),
                comments=parse_metric_number(row_dict.get("comment_count")),
                shares=parse_metric_number(row_dict.get("share_count")),
                collects=parse_metric_number(row_dict.get("collected_count")),
                views=None,
            )

            record_id = f"dy:post:{aweme_id}"
            inserted = self._insert_snapshot_row(
                record_id=record_id,
                record_type="post",
                platform="dy",
                native_id=aweme_id,
                parent_record_id=None,
                root_post_record_id=record_id,
                published_at=published_at,
                source_updated_at=None,
                first_seen_at=first_seen_at,
                snapshot_at=snapshot_at,
                ingest_at=ingest_at,
                title=title,
                text=text,
                canonical_url=canonical_url,
                author_id_hash=author_id_hash,
                source_keyword=source_keyword,
                metrics=metrics,
                ingest_run_id=run_id,
                source_table="douyin_aweme",
                source_row_id=str(row_dict.get("id") or aweme_id),
            )
            if inserted:
                rows_inserted += 1

        self.archive_conn.commit()
        return rows_read, rows_inserted, rows_rejected, max_first_seen

    def _import_douyin_comments(
        self, source_conn: sqlite3.Connection, run_id: str, ingest_at: str
    ) -> Tuple[int, int, int, Optional[str]]:
        cursor = source_conn.cursor()
        cursor.row_factory = sqlite3.Row
        cursor.execute("SELECT * FROM douyin_aweme_comment")
        rows = cursor.fetchall()

        rows_read = len(rows)
        rows_inserted = 0
        rows_rejected = 0
        max_first_seen: Optional[str] = None

        for row in rows:
            row_dict = dict(row)
            comment_id = str(row_dict.get("comment_id") or "").strip()
            aweme_id = str(row_dict.get("aweme_id") or "").strip()
            if not comment_id or not aweme_id:
                rows_rejected += 1
                continue

            published_at = parse_crawler_timestamp(row_dict.get("create_time"))
            first_seen_at = parse_crawler_timestamp(row_dict.get("add_ts"))
            snapshot_at = parse_crawler_timestamp(row_dict.get("last_modify_ts"))

            if not published_at or not first_seen_at or not snapshot_at:
                rows_rejected += 1
                continue

            if first_seen_at and (max_first_seen is None or first_seen_at > max_first_seen):
                max_first_seen = first_seen_at

            text = row_dict.get("content") or ""
            author_id_hash = compute_author_id_hash(row_dict.get("sec_uid") or row_dict.get("user_id"))

            parent_cid = row_dict.get("parent_comment_id")
            parent_record_id = f"dy:comment:{parent_cid}" if parent_cid and str(parent_cid).strip() and str(parent_cid).strip() != "0" else None

            sub_comments = parse_metric_number(row_dict.get("reply_comment_total") or row_dict.get("sub_comment_count"))
            metrics = SocialMetrics(
                likes=parse_metric_number(row_dict.get("like_count")),
                comments=sub_comments,
                shares=None,
                collects=None,
                views=None,
            )

            record_id = f"dy:comment:{comment_id}"
            inserted = self._insert_snapshot_row(
                record_id=record_id,
                record_type="comment",
                platform="dy",
                native_id=comment_id,
                parent_record_id=parent_record_id,
                root_post_record_id=f"dy:post:{aweme_id}",
                published_at=published_at,
                source_updated_at=None,
                first_seen_at=first_seen_at,
                snapshot_at=snapshot_at,
                ingest_at=ingest_at,
                title=None,
                text=text,
                canonical_url=None,
                author_id_hash=author_id_hash,
                source_keyword=None,
                metrics=metrics,
                ingest_run_id=run_id,
                source_table="douyin_aweme_comment",
                source_row_id=str(row_dict.get("id") or comment_id),
            )
            if inserted:
                rows_inserted += 1

        self.archive_conn.commit()
        return rows_read, rows_inserted, rows_rejected, max_first_seen
