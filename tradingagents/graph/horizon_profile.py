"""Unique horizon resolution and profile contract definitions.

Defines the single source of truth for analysis horizon normalization,
validation, profile metadata, and resolution source tracking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

# Supported analysis horizons
SUPPORTED_HORIZONS: Tuple[str, ...] = ("short", "medium")
CANONICAL_HORIZONS: Tuple[str, ...] = ("short", "medium")
HORIZON_SHORT: str = "short"
HORIZON_MEDIUM: str = "medium"

# Resolution sources
RESOLUTION_SOURCE_EXPLICIT: str = "explicit"
RESOLUTION_SOURCE_DEFAULT: str = "default"

# Product evaluation constants (profile constants only; no return calculations here)
T_PLUS_10: int = 10
T_PLUS_40: int = 40

HORIZON_PROFILE_V1: dict[str, Any] = {
    "short": {
        "horizon": "short",
        "min_trading_days": 5,
        "max_trading_days": 20,
        "primary_eval_offset": T_PLUS_10,
        "eval_label": "T+10",
        "description": "短线：5-20交易日，主评价T+10",
    },
    "medium": {
        "horizon": "medium",
        "min_trading_days": 21,
        "max_trading_days": 60,
        "primary_eval_offset": T_PLUS_40,
        "eval_label": "T+40",
        "description": "中线：21-60交易日，主评价T+40",
    },
}
# Expose both lower and upper case variants
horizon_profile_v1 = HORIZON_PROFILE_V1


# Sentinel object for detecting unprovided / omitted horizon arguments
class _UnsetType:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<HORIZONS_UNSET>"

    def __bool__(self) -> bool:
        return False


_HORIZONS_UNSET = _UnsetType()


# Regex for natural-language dual horizon mentions (used ONLY for non-blocking notices)
_DUAL_HORIZON_QUERY_RE = re.compile(
    r"(?:短线|短期).{0,16}(?:中线|中期)|(?:中线|中期).{0,16}(?:短线|短期)"
    r"|短中都看看|短中兼顾|短线和中线|短期和中期"
    r"|short(?:[- ]term)?.{0,24}medium(?:[- ]term)?"
    r"|medium(?:[- ]term)?.{0,24}short(?:[- ]term)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HorizonResolution:
    resolved: List[str]
    resolution_source: str
    notice: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": list(self.resolved),
            "resolution_source": self.resolution_source,
            "notice": self.notice,
        }


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    """Deduplicate elements while strictly preserving first-seen order."""
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def resolve_analysis_horizons(
    raw: Any = _HORIZONS_UNSET,
    *,
    query: Optional[str] = None,
    explicit: Optional[bool] = None,
) -> HorizonResolution:
    """Resolve and validate analysis horizons according to the unique horizon contract.

    Contract rules:
    1. Explicit valid list (only 'short' and/or 'medium', deduplicated preserving order)
       -> adopted as-is, resolution_source='explicit'.
    2. Field not provided (omitted in JSON or Python constructor)
       -> resolved=['short'], resolution_source='default'.
    3. Explicit null (None), empty list ([]), or illegal values (e.g. ['unknown'], ['short', 'bogus'])
       -> raises ValueError (validation error).
    4. Explicit single horizon + text mentioning dual horizons
       -> resolved remains that single horizon; optional non-blocking notice; resolved NEVER changed.
    5. Unprovided + text explicitly mentioning dual horizons
       -> resolved remains ['short'] with resolution_source='default'; optional non-blocking notice.
    6. Repeated normalization must not turn 'default' into 'explicit', nor rewrite resolved using query.
    """
    query_text = str(query or "")
    has_dual_mention = bool(_DUAL_HORIZON_QUERY_RE.search(query_text))

    # Idempotent re-normalization: if raw is already a HorizonResolution, preserve its contract
    if isinstance(raw, HorizonResolution):
        # Secondary normalization must not flip default -> explicit, nor rewrite resolved by query
        notice = raw.notice
        if not notice and has_dual_mention:
            if raw.resolution_source == RESOLUTION_SOURCE_DEFAULT:
                notice = "未显式指定分析期限，已默认使用短线分析；如需同时分析短线和中线，请显式勾选或传入双档参数"
            elif len(raw.resolved) == 1:
                notice = f"已按显式选档 [{raw.resolved[0]}] 分析，文本中提及的双档意图未生效"
        return HorizonResolution(
            resolved=list(raw.resolved),
            resolution_source=raw.resolution_source,
            notice=notice,
        )

    # Determine whether raw was provided vs unprovided
    is_unprovided = (raw is _HORIZONS_UNSET) or (explicit is False)

    if is_unprovided:
        notice = None
        if has_dual_mention:
            notice = "未显式指定分析期限，已默认使用短线分析；如需同时分析短线和中线，请显式勾选或传入双档参数"
        return HorizonResolution(
            resolved=["short"],
            resolution_source=RESOLUTION_SOURCE_DEFAULT,
            notice=notice,
        )

    # From here on, raw was explicitly provided (explicit is True or raw was passed)
    if raw is None:
        raise ValueError("显式 null 为非法期限配置，请输入合法期限列表如 ['short'] 或 ['short', 'medium']")

    if not isinstance(raw, (list, tuple, set)):
        raise ValueError(f"期限参数必须为列表或元组，收到: {type(raw).__name__}")

    if len(raw) == 0:
        raise ValueError("期限列表不能为空，请输入合法期限列表如 ['short'] 或 ['short', 'medium']")

    cleaned: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"期限列表项必须为字符串，收到非法值: {item!r}")
        normalized = item.strip().lower()
        if normalized not in SUPPORTED_HORIZONS:
            raise ValueError(
                f"包含非法期限值 '{item}'，支持的合法期限为: {list(SUPPORTED_HORIZONS)}"
            )
        cleaned.append(normalized)

    # Deduplicate while preserving user-specified order
    resolved = dedupe_preserve_order(cleaned)
    if not resolved:
        raise ValueError("期限列表不能为空，请输入合法期限列表如 ['short'] 或 ['short', 'medium']")

    notice = None
    if len(resolved) == 1 and has_dual_mention:
        notice = f"已按显式选档 [{resolved[0]}] 分析，文本中提及的双档意图未生效"

    return HorizonResolution(
        resolved=resolved,
        resolution_source=RESOLUTION_SOURCE_EXPLICIT,
        notice=notice,
    )
