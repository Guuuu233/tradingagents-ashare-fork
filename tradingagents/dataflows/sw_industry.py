"""SW2021 L1 point-in-time membership for manager prompts (no index prices)."""
from __future__ import annotations

from datetime import date, datetime
import logging
import re
from typing import Any

import pandas as pd

from tradingagents.dataflows.providers.industry_linkage_provider import _query_tushare_api

logger = logging.getLogger(__name__)
_CLASSIFY_FIELDS = "index_code,industry_name,level"
_MEMBER_FIELDS = "ts_code,l1_code,l1_name,in_date,out_date"
_GAP_PREFIX = "【数据获取失败】申万一级行业基准："


def _membership_date(value: Any, *, optional: bool = False) -> date | None:
    if value is None or (isinstance(value, str) and not value.strip()) or pd.isna(value):
        if optional:
            return None
        raise ValueError("membership_in_date_missing")
    if not isinstance(value, str):
        raise ValueError("membership_date_invalid")
    text = value.strip()
    fmt = "%Y%m%d" if re.fullmatch(r"\d{8}", text) else "%Y-%m-%d"
    if fmt == "%Y-%m-%d" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("membership_date_invalid")
    return datetime.strptime(text, fmt).date()


def _query_rows(api_name: str, **kwargs: Any) -> list[dict]:
    try:
        frame, category, _note = _query_tushare_api(api_name, **kwargs)
    except Exception:
        # Even an unexpected transport error is a gap, not a failed forecast node.
        raise ValueError(f"{api_name}_unavailable") from None
    if category == "empty_rows":
        return []
    if category or not isinstance(frame, pd.DataFrame):
        # Provider notes may contain endpoint/credential material: never echo them.
        raise ValueError(f"{api_name}_unavailable")
    required = set(kwargs["fields"].split(","))
    if not required.issubset(frame.columns):
        raise ValueError(f"{api_name}_schema_invalid")
    if len(frame) >= 2000 and api_name == "index_member_all":
        raise ValueError("membership_response_possibly_truncated")
    return frame.to_dict("records")


def _resolve_membership(classification: list[dict], members: list[dict], symbol: str, cutoff: date) -> dict:
    industries = {}
    for row in classification:
        if row.get("level") != "L1":
            continue
        code, name = row.get("index_code"), row.get("industry_name")
        if not isinstance(code, str) or not isinstance(name, str) or not name.strip():
            raise ValueError("industry_classification_invalid")
        if code in industries and industries[code] != name.strip():
            raise ValueError("industry_classification_ambiguous")
        industries[code] = name.strip()
    active = set()
    for row in members:
        if row.get("ts_code") != symbol:
            raise ValueError("membership_symbol_mismatch")
        joined = _membership_date(row.get("in_date"))
        left = _membership_date(row.get("out_date"), optional=True)
        if joined <= cutoff and (left is None or left > cutoff):
            code = row.get("l1_code")
            if code not in industries:
                raise ValueError("membership_not_in_SW2021_L1")
            active.add(code)
    if len(active) != 1:
        raise ValueError("membership_missing_or_ambiguous")
    code = active.pop()
    return {"src": "SW2021", "level": "L1", "code": code, "name": industries[code]}


def resolve_sw_l1_benchmark(symbol: str, analysis_date: str) -> tuple[dict | None, str | None]:
    """Resolve membership at in_date <= D < out_date, never today's-only list.

    index_member_all defaults to is_new=Y (official doc 335). Query BOTH
    current and exited memberships: filtering the default list alone leaks
    future reclassifications into historical analyses.
    """
    try:
        if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", symbol):
            raise ValueError("symbol_not_supported")
        cutoff = _membership_date(analysis_date)
        classification = _query_rows(
            "index_classify", fields=_CLASSIFY_FIELDS,
            params={"level": "L1", "src": "SW2021"},
        )
        members = []
        for is_new in ("Y", "N"):
            members.extend(_query_rows(
                "index_member_all", ts_code=symbol, fields=_MEMBER_FIELDS,
                params={"is_new": is_new},
            ))
        return _resolve_membership(classification, members, symbol, cutoff), None
    except (ValueError, TypeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "membership_schema_invalid"
        # No provider exception text: only our deterministic error identifiers.
        if not re.fullmatch(r"[A-Za-z0-9_]+", reason):
            reason = "membership_date_invalid"
        logger.warning("[SW2021] industry benchmark unavailable: %s", reason)
        return None, _GAP_PREFIX + reason
