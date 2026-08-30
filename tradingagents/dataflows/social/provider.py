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
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable
from zoneinfo import ZoneInfo

from tradingagents.dataflows.trade_calendar import CN_TZ, now_cn
from tradingagents.dataflows.social.archive_schema import verify_archive_schema
from tradingagents.dataflows.social.contracts import (
    REASON_OBSERVED_AFTER_CUTOFF_EXCLUDED,
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

    Rule (D-008):
    - Select snapshots where snapshot_at <= cutoff.
    - Pick the snapshot with MAXIMUM snapshot_at.
    - Tie-breaker: latest ingest_at or snapshot_id.
    - Returns None if no snapshot has snapshot_at <= cutoff.
    """
    valid_snapshots = []
    for s in snapshots:
        s_at = parse_iso_datetime(s.get("snapshot_at"))
        if s_at is not None and s_at <= cutoff_utc:
            valid_snapshots.append((s_at, s.get("ingest_at") or "", s.get("snapshot_id") or "", s))

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

        # 2. Check DB path
        db_file = self.db_path
        if not db_file or not str(db_file).strip():
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
            )

        # 3. Connect read-only
        try:
            conn = self._get_readonly_connection(db_file)
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
                reason_codes=[REASON_SOCIAL_ARCHIVE_MISSING],
            )
        except sqlite3.DatabaseError:
            return SocialFetchResult(
                status=SocialStatus.FAILED.value,
                requested_as_of=as_of,
                cutoff_at=cutoff_iso,
                window_start=window_start_iso,
                reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
            )

        try:
            # 4. Verify schema
            if not verify_archive_schema(conn):
                return SocialFetchResult(
                    status=SocialStatus.FAILED.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    reason_codes=[REASON_SOCIAL_SCHEMA_MISMATCH],
                )

            # 5. Read meta
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM social_archive_meta")
            meta_dict = {row["key"]: row["value"] for row in cursor.fetchall()}
            xhs_trusted = meta_dict.get("xhs_last_update_time_trusted", "false").lower() == "true"

            # 6. Normalize symbol and query candidate snapshots
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
            cursor.execute(query, sym_list + sym_list)
            raw_rows = cursor.fetchall()

            if not raw_rows:
                return SocialFetchResult(
                    status=SocialStatus.EMPTY.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    records=[],
                    reason_codes=[REASON_SOCIAL_EMPTY],
                    meta=meta_dict,
                )

            dict_rows = [dict(r) for r in raw_rows]

            # 7. Group snapshots by record_id
            by_record_id: Dict[str, List[Dict[str, Any]]] = {}
            for r_dict in dict_rows:
                rec_id = r_dict["record_id"]
                by_record_id.setdefault(rec_id, []).append(r_dict)

            # 8. Load crawler commits and mentions
            run_ids = {r_dict["ingest_run_id"] for r_dict in dict_rows if r_dict.get("ingest_run_id")}
            crawler_commits: Dict[str, str] = {}
            if run_ids:
                run_ph = ",".join("?" for _ in run_ids)
                cursor.execute(
                    f"SELECT run_id, crawler_commit FROM social_ingest_runs WHERE run_id IN ({run_ph})",
                    list(run_ids),
                )
                crawler_commits = {row["run_id"]: row["crawler_commit"] for row in cursor.fetchall()}

            snap_ids = {r_dict["snapshot_id"] for r_dict in dict_rows}
            mentions_by_snap: Dict[str, List[EntityMention]] = {}
            if snap_ids:
                snap_ph = ",".join("?" for _ in snap_ids)
                cursor.execute(
                    f"SELECT * FROM social_entity_mentions WHERE snapshot_id IN ({snap_ph})",
                    list(snap_ids),
                )
                for m_row in cursor.fetchall():
                    m_dict = dict(m_row)
                    mentions_by_snap.setdefault(m_dict["snapshot_id"], []).append(
                        EntityMention.from_dict(m_dict)
                    )

            target_platforms = set(platforms) if platforms else None
            eligible_records: List[SocialRawRecordV1] = []
            records_with_candidate_count = 0

            # 9. Apply snapshot candidate selection and eligibility checks
            for rec_id, snap_list in by_record_id.items():
                cand = select_candidate_snapshot(snap_list, cutoff_utc)
                if cand is None:
                    continue  # No historical snapshot <= cutoff

                records_with_candidate_count += 1

                # Platform filtering
                if target_platforms and cand["platform"] not in target_platforms:
                    continue

                # Content eligibility
                is_eligible, _ = check_content_eligibility(
                    cand,
                    window_start_utc=window_start_utc,
                    cutoff_utc=cutoff_utc,
                    xhs_last_update_time_trusted=xhs_trusted,
                )
                if not is_eligible:
                    continue

                # Construct SocialRawRecordV1
                metrics = SocialMetrics.from_dict(json.loads(cand.get("metrics_json") or "{}"))
                crawler_commit = crawler_commits.get(
                    cand["ingest_run_id"], "d6f7c5bb906b6dac40ddf343ef9e26438a3de092"
                )
                source_ref = SourceRef(
                    provider="mediacrawler",
                    crawler_commit=crawler_commit,
                    source_table=cand["source_table"],
                    source_row_id=cand["source_row_id"],
                )
                entities = mentions_by_snap.get(cand["snapshot_id"], [])

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
                eligible_records.append(raw_record)

            # Sort and apply limits
            posts = [r for r in eligible_records if r.record_type == "post"]
            comments = [r for r in eligible_records if r.record_type == "comment"]

            posts.sort(key=lambda r: (r.published_at or "", r.record_id), reverse=True)
            comments.sort(key=lambda r: (r.published_at or "", r.record_id), reverse=True)

            if max_posts is not None:
                posts = posts[:max_posts]
            if max_comments is not None:
                comments = comments[:max_comments]

            final_records = posts + comments

            if final_records:
                return SocialFetchResult(
                    status=SocialStatus.AVAILABLE.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    records=final_records,
                    reason_codes=[],
                    meta=meta_dict,
                )
            elif records_with_candidate_count == 0:
                # All snapshots in DB for this symbol were taken after cutoff
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
                # Candidate snapshots existed but all failed eligibility (e.g. outside window)
                return SocialFetchResult(
                    status=SocialStatus.EMPTY.value,
                    requested_as_of=as_of,
                    cutoff_at=cutoff_iso,
                    window_start=window_start_iso,
                    records=[],
                    reason_codes=[REASON_SOCIAL_EMPTY],
                    meta=meta_dict,
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
