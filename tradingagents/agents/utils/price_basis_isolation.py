"""DAV-1200 (1142-B3): Legacy Price-Basis Isolation — versioned manifest clean-cohort enforcement.

不改写历史 ReportDB（不动 analysis_status / price_basis_version），用 versioned
manifest 在 H1b / bias / calibration clean-cohort 过滤路径上做确定性排除。

Manifest 双层结构（price_basis_isolation_manifest_v1.json）：

1. ``history_confirmed_v1`` — DAV-1196 全量 86 份 confirmed contamination，
   用于历史审计/回溯，不只是正式池交集。
2. ``formal_pool_v1`` — DAV-1197 正式 51 池 crosswalk：
   - ``confirmed_contaminated_v1`` (15)：硬排除；
   - ``pending_review_v1`` (18)：fail-close 临时排除 clean cohort，绝不宣判 confirmed；
   - ``clean_not_in_scope_v1`` (18)：对照层。

Pending 诚实语义：DAV-1196 宽松上界 259 无法用当前脚本精确复现（最接近可复现
口径为 208）；pending_review_v1 取「正式池含任意 raw40 披露引用 − confirmed」
保守超集，目的是 fail-close 防漏，不表示已证明污染。单条复核可在下游卡中把
pending→confirmed 或 pending→clean，但每次变更必须带 evidence_ref 与
adjudication reason，禁止批量猜测。

排除 reason（独立于 D-009 §5 的 abstain/wait/no_trade 语义）：

- ``price_basis_contaminated``：report_id ∈ confirmed（86 全量层 ∪ 正式池 15 层；
  15 为 86 子集）。
- ``price_basis_pending_review``：report_id ∈ pending_review_v1。
- ``price_basis_contract_incomplete``：新 contract 样本（带
  ``price_ref_contract_version`` 标记）缺 ``price_basis_version=price_basis.vendor_qfq``
  或 ``price_ref_contract_version=price_ref.v1`` 任一项 → fail-close，
  不得混入 clean denominator。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

MANIFEST_VERSION: str = "price_basis_isolation.v1"
MANIFEST_FILENAME: str = "price_basis_isolation_manifest_v1.json"

# ── Exclusion reason codes (kept strictly separate from D-009 §5 categories) ──
REASON_CONTAMINATED: str = "price_basis_contaminated"
REASON_PENDING_REVIEW: str = "price_basis_pending_review"
REASON_CONTRACT_INCOMPLETE: str = "price_basis_contract_incomplete"

PRICE_BASIS_VERSION_REQUIRED: str = "price_basis.vendor_qfq"
PRICE_REF_CONTRACT_REQUIRED: str = "price_ref.v1"

_ANCHOR_REPORT_ID: str = "b188060fa75045bd9e52d1eacd265372"


def _default_manifest_path() -> Path:
    return Path(__file__).resolve().parent / MANIFEST_FILENAME


@lru_cache(maxsize=8)
def _load_manifest_cached(path_str: str, mtime_ns: int) -> dict[str, Any]:
    with open(path_str, "r", encoding="utf-8") as f:
        return json.load(f)


def load_manifest(path: Optional[Path] = None) -> dict[str, Any]:
    """Load the versioned price-basis isolation manifest (fail-close on malformed data).

    Raises ValueError if the manifest is missing/invalid or fails internal
    consistency checks — callers must treat a broken manifest as a hard error,
    never silently skip isolation.
    """
    p = Path(path) if path is not None else _default_manifest_path()
    try:
        stat = p.stat()
    except OSError as exc:
        raise ValueError(f"price-basis isolation manifest not found: {p}") from exc
    try:
        manifest = _load_manifest_cached(str(p), stat.st_mtime_ns)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"price-basis isolation manifest unreadable: {p}: {exc}") from exc
    _validate_manifest(manifest)
    return manifest


def _iter_entries(manifest: Mapping[str, Any]):
    for e in manifest.get("history_confirmed_v1") or []:
        yield e
    pool = manifest.get("formal_pool_v1") or {}
    for e in pool.get("pending_review_v1") or []:
        yield e
    for e in pool.get("clean_not_in_scope_v1") or []:
        yield e


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise ValueError("price-basis isolation manifest is not a mapping")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(
            f"unsupported manifest_version: {manifest.get('manifest_version')!r} "
            f"(expected {MANIFEST_VERSION!r})"
        )
    hist = manifest.get("history_confirmed_v1") or []
    pool = manifest.get("formal_pool_v1") or {}
    conf = pool.get("confirmed_contaminated_v1") or []
    pend = pool.get("pending_review_v1") or []
    clean = pool.get("clean_not_in_scope_v1") or []

    conf_ids = {e.get("report_id") for e in conf}
    hist_ids = {e.get("report_id") for e in hist}
    pend_ids = {e.get("report_id") for e in pend}
    clean_ids = {e.get("report_id") for e in clean}

    if not conf_ids or not pend_ids or not clean_ids:
        raise ValueError("formal_pool_v1 layers must be non-empty")
    if not conf_ids <= hist_ids:
        raise ValueError("confirmed_contaminated_v1 must be a subset of history_confirmed_v1")
    if _ANCHOR_REPORT_ID not in conf_ids:
        raise ValueError(f"anchor report {_ANCHOR_REPORT_ID} missing from confirmed_contaminated_v1")
    if conf_ids & pend_ids or conf_ids & clean_ids or pend_ids & clean_ids:
        raise ValueError("formal_pool_v1 layers must be disjoint")
    if pend_ids & hist_ids:
        raise ValueError("pending_review_v1 must not overlap history_confirmed_v1")


class _ManifestIndex:
    """Lazy lookup index over the manifest."""

    def __init__(self, manifest: Mapping[str, Any]):
        self.manifest = manifest
        self.confirmed_ids: set[str] = set()
        self.pending_ids: set[str] = set()
        self.entries: dict[str, Mapping[str, Any]] = {}
        for e in manifest.get("history_confirmed_v1") or []:
            rid = e.get("report_id")
            if rid:
                self.confirmed_ids.add(str(rid))
                self.entries[str(rid)] = e
        pool = manifest.get("formal_pool_v1") or {}
        for e in pool.get("confirmed_contaminated_v1") or []:
            rid = e.get("report_id")
            if rid:
                self.confirmed_ids.add(str(rid))
                self.entries[str(rid)] = e
        for e in pool.get("pending_review_v1") or []:
            rid = e.get("report_id")
            if rid:
                self.pending_ids.add(str(rid))
                self.entries[str(rid)] = e
        for e in pool.get("clean_not_in_scope_v1") or []:
            rid = e.get("report_id")
            if rid:
                self.entries.setdefault(str(rid), e)


@lru_cache(maxsize=8)
def _index_for(path_str: str, mtime_ns: int) -> _ManifestIndex:
    return _ManifestIndex(load_manifest(Path(path_str)))


def get_index(path: Optional[Path] = None) -> _ManifestIndex:
    p = Path(path) if path is not None else _default_manifest_path()
    stat = p.stat()
    return _index_for(str(p), stat.st_mtime_ns)


def extract_report_id(report: Mapping[str, Any]) -> Optional[str]:
    """Extract report_id from canonical locations (top-level `id`/`report_id` or result_data)."""
    if not isinstance(report, Mapping):
        return None
    res_data = report.get("result_data") if isinstance(report.get("result_data"), Mapping) else {}
    for src in (report, res_data):
        for key in ("id", "report_id"):
            val = src.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
    return None


def _find_field(report: Mapping[str, Any], key: str) -> Optional[str]:
    if not isinstance(report, Mapping):
        return None
    res_data = report.get("result_data") if isinstance(report.get("result_data"), Mapping) else {}
    inv_state = report.get("investment_debate_state") if isinstance(report.get("investment_debate_state"), Mapping) else (
        res_data.get("investment_debate_state") if isinstance(res_data.get("investment_debate_state"), Mapping) else {}
    )
    meta = report.get("metadata") if isinstance(report.get("metadata"), Mapping) else (
        res_data.get("metadata") if isinstance(res_data.get("metadata"), Mapping) else {}
    )
    for src in (report, res_data, inv_state, meta):
        val = src.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


def manifest_entry(report_id: Optional[str], *, path: Optional[Path] = None) -> Optional[Mapping[str, Any]]:
    """Return the manifest entry for a report_id, or None if not adjudicated."""
    if not report_id:
        return None
    return get_index(path).entries.get(str(report_id))


def classify_price_basis_exclusion(
    report: Mapping[str, Any],
    *,
    path: Optional[Path] = None,
) -> Optional[str]:
    """Classify a report under the price-basis isolation stage.

    Returns one of REASON_CONTAMINATED / REASON_PENDING_REVIEW /
    REASON_CONTRACT_INCOMPLETE, or None when the report may enter the clean cohort.

    Order matters: manifest adjudication first (confirmed > pending), then the
    new-contract fail-close check for samples stamped with
    ``price_ref_contract_version`` (post-DAV-1199 contract). Legacy samples
    without the contract marker are adjudicated by the manifest only — absence
    from the manifest leaves them eligible (DAV-1197 口径之外不加隐性排除).
    """
    idx = get_index(path)
    rid = extract_report_id(report)
    if rid:
        if rid in idx.confirmed_ids:
            return REASON_CONTAMINATED
        if rid in idx.pending_ids:
            return REASON_PENDING_REVIEW

    # New-contract fail-close: a sample carrying the price_ref contract marker
    # must satisfy BOTH price_ref_contract_version == price_ref.v1 AND
    # price_basis_version == price_basis.vendor_qfq to enter the clean cohort.
    prcv = _find_field(report, "price_ref_contract_version")
    if prcv is not None:
        pbv = _find_field(report, "price_basis_version")
        if prcv != PRICE_REF_CONTRACT_REQUIRED or pbv != PRICE_BASIS_VERSION_REQUIRED:
            return REASON_CONTRACT_INCOMPLETE

    return None
