"""Social data service for status aggregation and read-only metadata (Task 12).

Specification:
- docs/social_data/implementation_plan.md Task 12, Task 13 & §8
- GET /v1/social-data/status: authenticated, read-only
- Separately reports:
  1. Crawler process execution status (crawler_status)
  2. Ingestion counts and audit status (ingestion_status)
  3. Archive snapshot freshness (freshness)
  4. Downstream analysis availability (analysis_availability)
- Proves: crawler success, ingestion success, archive freshness, and analysis availability
  are four independent dimensions.
- Strictly forbids returning operational merely because archive DB file exists.
- Strictly forbids returning post text, comment text, cookies, or secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import sqlite3
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from tradingagents.default_config import DEFAULT_CONFIG


class PlatformCoverageStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    status: str = "operational"


class RecentRunSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    as_of: Optional[str] = None
    symbol: Optional[str] = None
    status: Optional[str] = None
    post_count: int = 0
    comment_count: int = 0
    updated_at: Optional[str] = None
    rows_read: Optional[int] = 0
    rows_inserted: Optional[int] = 0
    rows_rejected: Optional[int] = 0
    run_id: Optional[str] = None


class CrawlerStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "not_run"
    last_run_at: Optional[str] = None
    crawler_commit: Optional[str] = None
    exit_code: Optional[int] = None


class IngestionStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "no_runs"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_rejected: int = 0
    last_ingest_at: Optional[str] = None
    run_id: Optional[str] = None


class FreshnessStatus(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str = "empty"
    latest_snapshot_at: Optional[str] = None
    latest_published_at: Optional[str] = None
    age_seconds: Optional[float] = None
    snapshot_count: int = 0


class AnalysisAvailability(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str = "disabled"
    available: bool = False
    status: str = "disabled"
    reason: str = "social_disabled"


class SocialDataStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str = Field("disabled", description="当前社交运行模式 (disabled/shadow/active)")
    schema_version: str = Field("social.status.v1", description="状态响应规范版本")
    status: str = Field("disabled", description="社交服务整体状态 (operational/disabled/degraded/error)")
    platform_coverage: Dict[str, Any] = Field(default_factory=dict, description="各平台覆盖与启用情况")
    reason_codes: List[str] = Field(default_factory=list, description="状态或异常原因代码")
    error_codes: List[str] = Field(default_factory=list, description="错误代码列表（别名）")
    recent_successful_run: Optional[Dict[str, Any]] = Field(None, description="最近成功运行摘要（无正文）")
    crawler_status: Optional[Dict[str, Any]] = Field(None, description="采集进程执行状态与结果")
    ingestion_status: Optional[Dict[str, Any]] = Field(None, description="导入记录与指标（行数等）")
    freshness: Optional[Dict[str, Any]] = Field(None, description="归档数据新鲜度评估")
    analysis_availability: Optional[Dict[str, Any]] = Field(None, description="分析端是否可用（mode与bundle可用性）")


_recent_run_cache: Optional[Dict[str, Any]] = None


def record_social_run_summary(
    *,
    as_of: str,
    symbol: str,
    status: str,
    post_count: int = 0,
    comment_count: int = 0,
    updated_at: Optional[str] = None,
    archive_db: Optional[str] = None,
    rows_read: Optional[int] = None,
    rows_inserted: Optional[int] = None,
    rows_rejected: Optional[int] = None,
    crawler_commit: Optional[str] = None,
    crawler_exit_code: Optional[int] = None,
    crawler_status: Optional[str] = None,
) -> None:
    """Record a summary of a social data run without post/comment content."""
    global _recent_run_cache
    now_iso = datetime.now(timezone.utc).isoformat()
    effective_updated_at = updated_at or now_iso

    _recent_run_cache = {
        "as_of": as_of,
        "symbol": symbol,
        "status": status,
        "post_count": post_count,
        "comment_count": comment_count,
        "updated_at": effective_updated_at,
        "rows_read": rows_read if rows_read is not None else post_count,
        "rows_inserted": rows_inserted if rows_inserted is not None else post_count,
        "rows_rejected": rows_rejected if rows_rejected is not None else 0,
        "crawler_commit": crawler_commit,
        "crawler_status": {
            "status": crawler_status or ("success" if crawler_exit_code == 0 else "completed" if status == "completed" else "unknown"),
            "last_run_at": effective_updated_at,
            "crawler_commit": crawler_commit,
            "exit_code": crawler_exit_code,
        } if (crawler_status or crawler_commit or crawler_exit_code is not None) else None,
    }


def read_archive_metrics(archive_db_path: str) -> Dict[str, Any]:
    """Read audit run history and snapshot metrics from archive SQLite DB."""
    res: Dict[str, Any] = {
        "has_archive": False,
        "latest_run": None,
        "latest_completed_run": None,
        "snapshot_count": 0,
        "latest_snapshot_at": None,
        "latest_published_at": None,
    }

    if not archive_db_path or not os.path.exists(archive_db_path) or not os.path.isfile(archive_db_path):
        return res

    try:
        conn = sqlite3.connect(archive_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

            # Check social_ingest_runs
            if "social_ingest_runs" in tables:
                res["has_archive"] = True
                # Latest run
                cursor.execute(
                    "SELECT run_id, provider, platform, query_text, started_at, completed_at, "
                    "status, crawler_commit, rows_read, rows_inserted, rows_rejected, error_code "
                    "FROM social_ingest_runs ORDER BY started_at DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    res["latest_run"] = {
                        "run_id": row[0],
                        "provider": row[1],
                        "platform": row[2],
                        "query_text": row[3],
                        "started_at": row[4],
                        "completed_at": row[5],
                        "status": row[6],
                        "crawler_commit": row[7],
                        "rows_read": row[8],
                        "rows_inserted": row[9],
                        "rows_rejected": row[10],
                        "error_code": row[11],
                    }

                # Latest completed run
                cursor.execute(
                    "SELECT run_id, provider, platform, query_text, started_at, completed_at, "
                    "status, crawler_commit, rows_read, rows_inserted, rows_rejected, error_code "
                    "FROM social_ingest_runs WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 1"
                )
                row_comp = cursor.fetchone()
                if row_comp:
                    res["latest_completed_run"] = {
                        "run_id": row_comp[0],
                        "provider": row_comp[1],
                        "platform": row_comp[2],
                        "query_text": row_comp[3],
                        "started_at": row_comp[4],
                        "completed_at": row_comp[5],
                        "status": row_comp[6],
                        "crawler_commit": row_comp[7],
                        "rows_read": row_comp[8],
                        "rows_inserted": row_comp[9],
                        "rows_rejected": row_comp[10],
                        "error_code": row_comp[11],
                    }

            # Check social_record_snapshots
            if "social_record_snapshots" in tables:
                cursor.execute("SELECT COUNT(*), MAX(snapshot_at), MAX(published_at) FROM social_record_snapshots")
                snap_row = cursor.fetchone()
                if snap_row:
                    res["snapshot_count"] = snap_row[0] or 0
                    res["latest_snapshot_at"] = snap_row[1]
                    res["latest_published_at"] = snap_row[2]

        finally:
            conn.close()
    except Exception:
        pass

    return res


def get_social_data_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate read-only status and metadata for social data subsystem.

    Explicitly reports the 4 distinct operational facets:
    1. crawler_status: MediaCrawler execution outcome
    2. ingestion_status: Archive DB imported row counts and status
    3. freshness: Snapshot recency and age
    4. analysis_availability: Whether downstream analysis can consume the archive
    """
    cfg = config or DEFAULT_CONFIG
    social_cfg = cfg.get("social", {}) if isinstance(cfg, dict) else {}
    mode = os.getenv("TA_SOCIAL_MODE", social_cfg.get("mode", "disabled"))
    provider = os.getenv("TA_SOCIAL_PROVIDER", social_cfg.get("provider", "archive_sqlite"))
    archive_db = os.getenv("TA_SOCIAL_ARCHIVE_DB", social_cfg.get("archive_db", "")).strip()
    platforms_raw = os.getenv("TA_SOCIAL_PLATFORMS", social_cfg.get("platforms", "xhs,dy"))
    platform_list = [p.strip() for p in platforms_raw.split(",") if p.strip()]

    # Read real archive database metrics if configured
    archive_metrics = read_archive_metrics(archive_db) if archive_db else {
        "has_archive": False,
        "latest_run": None,
        "latest_completed_run": None,
        "snapshot_count": 0,
        "latest_snapshot_at": None,
        "latest_published_at": None,
    }

    # -------------------------------------------------------------------------
    # 1. Crawler Status (采集进程结果)
    # -------------------------------------------------------------------------
    crawler_status: Dict[str, Any]
    if _recent_run_cache and _recent_run_cache.get("crawler_status"):
        crawler_status = dict(_recent_run_cache["crawler_status"])
    elif archive_metrics.get("latest_run"):
        lr = archive_metrics["latest_run"]
        crawler_status = {
            "status": "success" if lr.get("status") == "completed" else lr.get("status", "unknown"),
            "last_run_at": lr.get("started_at"),
            "crawler_commit": lr.get("crawler_commit"),
            "exit_code": 0 if lr.get("status") == "completed" else 1,
        }
    else:
        crawler_status = {
            "status": "not_run",
            "last_run_at": None,
            "crawler_commit": None,
            "exit_code": None,
        }

    # -------------------------------------------------------------------------
    # 2. Ingestion Status (导入行数与审计记录)
    # -------------------------------------------------------------------------
    ingestion_status: Dict[str, Any]
    if archive_metrics.get("latest_run"):
        lr = archive_metrics["latest_run"]
        ingestion_status = {
            "status": lr.get("status", "completed"),
            "rows_read": lr.get("rows_read", 0),
            "rows_inserted": lr.get("rows_inserted", 0),
            "rows_rejected": lr.get("rows_rejected", 0),
            "last_ingest_at": lr.get("completed_at") or lr.get("started_at"),
            "run_id": lr.get("run_id"),
        }
    elif _recent_run_cache:
        ingestion_status = {
            "status": _recent_run_cache.get("status", "completed"),
            "rows_read": _recent_run_cache.get("rows_read", 0),
            "rows_inserted": _recent_run_cache.get("rows_inserted", _recent_run_cache.get("post_count", 0)),
            "rows_rejected": _recent_run_cache.get("rows_rejected", 0),
            "last_ingest_at": _recent_run_cache.get("updated_at"),
            "run_id": None,
        }
    else:
        ingestion_status = {
            "status": "no_runs",
            "rows_read": 0,
            "rows_inserted": 0,
            "rows_rejected": 0,
            "last_ingest_at": None,
            "run_id": None,
        }

    # -------------------------------------------------------------------------
    # 3. Freshness (归档新鲜度)
    # -------------------------------------------------------------------------
    freshness: Dict[str, Any]
    snap_count = archive_metrics.get("snapshot_count", 0)
    latest_snap = archive_metrics.get("latest_snapshot_at")
    latest_pub = archive_metrics.get("latest_published_at")

    lookback_days = int(os.getenv("TA_SOCIAL_LOOKBACK_DAYS") or social_cfg.get("lookback_days") or "7")
    max_age_seconds_cfg = os.getenv("TA_SOCIAL_FRESHNESS_MAX_AGE_SECONDS")
    freshness_max_age_seconds = float(max_age_seconds_cfg) if max_age_seconds_cfg else float(lookback_days * 86400)

    if snap_count > 0 and latest_snap:
        age_seconds: Optional[float] = None
        freshness_status = "stale"
        try:
            # Parse ISO 8601 timestamp (e.g. 2026-08-26T06:10:00Z)
            snap_clean = latest_snap.replace("Z", "+00:00")
            dt = datetime.fromisoformat(snap_clean)
            now = datetime.now(timezone.utc)
            age_seconds = max(0.0, (now - dt).total_seconds())
            if age_seconds <= freshness_max_age_seconds:
                freshness_status = "fresh"
        except Exception:
            freshness_status = "unknown"

        freshness = {
            "status": freshness_status,
            "latest_snapshot_at": latest_snap,
            "latest_published_at": latest_pub,
            "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
            "snapshot_count": snap_count,
        }
    else:
        freshness = {
            "status": "empty",
            "latest_snapshot_at": None,
            "latest_published_at": None,
            "age_seconds": None,
            "snapshot_count": 0,
        }

    # -------------------------------------------------------------------------
    # 4. Analysis Availability & Overall Status (分析端可用性)
    # -------------------------------------------------------------------------
    # Rule: Forbid returning operational merely because archive DB file exists.
    # Must verify archive actually contains non-empty usable snapshot data and completed runs.
    reason_codes: List[str] = []
    analysis_availability: Dict[str, Any]
    overall_status: str

    if mode == "disabled":
        overall_status = "disabled"
        reason_codes.append("social_disabled")
        analysis_availability = {
            "mode": "disabled",
            "available": False,
            "status": "disabled",
            "reason": "social_disabled",
        }
    elif mode in ("shadow", "active"):
        if not archive_db:
            overall_status = "error"
            reason_codes.append("social_archive_missing")
            analysis_availability = {
                "mode": mode,
                "available": False,
                "status": "error",
                "reason": "social_archive_missing",
            }
        elif not os.path.isabs(archive_db) or not os.path.exists(archive_db):
            overall_status = "degraded"
            reason_codes.append("social_archive_missing")
            analysis_availability = {
                "mode": mode,
                "available": False,
                "status": "degraded",
                "reason": "social_archive_missing",
            }
        else:
            # Archive file exists on disk: verify actual data presence
            if snap_count == 0 or not archive_metrics.get("latest_completed_run"):
                overall_status = "degraded"
                reason_codes.append("social_archive_empty")
                analysis_availability = {
                    "mode": mode,
                    "available": False,
                    "status": "degraded",
                    "reason": "social_archive_empty",
                }
            elif freshness["status"] == "stale":
                overall_status = "degraded"
                reason_codes.append("social_archive_stale")
                analysis_availability = {
                    "mode": mode,
                    "available": True,
                    "status": "degraded",
                    "reason": "social_archive_stale",
                }
            else:
                overall_status = "operational"
                analysis_availability = {
                    "mode": mode,
                    "available": True,
                    "status": "operational",
                    "reason": "social_archive_operational",
                }
    else:
        overall_status = "unknown"
        reason_codes.append("unknown_mode")
        analysis_availability = {
            "mode": mode,
            "available": False,
            "status": "unknown",
            "reason": "unknown_mode",
        }

    # -------------------------------------------------------------------------
    # 5. Recent Successful Run & Platform Coverage
    # -------------------------------------------------------------------------
    recent_successful_run: Optional[Dict[str, Any]] = None
    if archive_metrics.get("latest_completed_run"):
        lcr = archive_metrics["latest_completed_run"]
        recent_successful_run = {
            "as_of": lcr.get("completed_at", "")[:10] if lcr.get("completed_at") else None,
            "symbol": lcr.get("query_text"),
            "status": "completed",
            "post_count": lcr.get("rows_inserted", 0),
            "comment_count": 0,
            "updated_at": lcr.get("completed_at") or "",
            "rows_read": lcr.get("rows_read", 0),
            "rows_inserted": lcr.get("rows_inserted", 0),
            "rows_rejected": lcr.get("rows_rejected", 0),
            "run_id": lcr.get("run_id"),
        }
    elif _recent_run_cache:
        recent_successful_run = _recent_run_cache

    platform_coverage: Dict[str, Any] = {}
    for p in ("xhs", "dy"):
        enabled = p in platform_list
        if mode == "disabled" or not enabled:
            p_status = "inactive"
        elif overall_status == "operational":
            p_status = "operational"
        else:
            p_status = overall_status

        platform_coverage[p] = {
            "enabled": enabled,
            "status": p_status,
        }

    return {
        "mode": mode,
        "schema_version": "social.status.v1",
        "status": overall_status,
        "platform_coverage": platform_coverage,
        "reason_codes": reason_codes,
        "error_codes": reason_codes,
        "recent_successful_run": recent_successful_run,
        "crawler_status": crawler_status,
        "ingestion_status": ingestion_status,
        "freshness": freshness,
        "analysis_availability": analysis_availability,
    }

