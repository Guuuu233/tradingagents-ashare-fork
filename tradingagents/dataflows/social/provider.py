"""TradingAgents social data provider and as-of eligibility guards (Task 5 / B5 / D-008).

Specifications:
- docs/social_data/implementation_plan.md Task 5, §3.4, §4.2, §5.1, §5.2, §7, D-008
- work/2026-08-27-audit-decision-semantics-plan.md §8 P2
- DECISIONS.md D-008, D-009, D-010

Core Contracts:
- Protocol `SocialDataProvider`: ONLY `name` and `fetch_records(...)`.
- Does NOT inherit from `BaseMarketDataProvider`.
- SQLite archive opened strictly read-only: `file:<path>?mode=ro`, `PRAGMA query_only=ON`, `PRAGMA busy_timeout`.
- D-008 Eligibility:
  * Strict timezone-aware comparisons (no string comparisons for dates/times).
  * Historical day cutoff: 23:59:59.999999 in Asia/Shanghai (serialized to equivalent UTC).
  * Current day cutoff: actual now_cn().
  * Candidate snapshot per record_id: MAX(snapshot_at) WHERE snapshot_at <= cutoff.
  * Content eligibility: window_start <= published_at <= cutoff AND first_seen_at <= cutoff.
  * Metrics eligibility: snapshot_at <= cutoff (satisfied by candidate snapshot).
  * Ingest time (ingest_at) NEVER participates in eligibility.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from tradingagents.dataflows.trade_calendar import CN_TZ, now_cn
from tradingagents.dataflows.social.archive_schema import verify_archive_schema
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
    REASON_SOCIAL_ARCHIVE_CORRUPT,
    REASON_SOCIAL_ARCHIVE_LOCKED,
    REASON_SOCIAL_ARCHIVE_MISSING,
    REASON_SOCIAL_EMPTY,
    REASON_SOCIAL_FUTURE_AS_OF,
    REASON_SOCIAL_INVALID_AS_OF,
    REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT,
    REASON_SOCIAL_SCHEMA_MISMATCH,
    EntityMention,
    SocialMetrics,
    SocialRawRecordV1,
    SocialStatus,
    SourceRef,
)
from tradingagents.dataflows.social.entity_resolver import normalize_stock_code


# ============================================================================
# Fetch Result Dataclass
# ============================================================================

@dataclass
class SocialFetchResult:
    """Result object returned by SocialDataProvider.fetch_records.

    Behaves as both a typed metadata result and an iterable of records.
    """

    status: str
    requested_as_of: str
    cutoff_at: Optional[str] = None
    window_start: Optional[str] = None
    records: List[SocialRawRecordV1] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.records)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int) -> SocialRawRecordV1:
        return self.records[index]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "requested_as_of": self.requested_as_of,
            "cutoff_at": self.cutoff_at,
            "window_start": self.window_start,
            "records": [r.to_dict() for r in self.records],
            "reason_codes": list(self.reason_codes),
            "meta": dict(self.meta),
        }


# ============================================================================
# Time and Cutoff Helper Functions (D-008)
# ============================================================================

def parse_iso_datetime(val: Any) -> Optional[datetime]:
    """Parse string or timestamp into a timezone-aware UTC datetime.

    Returns None if val is None, empty, or unparseable.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, (int, float)):
        num = float(val)
        if abs(num) >= 1e11:  # milliseconds
            num = num / 1000.0
        return datetime.fromtimestamp(num, tz=timezone.utc)

    s = str(val).strip()
    if not s:
        return None

    try:
        s_clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(tzinfo=CN_TZ)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    return None


