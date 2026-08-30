"""Social data service for status aggregation and read-only metadata (Task 12).

Specification:
- docs/social_data/implementation_plan.md Task 12 & §8
- GET /v1/social-data/status: authenticated, read-only
- Returns: mode, schema_version, status, recent_successful_run, platform_coverage, reason_codes
- Strictly forbids returning post text, comment text, cookies, or secrets.
"""

from __future__ import annotations

import os
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


class SocialDataStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str = Field("disabled", description="当前社交运行模式 (disabled/shadow/active)")
    schema_version: str = Field("social.status.v1", description="状态响应规范版本")
    status: str = Field("disabled", description="社交服务整体状态 (operational/disabled/degraded/error)")
    platform_coverage: Dict[str, Any] = Field(default_factory=dict, description="各平台覆盖与启用情况")
    reason_codes: List[str] = Field(default_factory=list, description="状态或异常原因代码")
    error_codes: List[str] = Field(default_factory=list, description="错误代码列表（别名）")
    recent_successful_run: Optional[Dict[str, Any]] = Field(None, description="最近成功运行摘要（无正文）")


_recent_run_cache: Optional[Dict[str, Any]] = None


def record_social_run_summary(
    *,
    as_of: str,
    symbol: str,
    status: str,
    post_count: int = 0,
    comment_count: int = 0,
    updated_at: Optional[str] = None,
) -> None:
    """Record a summary of a social data run without post/comment content."""
    global _recent_run_cache
    _recent_run_cache = {
        "as_of": as_of,
        "symbol": symbol,
        "status": status,
        "post_count": post_count,
        "comment_count": comment_count,
        "updated_at": updated_at or "",
    }


def get_social_data_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate read-only status and metadata for social data subsystem."""
    cfg = config or DEFAULT_CONFIG
    social_cfg = cfg.get("social", {}) if isinstance(cfg, dict) else {}
    mode = os.getenv("TA_SOCIAL_MODE", social_cfg.get("mode", "disabled"))
    provider = os.getenv("TA_SOCIAL_PROVIDER", social_cfg.get("provider", "archive_sqlite"))
    archive_db = os.getenv("TA_SOCIAL_ARCHIVE_DB", social_cfg.get("archive_db", "")).strip()
    platforms_raw = os.getenv("TA_SOCIAL_PLATFORMS", social_cfg.get("platforms", "xhs,dy"))
    platform_list = [p.strip() for p in platforms_raw.split(",") if p.strip()]

    platform_coverage: Dict[str, Any] = {}
    for p in ("xhs", "dy"):
        platform_coverage[p] = {
            "enabled": p in platform_list,
            "status": "operational" if (mode != "disabled" and p in platform_list) else "inactive",
        }

    reason_codes: List[str] = []
    if mode == "disabled":
        status = "disabled"
        reason_codes.append("social_disabled")
    elif mode in ("shadow", "active"):
        if not archive_db:
            status = "error"
            reason_codes.append("social_archive_missing")
        elif not os.path.isabs(archive_db) or not os.path.exists(archive_db):
            status = "degraded"
            reason_codes.append("social_archive_missing")
        else:
            status = "operational"
    else:
        status = "unknown"

    return {
        "mode": mode,
        "schema_version": "social.status.v1",
        "status": status,
        "platform_coverage": platform_coverage,
        "reason_codes": reason_codes,
        "error_codes": reason_codes,
        "recent_successful_run": _recent_run_cache,
    }
