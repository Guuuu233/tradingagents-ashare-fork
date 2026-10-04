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

    The uuid suffix guarantees uniqueness per invocation so two replicates can
    never share a checkpoint thread even when all other fields match.
    """
    parts = (sample_id, system_version, arm, replicate, uuid.uuid4().hex)
    return ":".join(str(p) for p in parts)


def is_experiment_thread_id(thread_id: Optional[str]) -> bool:
    """Return True when *thread_id* matches the experiment format.

    ``{sample_id}:{system_version}:{arm}:{replicate}:{uuid}`` — exactly 5
    colon-separated segments, the last one a hex string (uuid4().hex is 32
    chars; a shorter hex suffix is still accepted to stay permissive).
    """
    if not thread_id or not isinstance(thread_id, str):
        return False
    parts = thread_id.split(":")
    if len(parts) != EXPERIMENT_THREAD_ID_PARTS:
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
    """Stable per-instance identifier for ``result_data.run_identity``.

    Uses ``type_name@id`` so two different ``MemorySaver`` objects can be told
    apart in recorded results.  Not persisted anywhere — only written into
    run_identity for traceability.
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


def assert_experiment_thread_id(thread_id: Optional[str]) -> str:
    """Validate *thread_id* for an experiment-mode run; return it unchanged.

    Raises :class:`ExperimentThreadIdError` when the id is missing or does not
    match the experiment format — this is the fail-closed guard that keeps an
    experiment run from silently reusing the production default thread name
    ``"{company}_{trade_date}_{horizon}"`` and contaminating a shared
    checkpoint store.
    """
    if not thread_id:
        raise ExperimentThreadIdError(
            "experiment-mode propagate() requires an explicit thread_id "
            "of the form '{sample_id}:{system_version}:{arm}:{replicate}:{uuid}'; "
            "the production default thread name is forbidden here"
        )
    if not is_experiment_thread_id(thread_id):
        raise ExperimentThreadIdError(
            f"experiment-mode thread_id {thread_id!r} does not match "
            "'{sample_id}:{system_version}:{arm}:{replicate}:{uuid}'"
        )
    return thread_id
