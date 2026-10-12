"""DAV-1953 — b2.v1 代码基率读取器（``p_base_rel_t40`` 的 PIT 查表层）.

The contract (``m1-output-contract-spec.md`` §2.2) writes a code-side
industry base rate alongside the model probability, **never shown to the
model**. The production lookup reads the precomputed PIT base-rate table
emitted by ``scripts/phase2/m1_base_rate.py``
(``p_base_rel_t40.parquet``: one row per ``(signal_date, l1, W)`` cell,
columns ``p_m{m}`` 0–1 shrunken rates + ``n`` matured counts).

Point-in-time discipline (controller ruling #3) is enforced upstream in
``b2_contract.build_base_rate_payload`` — this provider only answers
"what was knowable at the maturity cutoff", i.e. a lookup keyed by the
cutoff *date*: the table's per-signal-day PIT row at ``signal_date =
cutoff`` already contains exactly the labels with
``actual_exit_date <= cutoff`` (expanding window, W=0).

Configuration:

* ``TA_M1_BASE_RATE_PARQUET`` — path to ``p_base_rel_t40.parquet``;
  unset/unreadable → ``source_unavailable`` (fail-close, never 50, never
  a market-mean substitute);
* ``TA_M1_BASE_RATE_DISABLE=1`` — force ``source_unavailable`` (test /
  emergency kill switch).

The parquet is loaded lazily on first lookup and cached in-process;
``history_sha256`` fingerprints the exact file consumed so the sealed
record can prove which history produced the rate.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

_ENV_PATH = "TA_M1_BASE_RATE_PARQUET"
_ENV_DISABLE = "TA_M1_BASE_RATE_DISABLE"
_DEFAULT_PATH = Path("data/phase2/m1_base_rate/p_base_rel_t40.parquet")

_cache: dict[str, Any] = {"path": None, "table": None, "sha256": None,
                        "loaded": False}


def _resolve_path() -> Optional[Path]:
    raw = os.environ.get(_ENV_PATH, "").strip()
    p = Path(raw) if raw else _DEFAULT_PATH
    return p if p.exists() else None


def _load() -> None:
    """Populate the in-process table cache once (idempotent)."""
    if _cache["loaded"]:
        return
    _cache["loaded"] = True
    path = _resolve_path()
    if path is None:
        return
    try:
        import pandas as pd

        df = pd.read_parquet(
            path,
            columns=["signal_date", "l1", "W", "n", "p_m200"],
        )
        df = df[df["W"] == 0]
        table: dict[tuple[str, str], dict[str, Any]] = {}
        for row in df.itertuples(index=False):
            key = (str(row.signal_date), str(row.l1))
            table[key] = {"p_m200": float(row.p_m200)
                          if row.p_m200 == row.p_m200 else None,
                          "n": int(row.n) if row.n == row.n else None}
        _cache["table"] = table
        _cache["path"] = str(path)
        _cache["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception as exc:  # noqa: BLE001 — any read failure → unavailable
        logger.warning("[b2_base_rate_provider] load failed: %s", exc)
        _cache["table"] = None


def history_sha256() -> Optional[str]:
    _load()
    return _cache["sha256"]


def default_pit_lookup() -> Optional[Callable[[date, str], Mapping[str, Any]]]:
    """Return the default PIT lookup, or ``None`` when no source exists.

    ``None`` makes ``build_base_rate_payload`` answer
    ``source_unavailable`` — the explicit-status path, never a default.
    """
    if os.environ.get(_ENV_DISABLE, "").strip() in {"1", "true", "yes"}:
        return None
    _load()
    table = _cache["table"]
    if table is None:
        return None

    def _lookup(cutoff: date, industry_code: str) -> Optional[Mapping[str, Any]]:
        cell = table.get((cutoff.strftime("%Y%m%d"), str(industry_code)))
        if cell is None:
            return None
        return {
            "p_m200": cell.get("p_m200"),
            "n_industry": cell.get("n"),
            # 市场分母不在 m1_base_rate 输出表中，宁缺勿造（不拿行业格
            # 加总冒充；meta 允许 null）。
            "n_market": None,
        }

    return _lookup
