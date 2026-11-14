"""DAV-2453: user-selectable thinking level ("reasoning effort") helpers.

Semantics
- ``None`` (DB NULL) means "follow the model": no effort parameter is sent and
  the LLM request body is byte-identical to the pre-DAV-2453 behaviour.
- Only the whitelist below is accepted; the value is never placed in a prompt.
"""

from __future__ import annotations

from typing import Any, List, Optional

REASONING_EFFORT_WHITELIST = ("low", "medium", "high")
# API-level token meaning "back to follow-the-model" (stored as NULL).
REASONING_EFFORT_FOLLOW = "follow"

TIERS = ("quick", "deep")


def config_key(tier: str) -> str:
    """Runtime-config key carrying the user's effort for ``tier``."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}")
    return f"{tier}_reasoning_effort"


def normalize_reasoning_effort(value: Any) -> Optional[str]:
    """Validate an effort value coming from an untrusted source.

    Returns the whitelisted lowercase value. Raises ``ValueError`` for anything
    else (including empty strings and ``None`` — callers decide what those mean).
    """
    if isinstance(value, str):
        v = value.strip().lower()
        if v in REASONING_EFFORT_WHITELIST:
            return v
    raise ValueError(f"reasoning effort must be one of {list(REASONING_EFFORT_WHITELIST)}")


def parse_supported_levels(item: Any) -> Optional[List[str]]:
    """Extract whitelisted levels from one /v1/models item.

    The gateway lists ``supported_reasoning_levels`` as ``[{"effort": "high",
    ...}]`` (plain strings are tolerated). Returns ``None`` when the field is
    absent/unreadable (caller must treat that as "not adjustable"); otherwise
    the ordered, de-duplicated subset of low/medium/high.
    """
    if not isinstance(item, dict):
        return None
    raw = item.get("supported_reasoning_levels")
    if not isinstance(raw, list):
        return None
    out: List[str] = []
    for entry in raw:
        name = entry.get("effort") if isinstance(entry, dict) else entry
        if isinstance(name, str):
            n = name.strip().lower()
            if n in REASONING_EFFORT_WHITELIST and n not in out:
                out.append(n)
    return [lv for lv in REASONING_EFFORT_WHITELIST if lv in out]
