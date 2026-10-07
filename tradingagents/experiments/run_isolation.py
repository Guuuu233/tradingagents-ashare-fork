"""Experiment run-isolation helpers (DAV-1477 / Phase-2 P3).

Provides run isolation for same-process repeated runs, version pairing and
A/B experiments — the shared precondition of DAV-1450 (paired A/B), DAV-1455
(run-to-run instability quantification) and M1 replicate runs.

Production defaults are intentionally untouched:

* ``TradingAgentsGraph`` still falls back to the class-level shared
  ``MemorySaver`` and the production default thread name
  ``"{company}_{trade_date}_{horizon}"`` when no checkpointer is injected.
* The isolation guard only activates when the caller opts in via
  ``experiment_mode=True`` (or an injected ``checkpointer_identity``) — see
  ``TradingAgentsGraph.__init__``.

Experiment path contract:

* Every experiment run uses an independent checkpointer instance (never the
  class-level shared one).
* Every experiment run passes an explicit ``thread_id`` of the form
  ``"{sample_id}:{system_version}:{arm}:{replicate}:{uuid}"``.
* If an experiment-mode graph reaches ``propagate()`` without an explicit
  ``thread_id``, it raises ``ExperimentThreadIdError`` instead of silently
  falling back to the production default thread name.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver

# Default thread name pattern used by propagate() for standalone production
# runs: "{company}_{trade_date}_{horizon}".  An experiment thread id must
# never collide with this shape.
EXPERIMENT_THREAD_ID_PARTS = 5


class ExperimentThreadIdError(RuntimeError):
    """Raised when an experiment-mode run lacks an explicit valid thread_id."""


def make_experiment_thread_id(
    sample_id: str,
    system_version: str,
    arm: str,
    replicate: Any,
) -> str:
    """Build the experiment thread id ``{sample_id}:{system_version}:{arm}:{replicate}:{uuid}``.

    The first four fields MUST NOT contain ``:`` — the id is parsed by
    splitting on ``:`` into exactly 5 segments, so a colon inside
    ``sample_id``/``system_version``/``arm``/``replicate`` would silently
    corrupt the layout.  Empty fields are likewise rejected (DAV-1486).

    The uuid suffix guarantees uniqueness per invocation so two replicates can
    never share a checkpoint thread even when all other fields match.
    """
    head = (sample_id, system_version, arm, replicate)
    labels = ("sample_id", "system_version", "arm", "replicate")
    for label, value in zip(labels, head):
        text = str(value)
        if not text:
            raise ValueError(
                f"experiment thread_id field {label!r} must be non-empty "
                f"(got {value!r})"
            )
        if ":" in text:
            raise ValueError(
                f"experiment thread_id field {label!r} must not contain ':' "
                f"(got {text!r}); the id layout is parsed by splitting on ':' "
                "into exactly 5 segments"
            )
    parts = (*head, uuid.uuid4().hex)
    return ":".join(str(p) for p in parts)


def is_experiment_thread_id(thread_id: Optional[str]) -> bool:
    """Return True when *thread_id* matches the experiment format.

    ``{sample_id}:{system_version}:{arm}:{replicate}:{uuid}`` — exactly 5
    colon-separated segments (so the first four segments must not contain
    ``:``), every segment non-empty, and the last segment a hex string
    (uuid4().hex is 32 chars; a shorter hex suffix is still accepted to stay
    permissive).
    """
    if not thread_id or not isinstance(thread_id, str):
        return False
    parts = thread_id.split(":")
    if len(parts) != EXPERIMENT_THREAD_ID_PARTS:
        return False
    if any(part == "" for part in parts):
        return False
    *_, uuid_part = parts
    return bool(re.fullmatch(r"[0-9a-fA-F]+", uuid_part))


def new_experiment_checkpointer() -> MemorySaver:
    """Return a fresh in-memory checkpointer for a single experiment run.

    Never share this instance across runs — that would re-introduce the
    cross-run checkpoint crosstalk this module exists to prevent.
    """
    return MemorySaver()


def checkpointer_identity(checkpointer: Any) -> str:
    """Stable per-instance identifier for ``result_data.experiment_run_identity``.

    Uses ``type_name@id`` so two different ``MemorySaver`` objects can be told
    apart in recorded results.  Not persisted anywhere — only written into
    experiment_run_identity for traceability.
    """
    if checkpointer is None:
        return "none"
    return f"{type(checkpointer).__name__}@{id(checkpointer):#x}"


def experiment_graph_kwargs() -> Dict[str, Any]:
    """Return ``TradingAgentsGraph(**kwargs)`` kwargs for one experiment run.

    Bundles the two required opt-ins so callers cannot forget either half:
    a dedicated checkpointer instance plus ``experiment_mode=True`` (which
    activates the explicit-thread-id guard inside ``propagate()``).
    """
    return {
        "checkpointer": new_experiment_checkpointer(),
        "experiment_mode": True,
    }


def _thread_id_diagnostics(thread_id: str) -> str:
    """Per-segment check results for ExperimentThreadIdError messages (DAV-1486).

    Returns a compact ``label=value status`` list so a rejected id shows
    exactly which segment failed and why (empty / not-hex), instead of a bare
    'does not match' that leaves the caller guessing.
    """
    labels = ("sample_id", "system_version", "arm", "replicate", "uuid")
    parts = thread_id.split(":")
    checks = []
    for i, seg in enumerate(parts):
        label = labels[i] if i < len(labels) else f"extra_seg{i + 1}"
        problems = []
        if seg == "":
            problems.append("empty")
        if i == len(labels) - 1 and seg and not re.fullmatch(
            r"[0-9a-fA-F]+", seg
        ):
            problems.append("not-hex")
        status = "ok" if not problems else f"INVALID({','.join(problems)})"
        checks.append(f"{label}={seg!r}:{status}")
    return ", ".join(checks)


def assert_experiment_thread_id(thread_id: Optional[str]) -> str:
    """Validate *thread_id* for an experiment-mode run; return it unchanged.

    Raises :class:`ExperimentThreadIdError` when the id is missing or does not
    match the experiment format — this is the fail-closed guard that keeps an
    experiment run from silently reusing the production default thread name
    ``"{company}_{trade_date}_{horizon}"`` and contaminating a shared
    checkpoint store.  The error message reports the actual segment count and
    per-segment validation results so a malformed id is locatable at a glance
    (DAV-1486).
    """
    if not thread_id:
        raise ExperimentThreadIdError(
            "experiment-mode propagate() requires an explicit thread_id "
            "of the form '{sample_id}:{system_version}:{arm}:{replicate}:{uuid}'; "
            "the production default thread name is forbidden here"
        )
    if not is_experiment_thread_id(thread_id):
        parts = thread_id.split(":")
        raise ExperimentThreadIdError(
            f"experiment-mode thread_id {thread_id!r} does not match "
            "'{sample_id}:{system_version}:{arm}:{replicate}:{uuid}' "
            f"(segments={len(parts)}, expected {EXPERIMENT_THREAD_ID_PARTS}; "
            f"{_thread_id_diagnostics(thread_id)})"
        )
    return thread_id
