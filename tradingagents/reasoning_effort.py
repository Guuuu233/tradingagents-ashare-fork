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

# Providers that factory.create_llm_client maps to OpenAIClient, which forwards
# ``reasoning_effort`` to the request. Keep in sync with llm_clients/factory.py.
OPENAI_COMPATIBLE_PROVIDERS = ("openai", "ollama", "openrouter", "xai", "deepseek")


def consumed_effort(provider: Optional[str], kwargs: Any) -> Optional[str]:
    """Thinking level the client for ``provider`` actually sends, else ``None``.

    OpenAI-compatible clients consume ``reasoning_effort``; Google consumes
    ``thinking_level``; Anthropic (and anything unknown) consumes neither, so a
    value that merely sits in ``kwargs`` is not recorded as "passed".
    """
    name = (provider or "").lower()
    if name in OPENAI_COMPATIBLE_PROVIDERS:
        return kwargs.get("reasoning_effort") or None
    if name == "google":
        return kwargs.get("thinking_level") or None
    return None


def config_key(tier: str) -> str:
    """Runtime-config key carrying the user's effort for ``tier``."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier: {tier!r}")
    return f"{tier}_reasoning_effort"


def intersect_levels(*level_sets: Optional[List[str]]) -> List[str]:
    """Ordered (low/medium/high) intersection of level sets.

    Any set that is ``None`` (unreadable) or empty makes the result empty, which
    callers treat as "not adjustable".
    """
    if not level_sets:
        return []
    common = set(REASONING_EFFORT_WHITELIST)
    for levels in level_sets:
        if not isinstance(levels, (list, tuple, set, frozenset)):
            return []
        common &= {lv for lv in levels if isinstance(lv, str)}
    return [lv for lv in REASONING_EFFORT_WHITELIST if lv in common]


def effective_effort(member_effort: Any, group_levels: Optional[List[str]]) -> Optional[str]:
    """Level that may actually be sent: member-registered effort ∩ group levels.

    ``member_effort`` is the level registered for the member (``user_llm_configs``),
    ``group_levels`` the levels the group/catalog entry offers (``None`` =
    unreadable). Returns ``None`` — send no effort parameter, show "not
    adjustable" — when the member registration is missing/invalid, the group
    levels are unreadable, or the intersection is empty. It never falls back to a
    group default and never reports a level the group does not offer.
    """
    if not isinstance(member_effort, str):
        return None
    member = member_effort.strip().lower()
    if member not in REASONING_EFFORT_WHITELIST:
        return None
    common = intersect_levels([member], group_levels)
    return common[0] if common else None


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