def compute_as_of_cutoff(
    as_of: str,
    lookback_days: int = 7,
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime, datetime]:
    """Compute (window_start_utc, cutoff_utc, cutoff_shanghai) for a given as_of string.

    Rules (D-008):
    - All date/time comparisons MUST use timezone-aware datetime.
    - Current now in Asia/Shanghai: now_cn() or passed `now`.
    - If as_of is empty or cannot be parsed -> raises ValueError(REASON_SOCIAL_INVALID_AS_OF).
    - If as_of is a future date (> today in Asia/Shanghai) -> raises ValueError(REASON_SOCIAL_FUTURE_AS_OF).
    - If as_of is a historical date (< today in Asia/Shanghai):
      Cutoff is 23:59:59.999999 in Asia/Shanghai (serialized to equivalent UTC).
    - If as_of is today (== today in Asia/Shanghai):
      Cutoff is actual now_cn() (cannot be preset to end of day).
    - Window start is 00:00:00.000000 in Asia/Shanghai of (as_of_date - lookback_days).
    """
    if as_of is None or not str(as_of).strip():
        raise ValueError(REASON_SOCIAL_INVALID_AS_OF)

    s = str(as_of).strip()

    # Determine current time in Shanghai
    if now is not None:
        if now.tzinfo is None:
            now_shanghai = now.replace(tzinfo=CN_TZ)
        else:
            now_shanghai = now.astimezone(CN_TZ)
    else:
        now_shanghai = now_cn()

    today_shanghai = now_shanghai.date()

    parsed_date: Optional[date] = None
    parsed_time: Optional[dt_time] = None

    # Check YYYY-MM-DD pattern
    m_date = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", s)
    if m_date:
        try:
            parsed_date = date(int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3)))
        except ValueError:
            raise ValueError(REASON_SOCIAL_INVALID_AS_OF)
    else:
        # Full ISO string
        dt_parsed = parse_iso_datetime(s)
        if dt_parsed is None:
            raise ValueError(REASON_SOCIAL_INVALID_AS_OF)
        dt_cn = dt_parsed.astimezone(CN_TZ)
        parsed_date = dt_cn.date()
        parsed_time = dt_cn.time()

    # Future date check
    if parsed_date > today_shanghai:
        raise ValueError(REASON_SOCIAL_FUTURE_AS_OF)

    # Compute cutoff
    if parsed_date < today_shanghai:
        # Historical day: 23:59:59.999999 Shanghai time
        cutoff_cn = datetime(
            parsed_date.year,
            parsed_date.month,
            parsed_date.day,
            23,
            59,
            59,
            999999,
            tzinfo=CN_TZ,
        )
    else:
        # Today: actual now_cn()
        if parsed_time is not None:
            explicit_dt = datetime.combine(parsed_date, parsed_time, tzinfo=CN_TZ)
            cutoff_cn = min(explicit_dt, now_shanghai)
        else:
            cutoff_cn = now_shanghai

    cutoff_utc = cutoff_cn.astimezone(timezone.utc)

    # Compute window_start: lookback_days days before as_of_date 00:00:00 CST
    start_date = parsed_date - timedelta(days=lookback_days)
    window_start_cn = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        0,
        0,
        0,
        0,
        tzinfo=CN_TZ,
    )
    window_start_utc = window_start_cn.astimezone(timezone.utc)

    return window_start_utc, cutoff_utc, cutoff_cn


def select_candidate_snapshot(
    snapshots: List[Dict[str, Any]],
    cutoff_utc: datetime,
) -> Optional[Dict[str, Any]]:
    """Select candidate snapshot for a record_id from a list of snapshot rows/dicts.

    Rule (D-008 / M3):
    - Select snapshots where snapshot_at <= cutoff.
    - Pick the snapshot with MAXIMUM snapshot_at.
    - Tie-breaker: latest ingest_at (parsed as UTC datetime) then snapshot_id.
    - Reject snapshots with missing or invalid ingest_at (forbidden to backfill 'now').
    - Returns None if no snapshot has snapshot_at <= cutoff.
    """
    valid_snapshots = []
    for s in snapshots:
        s_at = parse_iso_datetime(s.get("snapshot_at"))
        if s_at is None or s_at > cutoff_utc:
            continue

        raw_ingest_at = s.get("ingest_at")
        ingest_dt = parse_iso_datetime(raw_ingest_at)
        if ingest_dt is None:
            logger.warning(
                "Snapshot %s has missing or invalid ingest_at (%r); rejecting snapshot",
                s.get("snapshot_id"),
                raw_ingest_at,
            )
            continue

        valid_snapshots.append((s_at, ingest_dt, str(s.get("snapshot_id") or ""), s))

    if not valid_snapshots:
        return None

    # Sort by snapshot_at ascending, then ingest_at ascending, snapshot_id ascending
    valid_snapshots.sort(key=lambda x: (x[0], x[1], x[2]))
    return valid_snapshots[-1][3]


def check_content_eligibility(
    snapshot: Union[Dict[str, Any], SocialRawRecordV1],
    window_start_utc: datetime,
    cutoff_utc: datetime,
    xhs_last_update_time_trusted: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Check content eligibility for a candidate snapshot (D-008).

    Rules:
    - window_start <= published_at <= cutoff
    - first_seen_at <= cutoff
    - If xhs_last_update_time_trusted is True and platform is xhs:
      source_updated_at is None or <= cutoff
    - ingest_at is NEVER checked.

    Returns (is_eligible, failure_reason).
    """
    if isinstance(snapshot, SocialRawRecordV1):
        published_at = snapshot.published_at
        first_seen_at = snapshot.first_seen_at
        source_updated_at = snapshot.source_updated_at
        platform = snapshot.platform
    else:
        published_at = snapshot.get("published_at")
        first_seen_at = snapshot.get("first_seen_at")
        source_updated_at = snapshot.get("source_updated_at")
        platform = snapshot.get("platform")

    p_dt = parse_iso_datetime(published_at)
    if p_dt is None:
        return False, "missing_or_invalid_published_at"
    if not (window_start_utc <= p_dt <= cutoff_utc):
        return False, "published_at_outside_window"

    f_dt = parse_iso_datetime(first_seen_at)
    if f_dt is None:
        return False, "missing_or_invalid_first_seen_at"
    if f_dt > cutoff_utc:
        return False, "first_seen_at_after_cutoff"

    if xhs_last_update_time_trusted and platform == "xhs" and source_updated_at:
        u_dt = parse_iso_datetime(source_updated_at)
        if u_dt is not None and u_dt > cutoff_utc:
            return False, "source_updated_at_after_cutoff"

    return True, None


# ============================================================================
# Protocol Definition
# ============================================================================

@runtime_checkable
class SocialDataProvider(Protocol):
    """Protocol for social data providers.

    Contains only `name` property/attribute and `fetch_records(...)` method.
    Must NOT inherit from BaseMarketDataProvider.
    """

    name: str

    def fetch_records(
        self,
        symbol: str,
        as_of: str,
        lookback_days: int = 7,
        platforms: Optional[Sequence[str]] = None,
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> SocialFetchResult:
        ...


# ============================================================================
# Social Archive SQLite Provider Implementation
# ============================================================================

class SocialArchiveProvider:
    """Read-only SQLite Social Archive Data Provider (D-008 / Task 5)."""

    name: str = "archive_sqlite"

    def __init__(
        self,
        db_path: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ):
        self._db_path = db_path
        if timeout_ms is not None:
            self._timeout_ms = int(timeout_ms)
        else:
            env_ms = os.environ.get("TA_SOCIAL_FETCH_TIMEOUT_MS")
            if env_ms:
                try:
                    self._timeout_ms = int(env_ms)
                except ValueError:
                    self._timeout_ms = 5000
            else:
                env_sec = os.environ.get("TA_SOCIAL_FETCH_TIMEOUT")
                if env_sec:
                    try:
                        self._timeout_ms = int(float(env_sec) * 1000)
                    except ValueError:
                        self._timeout_ms = 5000
                else:
                    self._timeout_ms = 5000

    @property
    def db_path(self) -> Optional[str]:
        return self._db_path or os.environ.get("TA_SOCIAL_ARCHIVE_DB")

    def _get_readonly_connection(self, path: str) -> sqlite3.Connection:
        """Open SQLite connection strictly read-only with query_only and busy_timeout."""
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Social archive DB not found: {abs_path}")

        if os.path.getsize(abs_path) == 0:
            raise sqlite3.DatabaseError(f"Social archive DB is empty: {abs_path}")

        timeout_sec = max(1.0, self._timeout_ms / 1000.0)
        conn = sqlite3.connect(f"file:{abs_path}?mode=ro", uri=True, timeout=timeout_sec)
        conn.execute("PRAGMA query_only = ON;")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms};")
        conn.row_factory = sqlite3.Row
        return conn

    def _resolve_cutoff(
        self,
        as_of: str,
        lookback_days: int,
        now: Optional[datetime],
    ) -> Union[Tuple[datetime, datetime, str, str], SocialFetchResult]:
        """Validate as_of and compute window_start and cutoff timestamps."""
        try:
            window_start_utc, cutoff_utc, _ = compute_as_of_cutoff(
                as_of=as_of,
                lookback_days=lookback_days,
                now=now,
            )
        except ValueError as exc:
            reason = str(exc)
            if reason in (REASON_SOCIAL_INVALID_AS_OF, REASON_SOCIAL_FUTURE_AS_OF):
                return SocialFetchResult(
                    status=SocialStatus.REFUSED.value,
                    requested_as_of=str(as_of or ""),
                    reason_codes=[reason],
                )
            return SocialFetchResult(
                status=SocialStatus.REFUSED.value,
                requested_as_of=str(as_of or ""),
                reason_codes=[REASON_SOCIAL_INVALID_AS_OF],
            )

        cutoff_iso = (
            cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            if cutoff_utc.microsecond == 0
            else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        )
        window_start_iso = window_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        return window_start_utc, cutoff_utc, window_start_iso, cutoff_iso

    def _open_archive_connection(
        self,
        db_file: str,
        as_of: str,
        cutoff_iso: str,
        window_start_iso: str,
    ) -> Union[sqlite3.Connection, SocialFetchResult]:
        """Open read-only connection to archive DB with error mapping."""
        try:
            return self._get_readonly_connection(db_file)
        except (FileNotFoundError, sqlite3.OperationalError) as exc:
            err_msg = str(exc).lower()
            if "unable to open" in err_msg or "not found" in err_msg or "no such file" in err_msg:
                return SocialFetchResult(
                    status=SocialStatus.FAILED.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
                )
            if "locked" in err_msg or "busy" in err_msg:
                return SocialFetchResult(
                    status=SocialStatus.TIMEOUT.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    reason_codes=[REASON_SOCIAL_ARCHIVE_LOCKED],
                )
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
            )
        except sqlite3.DatabaseError:
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
            )

    def _read_archive_meta(self, conn: sqlite3.Connection) -> Dict[str, str]:
        """Read key-value pairs from social_archive_meta table."""
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM social_archive_meta")
        return {row["key"]: row["value"] for row in cursor.fetchall()}

    def _query_candidate_snapshots(
        self,
        conn: sqlite3.Connection,
        symbol: str,
    ) -> List[Dict[str, Any]]:
        """Query raw snapshot rows matching target symbol mentions or parent post mentions."""
        target_symbols = self._resolve_target_symbols(symbol)
        placeholders = ",".join("?" for _ in target_symbols)
        sym_list = list(target_symbols)

        query = f"""
            SELECT DISTINCT s.*
            FROM social_record_snapshots s
            WHERE s.snapshot_id IN (
                SELECT snapshot_id FROM social_entity_mentions WHERE symbol IN ({placeholders})
            )
            OR s.root_post_record_id IN (
                SELECT s2.record_id
                FROM social_record_snapshots s2
                JOIN social_entity_mentions m ON s2.snapshot_id = m.snapshot_id
                WHERE m.symbol IN ({placeholders})
            )
        """
        cursor = conn.cursor()
        cursor.execute(query, sym_list + sym_list)
        return [dict(r) for r in cursor.fetchall()]

    def _load_crawler_commits(
        self,
        conn: sqlite3.Connection,
        run_ids: Set[str],
    ) -> Dict[str, str]:
        """Load crawler_commit for ingest run IDs."""
        if not run_ids:
            return {}
        run_ph = ",".join("?" for _ in run_ids)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT run_id, crawler_commit FROM social_ingest_runs WHERE run_id IN ({run_ph})",
            list(run_ids),
        )
        return {row["run_id"]: row["crawler_commit"] for row in cursor.fetchall()}

    def _load_snapshot_mentions(
        self,
        conn: sqlite3.Connection,
        snap_ids: Set[str],
    ) -> Dict[str, List[EntityMention]]:
        """Load entity mentions grouped by snapshot_id."""
        if not snap_ids:
            return {}
        snap_ph = ",".join("?" for _ in snap_ids)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT * FROM social_entity_mentions WHERE snapshot_id IN ({snap_ph})",
            list(snap_ids),
        )
        mentions_by_snap: Dict[str, List[EntityMention]] = {}
        for m_row in cursor.fetchall():
            m_dict = dict(m_row)
            mentions_by_snap.setdefault(m_dict["snapshot_id"], []).append(
                EntityMention.from_dict(m_dict)
            )
        return mentions_by_snap

    def _build_raw_record(
        self,
        cand: Dict[str, Any],
        crawler_commits: Dict[str, str],
        mentions_by_snap: Dict[str, List[EntityMention]],
    ) -> Optional[SocialRawRecordV1]:
        """Construct and validate a SocialRawRecordV1 from candidate snapshot row."""
        crawler_commit = crawler_commits.get(cand["ingest_run_id"])
        if not crawler_commit or not str(crawler_commit).strip():
            logger.warning(
                "Snapshot %s references missing or invalid ingest_run_id %s; rejecting record",
                cand.get("snapshot_id"),
                cand.get("ingest_run_id"),
            )
            return None

        crawler_commit = str(crawler_commit).strip()

        # Safely parse metrics_json (R1: row-level rejection on corruption)
        try:
            raw_metrics_json = cand.get("metrics_json")
            if raw_metrics_json is None or not str(raw_metrics_json).strip():
                metrics = SocialMetrics()
            else:
                metrics_data = json.loads(raw_metrics_json)
                if not isinstance(metrics_data, dict):
                    logger.warning(
                        "Snapshot %s has non-dict metrics_json %r; rejecting snapshot",
                        cand.get("snapshot_id"),
                        raw_metrics_json,
                    )
                    return None
                metrics = SocialMetrics.from_dict(metrics_data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning(
                "Snapshot %s has corrupt metrics_json %r: %s; rejecting snapshot",
                cand.get("snapshot_id"),
                cand.get("metrics_json"),
                exc,
            )
            return None

        source_ref = SourceRef(
            provider="mediacrawler",
            crawler_commit=crawler_commit,
            source_table=cand["source_table"],
            source_row_id=cand["source_row_id"],
        )
        entities = mentions_by_snap.get(cand["snapshot_id"], [])

        try:
            raw_record = SocialRawRecordV1(
                schema_version=cand.get("schema_version", "social.raw_record.v1"),
                record_id=cand["record_id"],
                snapshot_id=cand["snapshot_id"],
                record_type=cand["record_type"],
                platform=cand["platform"],
                native_id=cand["native_id"],
                parent_record_id=cand.get("parent_record_id"),
                root_post_record_id=cand["root_post_record_id"],
                published_at=cand["published_at"],
                source_updated_at=cand.get("source_updated_at"),
                first_seen_at=cand["first_seen_at"],
                snapshot_at=cand["snapshot_at"],
                ingest_at=cand["ingest_at"],
                title=cand.get("title"),
                text=cand.get("text", ""),
                canonical_url=cand.get("canonical_url"),
                author_id_hash=cand.get("author_id_hash"),
                source_keyword=cand.get("source_keyword"),
                entities=entities,
                metrics=metrics,
                content_hash=cand["content_hash"],
                metrics_hash=cand["metrics_hash"],
                ingest_run_id=cand["ingest_run_id"],
                source_ref=source_ref,
            )
            raw_record.validate()
            return raw_record
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning(
                "Snapshot %s record validation failed: %s; rejecting snapshot",
                cand.get("snapshot_id"),
                exc,
            )
            return None

    def _filter_and_assemble_records(
        self,
        dict_rows: List[Dict[str, Any]],
        window_start_utc: datetime,
        cutoff_utc: datetime,
        xhs_trusted: bool,
        platforms: Optional[Sequence[str]],
        crawler_commits: Dict[str, str],
        mentions_by_snap: Dict[str, List[EntityMention]],
    ) -> Tuple[List[SocialRawRecordV1], int]:
        """Group snapshots by record_id, pick PIT candidate, and check content eligibility."""
        by_record_id: Dict[str, List[Dict[str, Any]]] = {}
        for r_dict in dict_rows:
            rec_id = r_dict["record_id"]
            by_record_id.setdefault(rec_id, []).append(r_dict)

        target_platforms = set(platforms) if platforms else None
        eligible_records: List[SocialRawRecordV1] = []
        records_with_candidate_count = 0

        for rec_id, snap_list in by_record_id.items():
            cand = select_candidate_snapshot(snap_list, cutoff_utc)
            if cand is None:
                continue

            records_with_candidate_count += 1

            if target_platforms and cand["platform"] not in target_platforms:
                continue

            is_eligible, _ = check_content_eligibility(
                cand,
                window_start_utc=window_start_utc,
                cutoff_utc=cutoff_utc,
                xhs_last_update_time_trusted=xhs_trusted,
            )
            if not is_eligible:
                continue

            raw_record = self._build_raw_record(cand, crawler_commits, mentions_by_snap)
            if raw_record is not None:
                eligible_records.append(raw_record)

        return eligible_records, records_with_candidate_count

    def _sort_and_limit_records(
        self,
        records: List[SocialRawRecordV1],
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None,
    ) -> List[SocialRawRecordV1]:
        """Sort posts and comments by published_at / record_id and apply count limits."""
        posts = [r for r in records if r.record_type == "post"]
        comments = [r for r in records if r.record_type == "comment"]

        posts.sort(key=lambda r: (r.published_at or "", r.record_id), reverse=True)
        comments.sort(key=lambda r: (r.published_at or "", r.record_id), reverse=True)

        if max_posts is not None:
            posts = posts[:max_posts]
        if max_comments is not None:
            comments = comments[:max_comments]

        return posts + comments

    def _build_fetch_result(
        self,
        as_of: str,
        cutoff_iso: str,
        window_start_iso: str,
        records: List[SocialRawRecordV1],
        records_with_candidate_count: int,
        meta_dict: Dict[str, Any],
    ) -> SocialFetchResult:
        """Construct final SocialFetchResult based on available records and candidate presence."""
        if records:
            return SocialFetchResult(
                status=SocialStatus.AVAILABLE.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                records=records,
                reason_codes=[],
                meta=meta_dict,
            )
        elif records_with_candidate_count == 0:
            return SocialFetchResult(
                status=SocialStatus.REFUSED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                records=[],
                reason_codes=[REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT],
                meta=meta_dict,
            )
        else:
            return SocialFetchResult(
                status=SocialStatus.EMPTY.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                records=[],
                reason_codes=[REASON_SOCIAL_EMPTY],
                meta=meta_dict,
            )

    def fetch_records(
        self,
        symbol: str,
        as_of: str,
        lookback_days: int = 7,
        platforms: Optional[Sequence[str]] = None,
        max_posts: Optional[int] = None,
        max_comments: Optional[int] = None,
        now: Optional[datetime] = None,
        **kwargs: Any,
    ) -> SocialFetchResult:
        """Fetch qualified historical social media records for given symbol and as-of date (D-008)."""
        # 1. Check as_of and compute cutoff
        cutoff_res = self._resolve_cutoff(as_of, lookback_days, now)
        if isinstance(cutoff_res, SocialFetchResult):
            return cutoff_res
        window_start_utc, cutoff_utc, window_start_iso, cutoff_iso = cutoff_res

        # 2. Check DB path & connect
        db_file = self.db_path
        if not db_file or not str(db_file).strip():
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
            )

        conn_res = self._open_archive_connection(db_file, as_of, cutoff_iso, window_start_iso)
        if isinstance(conn_res, SocialFetchResult):
            return conn_res
        conn = conn_res

        try:
            # 3. Verify schema
            if not verify_archive_schema(conn):
                return SocialFetchResult(
                    status=SocialStatus.FAILED.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
                )

            # 4. Read metadata
            meta_dict = self._read_archive_meta(conn)
            xhs_trusted = meta_dict.get("xhs_last_update_time_trusted", "false").lower() == "true"

            # 5. Query candidate snapshots
            dict_rows = self._query_candidate_snapshots(conn, symbol)
            if not dict_rows:
                return SocialFetchResult(
                    status=SocialStatus.EMPTY.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    records=[],
                    reason_codes=[REASON_SOCIAL_EMPTY],
                    meta=meta_dict,
                )

            # 6. Load crawler commits & entity mentions
            run_ids = {r["ingest_run_id"] for r in dict_rows if r.get("ingest_run_id")}
            snap_ids = {r["snapshot_id"] for r in dict_rows if r.get("snapshot_id")}
            crawler_commits = self._load_crawler_commits(conn, run_ids)
            mentions_by_snap = self._load_snapshot_mentions(conn, snap_ids)

            # 7. Filter & assemble candidate records
            eligible_records, candidate_count = self._filter_and_assemble_records(
                dict_rows=dict_rows,
                window_start_utc=window_start_utc,
                cutoff_utc=cutoff_utc,
                xhs_trusted=xhs_trusted,
                platforms=platforms,
                crawler_commits=crawler_commits,
                mentions_by_snap=mentions_by_snap,
            )

            # 8. Sort, limit & build final result
            final_records = self._sort_and_limit_records(eligible_records, max_posts, max_comments)
            return self._build_fetch_result(
                as_of=as_of,
                cutoff_iso=cutoff_iso,
                window_start_iso=window_start_iso,
                records=final_records,
                records_with_candidate_count=candidate_count,
                meta_dict=meta_dict,
            )

        except sqlite3.OperationalError as exc:
            err_msg = str(exc).lower()
            if "locked" in err_msg or "busy" in err_msg:
                return SocialFetchResult(
                    status=SocialStatus.TIMEOUT.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    reason_codes=[REASON_SOCIAL_ARCHIVE_LOCKED],
                )
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
            )
        except sqlite3.DatabaseError:
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
            )
        finally:
            conn.close()

    def _resolve_target_symbols(self, symbol: str) -> Set[str]:
        target = {str(symbol).strip()}
        norm = normalize_stock_code(symbol)
        if norm:
            target.add(norm)
        if "." in symbol:
            target.add(symbol.split(".")[0])
        return target
