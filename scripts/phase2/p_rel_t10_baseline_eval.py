#!/usr/bin/env python3
"""DAV-1883 — p_rel_t10 probability-ranking baseline (M1 签收口径 + D-075 增量).

Reads the P4 forward ledger (``work/phase2-ledger/forward_ledger.jsonl``,
fixed account only) plus the DAV-1875 short-horizon expiry labels
(``work/phase2-labels/labels_t10.jsonl``) and evaluates the current
``p_rel_t10`` probability ranking under the signed-off M1 criteria:

* daily Spearman rank IC  — average-rank ties, equal-weight day summary,
  real date gaps kept, rank-degenerate days listed separately (M1 签收
  items 1, 3);
* quintile hi-lo diff     — boundary ties by §3.2 fractional weights
  (签收 item 3);
* reliability             — fixed 10 bins [1–10]…[91–99] (签收 item 7);
* Newey-West HAC          — primary band 60, robustness 120, 95% two-sided
  (签收 item 5);
* conclusion grade A–D    — C requires >=60 valid days AND both NW60/NW120
  lower bounds > 0 (签收 item 6); never auto-D;
* **D-075 increment**     — paired daily ``IC_model − IC_base`` on the same
  sample & same days, NW60/NW120 CI on the diff; the base rate is the
  shrunken SW2021-L1 industry T+10 relative-win-rate (m=200 shrinkage to
  the market mean, W=0 expanding window, DAV-1677 engine re-derived on the
  T+10 label panel) — D-075 fixes m200/W0 as the primary baseline, other
  grids are never substituted ex post.

Ledger p_rel_t10 read node (M1 签收 item 2 — 三处都要核到):
  record → ``horizons.short`` → ``forecast.p_rel_t10``
  (the slice's ``forecast`` dict is the ``parse_horizon_forecast`` output
  {contract, p_rel_t10, status, benchmark, node}; ``manager_verdict.
  forecast_input.p_rel_t10`` inside the same slice is the fallback when
  ``forecast`` is absent). Only ``status == "valid"`` ints 1–99 enter the
  formal queue — malformed/missing/node_failed count in the funnel, never
  filled.

Label leg (DAV-1875 ``labels_t10.jsonl``; DAV-2198 ①/③/④ rework):
  association = ``report_id`` + 档位 (``horizon``) — NEVER symbol+date
  (DAV-2195 §5.3: the old (symbol, signal_date) index could pick another
  report's row); every joined row must carry the same
  ``ledger_record_sha256`` as the sealed ledger record
  (``record_sha256``), else the record is excluded with reason code
  ``label_record_sha256_mismatch``;
  r = ``sw_excess_pct_prediction`` / 100   (stock − SW L1, T+1 open →
       target close, prediction basis, NO roll — the contract window of
       p_rel_t10);
  y = ``y_rel_prediction``                 (r > 0);
  l1 = ``sw.index_code``                   (PIT membership at signal T);
  matured rows only = ``prediction.status == "evaluated"`` AND r non-null —
  ``未到期`` / ``suspension`` / ``unexecutable_entry`` / typed-status rows
  carry no r and count as immature in the funnel (not zero, not dropped
  silently).
  Duplicate rows on one ``(report_id, 档位)`` key (the file is append-only):
  a typed row (``pending`` / ``data_missing`` …) superseded by a later
  ``evaluated_ok`` row is NORMAL; two ``evaluated_ok`` rows whose return
  fields disagree are a ``label_conflict`` — the report leaves the formal
  queue under that reason code and the report's first screen raises the
  alarm (never a silent pick). The label file itself is never rewritten
  (DAV-2198 ⑤) and its row-level ``input_pit_status`` is never read: 时点
  判定 = 账本 VERIFIED/FAILED 优先, UNVERIFIED 查 ``pit_reattest.jsonl``
  (DAV-2198 ③, 总控 10-15 取值顺序).

Formal cohort start (DAV-2198 ②, freeze register §2): 首个正式信号日
``2026-10-08`` is enforced HERE and read from
``docs/plans/phase2-midterm/dav2142-freeze-register-v0.2.md``; a register
that is unreadable or disagrees with the code constant fails closed rather
than silently moving the cohort start.

Dedup (签收 item 4): same (symbol, signal_date, version_key) keeps the
earliest-``created_at`` completed run — fixed ex ante, no ex-post picking.
Ledger ``version_key`` is the DAV-2142 frozen analysis-version key
``AV1|anchor=<8-char production release anchor>|contract=<forecast contract>-T10|resolved=<full sha256 of run_identity.prompt_resolved_hashes>|custom=<full sha256 of custom_prompt_snapshot | unverifiable>|served=<sorted served_model set>``
— verbatim the signed freeze register §3 ``analysis_version_id`` (DAV-2166:
key must reconcile against the register, not a home-grown format). A field
whose evidence the ledger did not seal (``custom`` on rows sealed before
DAV-2181 — ``daily_snapshot_ledger._version_fields`` did not capture
``custom_prompt_snapshot`` then; post-DAV-2181 rows seal it verbatim) is
written ``unverifiable`` instead of ``none`` so a missing hash can never
masquerade as a real one.

Custom-prompt evidence bases (DAV-2214, 总控 10-17): a ledger row whose
seal never captured ``custom_prompt_snapshot`` takes the per-report
snapshot from the shared static snapshot (``~/ta-snapshot/current.db``,
``reports.result_data.custom_prompt_snapshot`` looked up by
``report_id``, opened ``file:…?mode=ro&immutable=1`` only — never the
live DB) and hashes it under the same register §3 canonicalisation.
Every row carries a ``custom_basis`` label: ``ledger`` (the seal itself
carries the snapshot), ``report_snapshot`` (only the report carries it),
or ``unverifiable`` (neither — the register value is never substituted).
A row whose two evidences disagree is excluded with reason code
``custom_conflict`` (never a silent pick), and the run's first screen
raises the alarm. Per-row evidence is also written to
``custom_evidence.jsonl`` in the output directory.
A multi-version input fails closed unless ``--version-key`` names one
queue (D-075/DAV-1682 single-version rule — cross-version comparison
belongs to M3).

Fail-close: the trading calendar must resolve (``--calendar-file`` or the
live ``require_cn_trade_dates`` route) — unavailable → exit 2; an
unparseable ledger or label line aborts the run (never partially trusted).
Peak memory: ledger and labels are streamed line by line; the base-rate
engine is O(#matured_labels + #signal_days × #industries), no signal×label
cross-product — far under the 4 GiB budget.

Usage:
  python scripts/phase2/p_rel_t10_baseline_eval.py run \
      --ledger work/phase2-ledger/forward_ledger.jsonl \
      --labels work/phase2-labels/labels_t10.jsonl \
      [--calendar-file PATH] [--version-key KEY] [--out-dir DIR]
  python scripts/phase2/p_rel_t10_baseline_eval.py --selftest
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
# Sibling imports keep the M1 primitives + ledger helpers + base-rate
# engine single-sourced with their production modules (DAV-1480 / 1677 /
# 1678 / 1875).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl
import expiry_label_pipeline as elp
import m1_eval as m1
import pit_reattest
from m1_base_rate import build_pit_base_rates, load_panel

PIPELINE_VERSION = "p_rel_t10_baseline_eval.v1"

# DAV-2052 🟡-3: date-only sealed_at ("YYYY-MM-DD", no time part). m1_eval's
# _sealed_at_sh reads it as 00:00 UTC — admitted like any on-time seal — so
# this is a funnel DISCLOSURE count only, kept in sync with the pre-33b1f5b
# implementation's seal_dateonly_fallback key (production _fmt_dt always
# writes full timestamps, making it unreachable in production). The correct
# admit semantics for date-only stamps is an m1_eval caliber decision
# tracked on the DAV-1991 line — NOT changed here.
_DATEONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_USER_ID = dsl.DEFAULT_USER_ID
DEFAULT_LEDGER = dsl.DEFAULT_LEDGER_DIR / dsl.LEDGER_FILE
DEFAULT_LABELS = elp.DEFAULT_OUT_DIR / elp.LABEL_FILE_NAMES["short"]
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "phase2-p_rel_t10_baseline"

# DAV-2198: 档位 of the label file this script consumes (``horizon`` field).
SHORT_HORIZON = "short"

# DAV-2198 ② / DAV-2142 §2.2 (frozen preregistration): 每日批正式起点.
# 取值来自冻结登记 §2「首个正式信号日」— the cross-check below fails closed
# when the register is unreadable or disagrees, so the cohort start can
# never drift silently (a re-signed register forces a reviewed code change).
FORMAL_COHORT_START = "2026-10-08"
_FORMAL_START_RE = re.compile(
    r"首个正式信号日\s*[：:]\s*\*{0,2}\s*(\d{4}-\d{2}-\d{2})")

# DAV-2198 ④ / DAV-2195 §5.1: return-carrying fields compared when one
# ``(report_id, 档位)`` carries more than one ``evaluated_ok`` row. All
# equal → append-only duplicate row, normal; any difference →
# ``label_conflict`` (no silent pick, affected report excluded).
LABEL_RETURN_FIELDS: tuple[str, ...] = (
    "sw_excess_pct_prediction",   # the `r` this pipeline consumes
    "sw_excess_pct",
    "return_pct",
    "hs300_excess_pct",
    "entry_price",
    "exit_price",
    "direction_signed_return_pct",
)

# Reason codes for a label that cannot be trusted (plan §2.2: 冲突/不一致
# 的观测停止并给出原因码).
REASON_LABEL_CONFLICT = "label_conflict"
REASON_LABEL_RECORD_SHA_MISMATCH = "label_record_sha256_mismatch"

# DAV-2214: custom-prompt evidence bases + the exclusion reason code for a
# row whose ledger seal and report snapshot disagree.
CUSTOM_BASIS_LEDGER = "ledger"
CUSTOM_BASIS_REPORT_SNAPSHOT = "report_snapshot"
CUSTOM_BASIS_UNVERIFIABLE = "unverifiable"
CUSTOM_BASIS_CONFLICT = "conflict"
REASON_CUSTOM_CONFLICT = "custom_conflict"
CUSTOM_EVIDENCE_FILE = "custom_evidence.jsonl"

# Shared static snapshot (总控 10-13 令 / DAV-2033): read ``current.db``
# in place through the read-only URI — never copy it, never open the live
# ``data/tradingagents.db``.
DEFAULT_SNAPSHOT_DB = Path("~").expanduser() / "ta-snapshot" / "current.db"

# D-075 primary baseline grid (fixed; other (m, W) never substituted ex
# post — they are emitted as sensitivity only).
BASE_M = 200
BASE_W = 0
SENSITIVITY_MS = (50, 200, 500)
SENSITIVITY_WS = (0, 250, 500)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _parse_d(raw: Any) -> str | None:
    return elp._parse_signal_date(raw)


# ---------------------------------------------------------------------------
# 1. ledger -> per-record p_rel_t10 frame
# ---------------------------------------------------------------------------

def _canonical_json_sha(obj: Any) -> str | None:
    """sha256 of the canonical-JSON form used by freeze register §3
    (UTF-8, sort_keys=True, ensure_ascii=False, separators=(",",":"))."""
    if obj is None:
        return None
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _record_analysis_anchor(record: Mapping[str, Any]) -> str:
    """Audit anchor for the version key = the production release SHA the
    record was sealed under (generated_by_commit_sha → run_identity.commit_sha),
    truncated to 8 chars to match the register §3 ``anchor`` field. It is an
    anchor label, NOT a split factor — an adjudicated-equivalent deploy
    (10-12 65988c79→082be047) keeps the same queue because the split
    factors (contract/resolved/custom/served) do not change."""
    v = record.get("versions") or {}
    commit = v.get("generated_by_commit_sha")
    if isinstance(commit, str) and commit:
        return commit[:8]
    ri = v.get("run_identity")
    c = ri.get("commit_sha") if isinstance(ri, dict) else None
    if isinstance(c, str) and c:
        return c[:8]
    if isinstance(ri, str) and ri:
        return ri[:8]
    return "unanchored"


def _record_contract_t10(record: Mapping[str, Any]) -> str | None:
    """Register §3 ``contract`` field = the short-horizon forecast contract
    plus the ``-T10`` horizon suffix (``b1.v1`` → ``b1.v1-T10``). Read at the
    same node the probability itself is read from —
    ``horizons.short.forecast.contract`` first, then the
    ``manager_verdict.forecast_input`` twin — so the version-key contract
    labels the actual probability contract the queue consumes, never the
    ledger's storage-level ``evidence_contract_version`` (DAV-2166: the two
    disagree on the real ledger and the register value tracks the
    probability contract). Returns ``None`` when no contract string is
    sealed — the key then carries ``unverifiable``."""
    hz = (record.get("horizons") or {}).get("short") or {}
    fc = hz.get("forecast")
    if isinstance(fc, dict):
        c = fc.get("contract")
        if isinstance(c, str) and c:
            return f"{c}-T10"
    mv = hz.get("manager_verdict") if isinstance(hz.get("manager_verdict"), dict) else {}
    fi = mv.get("forecast_input") if isinstance(mv.get("forecast_input"), dict) else {}
    c = (fi.get("contract") or fi.get("probability_contract_version"))
    if isinstance(c, str) and c:
        return f"{c}-T10"
    return None


def _record_prompt_resolved_sha(record: Mapping[str, Any]) -> str | None:
    """Prompt-equivalence evidence: full sha256 of
    ``run_identity.prompt_resolved_hashes`` under the register §3
    canonicalisation (``sort_keys=True, ensure_ascii=False,
    separators=(\",\",\":\")``). Returns None when the record sealed no
    ``run_identity`` dict — ``unverifiable`` in the key, never ``none``."""
    v = record.get("versions") or {}
    ri = v.get("run_identity")
    if isinstance(ri, dict):
        return _canonical_json_sha(ri.get("prompt_resolved_hashes"))
    return None


def _record_custom_prompt_sha(record: Mapping[str, Any]) -> str | None:
    """Prompt-equivalence evidence: sha256 of ``custom_prompt_snapshot``
    under the register §3 canonicalisation. The sealed record carries the
    snapshot object at ``result_data.custom_prompt_snapshot``
    (``api/main.py:_attach_custom_prompt_snapshot``); the ledger's
    ``versions`` projection seals it verbatim (DAV-2181:
    ``versions.custom_prompt_snapshot``), so rows sealed after that
    projection change produce the real hash; pre-DAV-2181 rows keep
    returning None → ``unverifiable`` in the key. The top-level record
    fallback covers hand-shaped ledger lines.

    DAV-2214: this function answers "did the LEDGER seal it?". When it
    returns None the caller resolves the report-side evidence through
    :class:`ReportSnapshotReader` and stamps
    ``custom_basis=report_snapshot``; this function itself never touches
    the DB (kept pure so version-key unit tests need no fixture DB)."""
    v = record.get("versions") or {}
    if isinstance(v.get("custom_prompt_snapshot"), (dict, list)):
        return _canonical_json_sha(v["custom_prompt_snapshot"])
    cps = record.get("custom_prompt_snapshot")
    if isinstance(cps, (dict, list)):
        return _canonical_json_sha(cps)
    return None


# ---------------------------------------------------------------------------
# 1b. DAV-2214 custom-prompt evidence: ledger seal, else report snapshot
# ---------------------------------------------------------------------------

def _decode_report_payload(raw: Any) -> Any:
    """A ``reports.result_data`` cell → dict (or None).

    The physical cell is plaintext JSON on dual/plaintext rows and a
    zstd-9 frame on ``compressed_only`` rows (DAV-1770); the production
    codec stays single-sourced in
    ``tradingagents/storage/compressed_json.py`` so this reader never
    re-implements the storage layout."""
    if raw is None or isinstance(raw, dict):
        return raw
    from tradingagents.storage.compressed_json import decode_result_data
    return decode_result_data(raw)


class ReportSnapshotReader:
    """Per-report ``result_data.custom_prompt_snapshot`` from the shared
    static snapshot ``~/ta-snapshot/current.db`` (DAV-2214).

    Read discipline (总控 10-13 令 / DAV-2033): in-place read through
    ``file:<path>?mode=ro&immutable=1`` + ``PRAGMA query_only=ON`` — the
    live DB is never opened and the snapshot is never copied. The
    connection is lazy, one sha per ``report_id`` is cached, and a
    snapshot that is absent or unreadable degrades to
    ``(None, "unavailable")`` instead of guessing — the row then keeps its
    honest ``unverifiable`` basis.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).expanduser()
        self._con: sqlite3.Connection | None = None
        self._cache: dict[str, tuple[str | None, str]] = {}
        self.unavailable_reason: str | None = None
        self.lookups = 0
        self.status_counts: dict[str, int] = {}
        if not self.db_path.exists():
            self.unavailable_reason = f"snapshot db not found: {self.db_path}"
            log("!! report-snapshot custom evidence unavailable — "
                + self.unavailable_reason)

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None

    def __enter__(self) -> "ReportSnapshotReader":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def _connect(self) -> sqlite3.Connection:
        if self._con is None:
            self._con = sqlite3.connect(
                f"file:{self.db_path}?mode=ro&immutable=1",
                uri=True, timeout=30)
            self._con.execute("PRAGMA query_only=ON")
        return self._con

    def custom_snapshot_sha256(self, report_id: Any) -> tuple[str | None, str]:
        """``(canonical sha256, status)`` for one report id.

        status: ``ok`` | ``missing_report`` | ``missing_field`` |
        ``unreadable`` (= row/lookup failure, treated as no evidence).
        """
        if report_id in (None, ""):
            return None, "missing_report"
        if not self.available:
            return None, "unavailable"
        rid = str(report_id)
        if rid in self._cache:
            return self._cache[rid]
        self.lookups += 1
        out = self._lookup(rid)
        self.status_counts[out[1]] = self.status_counts.get(out[1], 0) + 1
        self._cache[rid] = out
        return out

    def _lookup(self, rid: str) -> tuple[str | None, str]:
        try:
            con = self._connect()
            # Plaintext-first operand order (the ORM's plaintext mode), and
            # the select expression comes from the single-sourced decode
            # layer so this reader never hard-codes the storage layout
            # (DAV-1904 B-6e raw-SQL gate).
            from tradingagents.storage.compressed_json import (
                result_data_select_expr_plaintext)
            expr = result_data_select_expr_plaintext(con)
            row = con.execute(
                f"SELECT {expr} FROM reports WHERE id = ?",
                (rid,)).fetchone()
        except sqlite3.Error as exc:
            self.unavailable_reason = f"snapshot db unreadable: {exc}"
            log("!! report-snapshot custom evidence unavailable — "
                + self.unavailable_reason)
            return None, "unreadable"
        if row is None or row[0] is None:
            return None, "missing_report"
        try:
            data = _decode_report_payload(row[0])
        except Exception as exc:  # zstd/json corruption — never guessed
            log(f"!! report {rid}: result_data undecodable ({exc}); "
                "custom evidence stays unverifiable")
            return None, "unreadable"
        if not isinstance(data, Mapping):
            return None, "missing_field"
        snap = data.get("custom_prompt_snapshot")
        if not isinstance(snap, (dict, list)):
            return None, "missing_field"
        return _canonical_json_sha(snap), "ok"


def resolve_custom_evidence(record: Mapping[str, Any],
                            reader: ReportSnapshotReader | None,
                            ) -> dict[str, Any]:
    """DAV-2214 rule engine: one ledger record → its custom evidence.

    * ledger seal carries the snapshot and no report evidence is read →
      ``custom_basis=ledger``;
    * only the report snapshot carries it →
      ``custom_basis=report_snapshot`` (register §3 canonicalisation);
    * both carry it and the hashes agree → ``ledger`` (the seal wins);
    * both carry it and they disagree → NO basis is silently picked:
      ``custom_basis=conflict`` + reason code ``custom_conflict`` — the
      row leaves the formal queue;
    * neither carries it → ``unverifiable``; the register's value is
      never substituted for an evidence the pipeline did not observe.

    The returned ``sha`` is what :func:`_record_version_key` must stamp on
    the row's key (never the register value).
    """
    ledger_sha = _record_custom_prompt_sha(record)
    report_sha: str | None = None
    lookup = "not_read"
    if reader is not None:
        report_sha, lookup = reader.custom_snapshot_sha256(
            record.get("report_id"))
    out = {
        "ledger_sha256": ledger_sha,
        "report_sha256": report_sha,
        "report_lookup": lookup,
        "sha256": None,
        "basis": CUSTOM_BASIS_UNVERIFIABLE,
        "reason": None,
        "reason_detail": None,
    }
    if ledger_sha and report_sha:
        if ledger_sha == report_sha:
            out["sha256"] = ledger_sha
            out["basis"] = CUSTOM_BASIS_LEDGER
        else:
            out["basis"] = CUSTOM_BASIS_CONFLICT
            out["reason"] = REASON_CUSTOM_CONFLICT
            out["reason_detail"] = (
                f"ledger custom_prompt_snapshot={ledger_sha} != report "
                f"result_data.custom_prompt_snapshot={report_sha}")
    elif ledger_sha:
        out["sha256"] = ledger_sha
        out["basis"] = CUSTOM_BASIS_LEDGER
    elif report_sha:
        out["sha256"] = report_sha
        out["basis"] = CUSTOM_BASIS_REPORT_SNAPSHOT
    return out


def _record_served_set(record: Mapping[str, Any]) -> tuple[list[str], int]:
    """Flatten ``versions.served_models.by_role`` into the deduped served
    set + the count of calls whose response carried no model (DAV-2142
    §2.3: llm_call_logs.served_model is the version-key field; a report
    with >1 non-empty served_model is multi-model and gets flagged)."""
    v = record.get("versions") or {}
    sm = v.get("served_models")
    if not isinstance(sm, dict):
        return [], 0
    by_role = sm.get("by_role")
    models: list[str] = []
    if isinstance(by_role, dict):
        for vals in by_role.values():
            if isinstance(vals, str):
                vals = [vals]
            for x in vals or []:
                if isinstance(x, str) and x and x not in models:
                    models.append(x)
    missing = sm.get("served_model_missing")
    try:
        missing_n = int(missing or 0)
    except (TypeError, ValueError):
        missing_n = 0
    return sorted(models), missing_n


def _record_version_key(record: Mapping[str, Any],
                        anchor: str | None = None,
                        custom_sha: str | None = None) -> str:
    """DAV-2142 frozen analysis-version key, verbatim register §3
    ``analysis_version_id`` (DAV-2166: the script's key must reconcile
    against the register, not a home-grown format).

    ``AV1|anchor=<8>|contract=<forecast contract>-T10|resolved=<64>|custom=<64|unverifiable>|served=<set>``

    Split factors: the probability contract + resolved-prompt hash +
    custom-prompt hash + served_model set — the production release commit
    is only an audit anchor, never a per-deploy split key. ``anchor`` is
    the frozen production-release anchor (``--analysis-anchor``); when
    supplied it is stamped on every record so the adjudicated-equivalent
    10-12 deploy (65988c79→082be047) keeps ONE key before and after.
    Without it each record carries its own sealed commit and two genuinely
    different deploys split — fail-closed, never silently merged.

    A field whose evidence the ledger never sealed is written
    ``unverifiable`` (not ``none``): pre-DAV-2181 ledger rows do not
    project ``custom_prompt_snapshot`` and a bare ``none`` would falsely
    imply a real empty hash. Rows sealed after DAV-2181 carry the real
    snapshot hash, so a custom-prompt change switches the key via
    resolved/custom even under a fixed anchor.

    ``custom_sha`` (DAV-2214) overrides the custom leg with the resolved
    evidence — the ledger's own seal when it has one, else the report
    snapshot's canonical hash. Only the hash is injected; the resolution
    rule and its ``custom_basis`` label live in
    :func:`resolve_custom_evidence`, so this function stays pure."""
    anchor = anchor or _record_analysis_anchor(record)
    contract = _record_contract_t10(record) or "unverifiable"
    resolved = _record_prompt_resolved_sha(record) or "unverifiable"
    custom = (custom_sha or _record_custom_prompt_sha(record)
              or "unverifiable")
    served, _missing = _record_served_set(record)
    served_s = "+".join(served) if served else "none"
    return (f"AV1|anchor={anchor}|contract={contract}"
            f"|resolved={resolved}"
            f"|custom={custom}"
            f"|served={served_s}")


def _key_register_diff(key: str) -> dict:
    """Per-segment diff of a script version key vs the register §3
    ``analysis_version_id`` — ``{segment: {script, register}}`` for every
    segment that disagrees. Empty dict = verbatim match. Surfaces the
    honest `custom=unverifiable` divergence (ledger never seals the
    snapshot) without string-diffing two 300-char keys by eye."""

    def _seg(k: str) -> dict:
        out: dict[str, str] = {}
        parts = k.split("|")
        out["prefix"] = parts[0]
        for p in parts[1:]:
            name, _, val = p.partition("=")
            out[name] = val
        return out

    a = _seg(key)
    b = _seg(REGISTER_ANALYSIS_VERSION_ID)
    diff: dict[str, dict] = {}
    for name in sorted(set(a) | set(b)):
        if a.get(name) != b.get(name):
            diff[name] = {"script": a.get(name), "register": b.get(name)}
    return diff


def _p_rel_t10(record: Mapping[str, Any]) -> tuple[int | None, str]:
    """Read node: horizons.short.forecast.p_rel_t10 (M1 签收 item 2).

    Returns (p_int, status) where status mirrors parse_horizon_forecast:
    "valid" (int 1–99) / "missing" / "malformed" / "node_failed".
    """
    hz = (record.get("horizons") or {}).get("short") or {}
    fc = hz.get("forecast")
    if isinstance(fc, dict):
        status = str(fc.get("status") or "")
        p = fc.get("p_rel_t10")
        # Enforce the same 1–99 contract as parse_horizon_forecast — never
        # trust a "valid" status blindly: a 0/100/60.5/"60"/True must be
        # re-typed malformed and funnel out, not coerced into the queue.
        if status == "valid":
            if (isinstance(p, bool)
                    or not isinstance(p, (int, float))
                    or not 1 <= p <= 99
                    or (isinstance(p, float) and not p.is_integer())):
                return None, "malformed"
            return int(p), "valid"
        return None, status or "missing"
    # Fallback: the same slice's manager_verdict.forecast_input (the read
    # node's upstream twin, in case an older ledger line sealed before the
    # `forecast` dict was persisted into horizons.short).
    mv = hz.get("manager_verdict") if isinstance(hz.get("manager_verdict"), dict) else {}
    fi = mv.get("forecast_input") if isinstance(mv.get("forecast_input"), dict) else {}
    if fi.get("node_ran") is not True:
        return None, "node_failed"
    p = fi.get("p_rel_t10")
    if p is None:
        return None, "missing"
    if isinstance(p, bool) or not isinstance(p, (int, float)) \
            or not 1 <= p <= 99:
        return None, "malformed"
    if isinstance(p, float) and not p.is_integer():
        return None, "malformed"
    return int(p), "valid"


def load_ledger_signals(
    ledger_path: Path,
    labels: Any,
    user_id: str,
    reattest_index: Optional[Mapping[str, dict]] = None,
    analysis_anchor: str | None = None,
    snapshot: ReportSnapshotReader | None = None,
) -> pd.DataFrame:
    """Stream the ledger; one signal row per P4-account ledger line.

    ``labels`` is a :class:`LabelIndex` (or a plain mapping of the same
    shape). Association is by ``report_id`` + 档位 and each joined row is
    checked against the ledger record's own ``record_sha256``
    (``elp._record_identity``); a conflict or a hash mismatch sets
    ``label_reason`` instead of silently using another record's row
    (DAV-2198 ①, DAV-2195 §5.3). Only short-horizon matured labels supply
    `r`; every ledger line still lands in the frame so the funnel counts
    the honest denominator (无标签 / 未到期 = excl_immature_label
    downstream).

    ``reattest_index`` (DAV-2097③, 总控取值顺序 / DAV-2198 ③): ledger
    VERIFIED/FAILED stand; UNVERIFIED consults the pit_reattest.jsonl
    index (report_id -> adjudicated verdict). The label row's own
    ``input_pit_status`` is never read — the ledger's status is kept in
    ``_ledger_input_pit_status`` for disclosure.

    ``snapshot`` (DAV-2214) supplies the report-side
    ``custom_prompt_snapshot`` for rows whose ledger seal never captured
    it; every row gets ``custom_basis`` / ``custom_sha256`` and the
    version key is stamped with the resolved hash (the ledger value wins
    whenever it exists and agrees, an absence never borrows the register
    value). A row whose two evidences disagree carries
    ``custom_reason=custom_conflict`` and is dropped by
    :func:`_apply_custom_gate` before version selection.
    """
    idx = _as_label_index(labels)
    rows: list[dict[str, Any]] = []
    for _lineno, rec in elp._iter_ledger_records(ledger_path):
        sig = _parse_d(rec.get("signal_date") or rec.get("trade_date"))
        sym = rec.get("symbol")
        p, p_status = _p_rel_t10(rec)
        entry = None
        rid = rec.get("report_id")
        if idx and rid is not None:
            entry = idx.get((str(rid), SHORT_HORIZON))
        lab = None
        label_reason = None
        label_reason_detail = None
        label_l1 = ""
        if entry is not None:
            if entry.status == "conflict":
                label_reason = REASON_LABEL_CONFLICT
                label_reason_detail = (
                    "two evaluated_ok rows differ on: "
                    + ",".join(entry.conflict_fields))
            else:
                lab = entry.row
                ledger_sha = elp._record_identity(rec)
                label_sha = entry.record_sha256
                if not label_sha or label_sha != ledger_sha:
                    # Cannot prove the row belongs to THIS sealed record
                    # (plan §2.2: 只凭股票+日期关联不足；不一致的排除).
                    label_reason = REASON_LABEL_RECORD_SHA_MISMATCH
                    label_reason_detail = (
                        "label ledger_record_sha256="
                        f"{label_sha or '<missing>'} != ledger "
                        f"record_sha256={ledger_sha}")
                    lab = None
        if lab is not None:
            label_l1 = ((lab.get("sw") or {}).get("index_code") or "")
        r = None
        # DAV-2025 🟡-2: `.get(key, {})` returns None (not {}) when the key
        # exists with an explicit JSON null — guard with `or {}` so a
        # `prediction: null` label row routes to excl_immature_label instead
        # of crashing the run with AttributeError.
        # DAV-2207 ③: the r-extractor and the duplicate-label classifier now
        # share one predicate — a row the gate classifies ``typed`` can no
        # longer hand its `r` to the queue behind the gate's back. The
        # pre-existing r contract (``prediction.status == "evaluated"``) is
        # kept as-is, so this only ever *withholds* a return, never adds one.
        if (lab is not None and _is_evaluated_ok(lab)
                and (lab.get("prediction") or {}).get("status") == "evaluated"):
            v = lab.get("sw_excess_pct_prediction")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                r = float(v) / 100.0
        pit = rec.get("input_pit_status")
        eff_pit = pit_reattest.resolve_effective_pit(
            pit, rec.get("report_id"), reattest_index)
        ledger_pit_u = str(pit or "").upper() or "UNVERIFIED"
        served_set, served_missing = _record_served_set(rec)
        custom = resolve_custom_evidence(rec, snapshot)
        rows.append({
            "signal_date": sig,
            "symbol": str(sym) if sym is not None else None,
            "run_id": rec.get("report_id"),
            "created_at": rec.get("created_at") or rec.get("sealed_at"),
            "sealed_at": rec.get("sealed_at") or rec.get("seal_basis_at"),
            "timing_class": rec.get("timing_class"),
            "input_pit_status": eff_pit,
            "_ledger_input_pit_status": pit,
            # DAV-2198 ③: the effective status came from the reattest sidecar
            # (ledger UNVERIFIED/non-final overridden) — disclosed, never
            # sourced from the label row.
            "pit_reattest_applied": bool(eff_pit != ledger_pit_u),
            "completed": rec.get("status") == "completed",
            "user_id": rec.get("user_id"),
            "version_key": _record_version_key(
                rec, anchor=analysis_anchor, custom_sha=custom["sha256"]),
            "analysis_anchor": _record_analysis_anchor(rec),
            # DAV-2214 custom-prompt evidence (per-row, also dumped to
            # custom_evidence.jsonl by run()).
            "custom_basis": custom["basis"],
            "custom_sha256": custom["sha256"],
            "custom_ledger_sha256": custom["ledger_sha256"],
            "custom_report_sha256": custom["report_sha256"],
            "custom_report_lookup": custom["report_lookup"],
            "custom_reason": custom["reason"],
            "custom_reason_detail": custom["reason_detail"],
            "served_models": served_set,
            "served_model_missing": served_missing,
            "multi_model": len(served_set) > 1,
            "p_rel_t10": p,
            "_p_status": p_status,
            "r": r,
            # DAV-2198: label association evidence (never the row's own
            # input_pit_status — 时点 only from the ledger + reattest).
            "label_key_present": entry is not None,
            "label_reason": label_reason,
            "label_reason_detail": label_reason_detail,
            "label_l1": label_l1,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["signal_date"] = (
            pd.to_datetime(df["signal_date"]).dt.strftime("%Y%m%d"))
        # canonical names m1_eval.admit() expects: `prob` -> q, `r` already set
        df["prob"] = df["p_rel_t10"]
    return df


# ---------------------------------------------------------------------------
# 2b. seal-time gate —— 统一走 m1_eval 的 DAV-1991 实现（总控 10-14 令）。
# 本文件不再自养解析/判门：m1.admit(calendar=...) 内置 sealed_at < 信号日
# 次一交易日 09:30 Asia/Shanghai 的封存时点门，时区口径与解析细节以它为准。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 2. labels -> (report_id, 档位) index + T+10 base-rate panel
# ---------------------------------------------------------------------------

# DAV-2207 🟡-1(a) (复审 DAV-2202 §三): the r-extractor reads a row through
# ``prediction.status == "evaluated"`` while the duplicate-label classifier
# only knew ``evaluated_ok``, so a return-carrying legacy row fell to
# ``typed`` and bypassed the conflict gate (silent pick of a row). Both
# spellings mean "the return fields count" — accept either; a non-empty
# ``outcome_status`` still wins (DAV-2198 ③ vocabulary unchanged).
_EVALUATED_STATUS = frozenset({"evaluated_ok", "evaluated"})


def _label_value_class(v: Any) -> tuple:
    """Comparison token for ONE return field (DAV-2207 🟡-1(b)).

    Values compare by class: ``null`` equals only ``null`` (an absent field
    must never be turned into 0), numbers compare numerically (NaN is the
    same "no value" as NaN), every other payload — bool, string, list —
    keeps its own identity. The previous "non-numeric → None" mapping made
    ``"1.0"`` and ``"9.9"`` compare equal, so two rows that genuinely
    disagree were waved through as an append-only duplicate.
    """
    if v is None:
        return ("null",)
    if isinstance(v, bool):
        return ("bool", v)
    if isinstance(v, (int, float)):
        f = float(v)
        return ("nan",) if math.isnan(f) else ("num", f)
    return ("raw", json.dumps(v, sort_keys=True, default=str))


def _label_status(row: Mapping[str, Any]) -> str:
    """Row-level label status: ``outcome_status`` (the 7-state
    OutcomeStatus vocabulary ``expiry_label_pipeline`` writes), falling
    back to the ``prediction`` sub-status for hand-shaped rows."""
    st = str(row.get("outcome_status") or "").strip()
    if st:
        return st
    pred = row.get("prediction") or {}
    return str(pred.get("status") or "").strip()


def _is_evaluated_ok(row: Mapping[str, Any]) -> bool:
    return _label_status(row) in _EVALUATED_STATUS


def _label_return_signature(row: Mapping[str, Any]) -> tuple:
    return tuple(_label_value_class(row.get(f)) for f in LABEL_RETURN_FIELDS)


@dataclass(frozen=True)
class LabelEntry:
    """Every append-only row for ONE ``(report_id, 档位)`` key, resolved.

    ``row`` is the canonical row the eval consumes (``None`` on conflict —
    a conflicted key has no canonical row by construction). Counters feed
    the first-screen integrity block; nothing here is ever written back.
    """
    report_id: str
    horizon: str
    row: Optional[Mapping[str, Any]]
    status: str                      # evaluated_ok | typed | conflict
    n_rows: int
    statuses: tuple[str, ...]
    superseded_typed: int            # typed rows superseded by evaluated_ok
    typed_after_evaluated: int       # …appearing AFTER it (anomaly disclosure)
    duplicate_identical: int         # extra evaluated_ok rows, values equal
    conflict_fields: tuple[str, ...]
    record_sha256: Optional[str]     # the label row's ledger_record_sha256


class LabelIndex:
    """``{(report_id, 档位): LabelEntry}`` + append-only integrity evidence.

    Association is by report_id + 档位 (DAV-2198 ① / DAV-2195 §5.3);
    ``(symbol, signal_date)`` is never a join key. ``.integrity`` carries
    the duplicate/conflict counters the report's first screen discloses.
    """

    def __init__(self, entries: Mapping[tuple, LabelEntry],
                 integrity: Mapping[str, Any]):
        self.entries = dict(entries)
        self.integrity = dict(integrity)

    def get(self, key: tuple, default: Any = None) -> Any:
        return self.entries.get(key, default)

    def __getitem__(self, key: tuple) -> LabelEntry:
        return self.entries[key]

    def __contains__(self, key: tuple) -> bool:
        return key in self.entries

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def build_label_index(rows) -> LabelIndex:
    """Resolve append-only label rows into one entry per (report_id, 档位).

    DAV-2198 ④ rules (DAV-2195 §5.1/§5.2 facts):

    * ``pending`` / ``data_missing`` … typed row superseded by an
      ``evaluated_ok`` row → normal; canonical = the last ``evaluated_ok``
      row (append-only file order); typed rows are counted as superseded;
      typed rows landing AFTER the canonical evaluated row are disclosed
      (``typed_after_evaluated``) but follow the same rule — the card
      does not invent an exclusion for that ordering;
    * ≥2 ``evaluated_ok`` rows whose return fields all agree → duplicate
      append-only row, normal; canonical = the LAST one;
    * ≥2 ``evaluated_ok`` rows whose return fields differ → the key is a
      ``label_conflict``: no row is silently picked, ``status="conflict"``
      and the affected report is excluded downstream.

    Rows without ``report_id`` cannot be associated (association is
    report_id + 档位, never symbol+date) and are counted, not guessed.
    """
    groups: dict[tuple[str, str], list[tuple[int, Mapping[str, Any]]]] = {}
    integrity: dict[str, Any] = {
        "n_rows": 0,
        "n_short_rows": 0,
        "n_other_horizon_rows": 0,
        "n_unkeyed_rows": 0,
        "n_groups": 0,
        "n_evaluated_groups": 0,
        "n_typed_groups": 0,
        "n_conflict_groups": 0,
        "superseded_typed_rows": 0,
        "typed_after_evaluated_rows": 0,
        "duplicate_identical_evaluated_rows": 0,
        "conflict_reports": [],
        "conflicts": [],
        "reason_codes": {},
    }
    seq = 0
    for row in rows:
        integrity["n_rows"] += 1
        if not isinstance(row, Mapping):
            raise elp.PipelineError(
                "label row is not a JSON object; refusing to guess")
        horizon = str(row.get("horizon") or "").strip()
        if horizon != SHORT_HORIZON:
            integrity["n_other_horizon_rows"] += 1
            continue
        integrity["n_short_rows"] += 1
        rid = row.get("report_id")
        rid_s = str(rid) if rid not in (None, "") else ""
        if not rid_s:
            integrity["n_unkeyed_rows"] += 1
            continue
        groups.setdefault((rid_s, horizon), []).append((seq, row))
        seq += 1

    entries: dict[tuple, LabelEntry] = {}
    for key, items in groups.items():
        ev = [(i, r) for i, r in items if _is_evaluated_ok(r)]
        typed = [(i, r) for i, r in items if not _is_evaluated_ok(r)]
        statuses = tuple(_label_status(r) or "unknown" for _i, r in items)
        conflict_fields: tuple[str, ...] = ()
        duplicate_identical = 0
        if len(ev) >= 2:
            sigs = [_label_return_signature(r) for _i, r in ev]
            # raw rows parallel to `sigs`: the comparison uses value-class
            # tokens, while the "values" disclosure below must stay the
            # literal field values a human reads (DAV-2207 🟡-1(b)).
            raws = [tuple(r.get(f) for f in LABEL_RETURN_FIELDS)
                    for _i, r in ev]
            conflict_fields = tuple(
                f for j, f in enumerate(LABEL_RETURN_FIELDS)
                if len({s[j] for s in sigs}) > 1)
            if conflict_fields:
                integrity["n_conflict_groups"] += 1
                integrity["conflict_reports"].append(key[0])
                integrity["conflicts"].append({
                    "report_id": key[0],
                    "horizon": key[1],
                    "n_rows": len(items),
                    "statuses": list(statuses),
                    "fields": list(conflict_fields),
                    "values": {
                        f: [s[j] for s in raws]
                        for j, f in enumerate(LABEL_RETURN_FIELDS)
                        if f in conflict_fields},
                    "outcome": (REASON_LABEL_CONFLICT
                                + ": report excluded from the formal queue"),
                })
                entries[key] = LabelEntry(
                    report_id=key[0], horizon=key[1], row=None,
                    status="conflict", n_rows=len(items),
                    statuses=statuses, superseded_typed=len(typed),
                    typed_after_evaluated=0, duplicate_identical=0,
                    conflict_fields=conflict_fields, record_sha256=None)
                continue
            # identical values → append-only duplicate; normal, last wins.
            duplicate_identical = len(ev) - 1
            integrity["duplicate_identical_evaluated_rows"] += \
                duplicate_identical
        canonical = ev[-1] if ev else typed[-1]
        typed_after = sum(1 for i, _r in typed if i > canonical[0]) if ev else 0
        if ev:
            integrity["superseded_typed_rows"] += len(typed)
            integrity["typed_after_evaluated_rows"] += typed_after
        sha = canonical[1].get("ledger_record_sha256")
        entries[key] = LabelEntry(
            report_id=key[0], horizon=key[1], row=canonical[1],
            status="evaluated_ok" if ev else "typed", n_rows=len(items),
            statuses=statuses, superseded_typed=len(typed) if ev else 0,
            typed_after_evaluated=typed_after,
            duplicate_identical=duplicate_identical,
            conflict_fields=conflict_fields,
            record_sha256=str(sha) if isinstance(sha, str) and sha else None)

    integrity["n_groups"] = len(entries)
    integrity["n_evaluated_groups"] = sum(
        1 for e in entries.values() if e.status == "evaluated_ok")
    integrity["n_typed_groups"] = sum(
        1 for e in entries.values() if e.status == "typed")
    if integrity["n_conflict_groups"]:
        integrity["reason_codes"] = {
            REASON_LABEL_CONFLICT: integrity["n_conflict_groups"]}
    return LabelIndex(entries, integrity)


def load_labels(path: Path) -> LabelIndex:
    """Read the append-only short-horizon label file into a LabelIndex.

    READ-ONLY (DAV-2198 ⑤): the file is never rewritten — duplicate and
    conflicting rows are resolved in memory and disclosed, never repaired
    in place. The file sha256 is recorded as first-screen evidence that
    the run did not touch it.
    """
    if not path.exists():
        raise elp.PipelineError(f"labels file not found: {path}")
    blob = Path(path).read_bytes()
    rows: list[Any] = []
    for lineno, line in enumerate(blob.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise elp.PipelineError(
                f"label file {path} line {lineno} unreadable: {exc}") from exc
    index = build_label_index(rows)
    index.integrity["labels_file"] = str(path)
    index.integrity["labels_sha256"] = hashlib.sha256(blob).hexdigest()
    return index


def _as_label_index(labels: Any) -> Mapping:
    """Accept a LabelIndex or a plain mapping (tests / partial callers)."""
    if isinstance(labels, LabelIndex):
        return labels.entries
    return labels or {}


def build_base_panel(chunks_dir: Path) -> pd.DataFrame:
    """PIT base-rate panel from the P1 all-market T+10 partitions.

    D-075/DAV-1890 fix: the comparison baseline is the WHOLE-MARKET history
    (P1 分区, matured rows only), not the P4 ledger's own ~24/day labels.
    Reuses m1_base_rate.load_panel — same column contract and the same
    matured filter (outcome_status==evaluated_ok & universe_ok & y_rel
    non-null & actual_exit_date present, i.e. actual_exit_date <= T).
    Columns: signal_date (T), mat (label maturity = actual_exit_date),
    l1 (SW index code at T), y.
    """
    panel = load_panel(Path(chunks_dir))
    # load_panel keeps signal_date as the partition's YYYYMMDD string; the
    # label map / paired join keys are ISO YYYY-MM-DD — normalise once here.
    if not panel.empty:
        sd = panel["signal_date"].astype(str).str.replace("-", "", regex=False)
        panel["signal_date"] = (sd.str[:4] + "-" + sd.str[4:6]
                                + "-" + sd.str[6:8])
        md = panel["mat"].astype(str).str.replace("-", "", regex=False)
        panel["mat"] = (md.str[:4] + "-" + md.str[4:6] + "-" + md.str[6:8])
    return panel


def _freeze_file_sha(path: Path) -> str | None:
    """sha256 of a freeze-register file under the repo (None when absent)."""
    try:
        p = Path(path)
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


# DAV-2160: freeze evidence fingerprints printed on the report's first
# screen (§6 来源表 item 1). The plan/register were signed at freeze SHA
# 58d6a2eb; the working tree copies are hashed live so a post-freeze edit
# is visible as a mismatch, not silently stamped.
FREEZE_PLAN_FILE = (REPO_ROOT / "docs/plans/phase2-midterm"
                    / "dav2142-phase1-preregistration-v0.2.md")
FREEZE_REGISTER_FILE = (REPO_ROOT / "docs/plans/phase2-midterm"
                        / "dav2142-freeze-register-v0.2.md")
FREEZE_PLAN_SHA256_SIGNED = ("2f63d4f49a5f5d6587e53b71c8ef3ffab34da59e3d"
                           "55c5c3a7eabd355e3caaef")
FREEZE_REGISTER_SHA256_SIGNED = ("7946f7d9b085709773182182d9b7a59159816638"
                               "4268d7cf52b898de8852dd76")

# Freeze register §3 signed analysis_version_id — the statutory key the
# first-screen report must reconcile against (DAV-2166 🔴-2). Written on
# every run.json so a reviewer can compare ``selected_version_key`` to the
# register value mechanically. On ledgers sealed before DAV-2181 the
# script's ``custom`` leg stays ``unverifiable`` (those rows never sealed
# the snapshot) rather than borrowing the register's ``b0ababd6…`` value,
# which would assert an evidence this pipeline did not observe; rows
# sealed after DAV-2181 carry the real hash and the leg matches verbatim.
REGISTER_ANALYSIS_VERSION_ID = (
    "AV1|anchor=082be047|contract=b1.v1-T10"
    "|resolved=a9950a9e81f5a18571cd7acb82777922583f147419f97d5499fdd5659d0b781d"
    "|custom=b0ababd6ba2245ace36191d22b0958a41cab8cff0a572b03e5bab4da2d3e33a2"
    "|served=gemini-3.8-flash-n")


def _register_formal_start(
        register_file: Path = FREEZE_REGISTER_FILE) -> str | None:
    """首个正式信号日 as written in the freeze register §2, or None.

    Read live from the register (not duplicated as the only source) so a
    re-signed register that moves the cohort start is visible to the code:
    :func:`_formal_start_gate` then fails closed instead of silently
    running the old window.
    """
    try:
        text = Path(register_file).read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FORMAL_START_RE.search(text)
    return m.group(1) if m else None


def _formal_start_gate(
        register_file: Path = FREEZE_REGISTER_FILE) -> dict:
    """DAV-2198 ②: enforce the formal cohort start, sourced from the
    freeze register §2 (首个正式信号日).

    Fail-close (never a silently moved window):
    * the register is unreadable or no 首个正式信号日 line parses;
    * the register's value disagrees with :data:`FORMAL_COHORT_START`
      (a re-signed register must force an explicit, reviewed code change).
    """
    value = _register_formal_start(register_file)
    if value is None:
        raise elp.PipelineError(
            "formal cohort start (首个正式信号日) not readable from the "
            f"freeze register {register_file}; refusing to run without the "
            "frozen 起点 (DAV-2198 ②)")
    if value != FORMAL_COHORT_START:
        raise elp.PipelineError(
            f"freeze register 首个正式信号日={value} != code-enforced "
            f"FORMAL_COHORT_START={FORMAL_COHORT_START}; re-sign the "
            "register and update the code constant in the same review "
            "(DAV-2198 ②)")
    try:
        source = str(Path(register_file).relative_to(REPO_ROOT))
    except ValueError:
        source = str(register_file)
    return {
        "start": FORMAL_COHORT_START,
        "enforced_in_code": True,
        "rule": "signal_date >= start (DAV-2142 §2.2 每日批正式起点)",
        "source_file": source,
        "source_field": "首个正式信号日",
        "register_value": value,
        "register_sha256": _freeze_file_sha(Path(register_file)),
        "register_sha256_signed": FREEZE_REGISTER_SHA256_SIGNED,
        "match": True,
    }


def _apply_formal_start(df: pd.DataFrame,
                        block: Mapping[str, Any]) -> tuple[pd.DataFrame, dict]:
    """Drop ledger rows whose signal_date precedes the frozen cohort start.

    Only rows carrying a parseable ``YYYYMMDD`` signal date can be judged
    as pre-cohort; an unparseable/missing date is NOT counted here — it
    stays in the frame and keeps failing downstream where it already did
    (seal gate / immature label), so this gate cannot absorb a different
    failure bucket.
    """
    out = dict(block)
    out.update({"excluded_rows": 0, "excluded_min_signal_date": None,
                "excluded_max_signal_date": None})
    if df.empty or "signal_date" not in df.columns:
        return df, out
    start_ymd = str(block["start"]).replace("-", "")
    sd = df["signal_date"].astype(str)
    before = sd.str.fullmatch(r"\d{8}") & (sd < start_ymd)
    if not before.any():
        return df, out
    ex = sd[before]
    out.update({
        "excluded_rows": int(before.sum()),
        "excluded_min_signal_date": str(ex.min()),
        "excluded_max_signal_date": str(ex.max()),
    })
    return df[~before], out


def _pit_provenance(df: pd.DataFrame,
                    source: Mapping[str, Any] | None) -> dict:
    """DAV-2198 ③ first-screen evidence: where the effective input-pit
    status came from on the selected queue.

    口径: 账本 VERIFIED/FAILED 为终态；账本 UNVERIFIED 才查
    ``pit_reattest.jsonl``（仅 adjudicated 行）。标签行的
    ``input_pit_status`` 从不参与（``label_row_input_pit_status_read``）。
    """
    eff = (df["input_pit_status"].astype(str).str.upper()
           if "input_pit_status" in df.columns else pd.Series(dtype=str))
    applied = (df["pit_reattest_applied"].fillna(False).astype(bool)
               if "pit_reattest_applied" in df.columns
               else pd.Series(dtype=bool))
    return {
        "order": "账本 VERIFIED/FAILED 优先；账本 UNVERIFIED 查 "
                 "pit_reattest.jsonl（仅 adjudicated 行）",
        "label_row_input_pit_status_read": False,
        "scope": "selected version queue (post select_version, pre-cohort)",
        "queue_rows": int(len(df)),
        "overlay_applied": int(applied.sum()),
        "effective": {k: int((eff == k).sum())
                      for k in ("VERIFIED", "UNVERIFIED", "FAILED")},
        "source": dict(source) if source else None,
    }


def _apply_label_gate(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop ledger rows whose joined label could not be trusted.

    ``load_ledger_signals`` sets ``label_reason`` for ① a key that carries
    two differing ``evaluated_ok`` rows (``label_conflict``) and ② a label
    whose ``ledger_record_sha256`` does not match the sealed record
    (``label_record_sha256_mismatch``). Both leave the formal queue and
    are reported with their reason code (plan §2.2: 冲突、不一致的观测
    停止，不能悄悄选择一份).

    The record hash is the sealed record's whole-record chain hash, so it
    also anchors the symbol / signal_date / 档位 identity the plan §2.2
    requires — 只凭股票+日期关联不足 no longer arises.
    """
    block: dict[str, Any] = {
        "association_key": "report_id+horizon",
        "horizon": SHORT_HORIZON,
        "record_sha256_verified": True,
        "counts": {},
        "excluded": [],
    }
    if df.empty or "label_reason" not in df.columns:
        return df, block
    reason = df["label_reason"]
    mask = reason.notna() & (reason.astype(str).str.len() > 0)
    if "label_key_present" in df.columns:
        key_present = df["label_key_present"].fillna(False).astype(bool)
        block["rows_with_label_key"] = int(key_present.sum())
        block["rows_usable"] = int((key_present & ~mask).sum())
    if "r" in df.columns:
        block["rows_with_evaluated_label"] = int(df["r"].notna().sum())
    if not mask.any():
        return df, block
    counts: dict[str, int] = {}
    detail: list[dict[str, Any]] = []
    for _i, row in df[mask].iterrows():
        code = str(row.get("label_reason"))
        counts[code] = counts.get(code, 0) + 1
        detail.append({
            "report_id": (str(row.get("run_id"))
                          if row.get("run_id") is not None else None),
            "symbol": row.get("symbol"),
            "signal_date": row.get("signal_date"),
            "reason": code,
            "detail": row.get("label_reason_detail"),
        })
    block["counts"] = counts
    block["excluded"] = sorted(
        detail, key=lambda x: (x["reason"], str(x["report_id"])))
    return df[~mask], block


def _apply_custom_gate(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Drop ledger rows whose custom-prompt evidences disagree (DAV-2214).

    ``load_ledger_signals`` stamps ``custom_reason=custom_conflict`` when
    BOTH the ledger seal and the report snapshot carry a
    ``custom_prompt_snapshot`` and their register §3 hashes differ; such a
    row is excluded with its reason code (plan §2.2: 冲突、不一致的观测
    停止并给出原因码) — neither side is silently preferred. Rows with
    one-sided or absent evidence are NOT excluded here: they keep their
    ``custom_basis`` (ledger / report_snapshot / unverifiable) and stay in
    the funnel.

    Deliberately runs BEFORE ``m1.select_version``: a conflicting row must
    not force the multi-version fail-close on a queue it never enters
    (its two versions are exactly what is in dispute) — the exclusion is
    the disposition the card requires.
    """
    block: dict[str, Any] = {
        "association_key": "report_id",
        "input_rows": int(len(df)),
        "basis_counts": {},
        "reason_codes": {},
        "excluded": [],
    }
    if df.empty or "custom_basis" not in df.columns:
        return df, block
    block["basis_counts"] = {
        str(k): int(v) for k, v in
        df["custom_basis"].value_counts().items()}
    if "custom_reason" not in df.columns:
        return df, block
    reason = df["custom_reason"]
    mask = reason.notna() & (reason.astype(str).str.len() > 0)
    if not mask.any():
        return df, block
    counts: dict[str, int] = {}
    detail: list[dict[str, Any]] = []
    for _i, row in df[mask].iterrows():
        code = str(row.get("custom_reason"))
        counts[code] = counts.get(code, 0) + 1
        detail.append({
            "report_id": (str(row.get("run_id"))
                          if row.get("run_id") is not None else None),
            "symbol": row.get("symbol"),
            "signal_date": row.get("signal_date"),
            "reason": code,
            "detail": row.get("custom_reason_detail"),
        })
    block["reason_codes"] = counts
    block["excluded"] = sorted(
        detail, key=lambda x: (x["reason"], str(x["report_id"])))
    return df[~mask], block


def _custom_evidence_alert(block: Mapping[str, Any]) -> str | None:
    """First-screen alarm for the DAV-2214 ``custom_conflict`` exclusion."""
    counts = dict(block.get("reason_codes") or {})
    n = int(counts.get(REASON_CUSTOM_CONFLICT, 0))
    if not n:
        return None
    ids = sorted({str(x.get("report_id"))
                  for x in (block.get("excluded") or [])})
    return (f"custom_conflict: {n} ledger row(s) carry a "
            "custom_prompt_snapshot whose ledger-sealed hash disagrees "
            "with the report snapshot — excluded from the formal queue "
            f"({', '.join(ids)})")


def _custom_evidence_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """One audit line per ledger row (DAV-2214 逐行证据)."""
    cols = ["run_id", "user_id", "symbol", "signal_date", "custom_basis",
            "custom_sha256", "custom_ledger_sha256", "custom_report_sha256",
            "custom_report_lookup", "custom_reason",
            "custom_reason_detail"]
    if df.empty or "custom_basis" not in df.columns:
        return []
    work = df[[c for c in cols if c in df.columns]].copy()
    work["_rid"] = work["run_id"].map(
        lambda x: str(x) if x not in (None, "") else None)
    work["_sd"] = work["signal_date"].astype(str)
    work = work.sort_values(["_sd", "_rid"], na_position="last",
                            kind="stable")
    out: list[dict[str, Any]] = []
    for _i, row in work.iterrows():
        out.append({
            "report_id": row.get("_rid"),
            "user_id": row.get("user_id"),
            "symbol": row.get("symbol"),
            "signal_date": row.get("signal_date"),
            "custom_basis": row.get("custom_basis"),
            "custom_sha256": row.get("custom_sha256"),
            "ledger_custom_sha256": row.get("custom_ledger_sha256"),
            "report_custom_sha256": row.get("custom_report_sha256"),
            "report_lookup": row.get("custom_report_lookup"),
            "reason": row.get("custom_reason"),
            "detail": row.get("custom_reason_detail"),
        })
    return out


def _custom_evidence_summary(df_full: pd.DataFrame,
                             reader: ReportSnapshotReader | None,
                             gate: Mapping[str, Any]) -> dict:
    """First-screen + run.json block for the custom evidence bases."""
    basis = dict((gate or {}).get("basis_counts") or {})
    lookups = dict(reader.status_counts) if reader is not None else {}
    # DAV-2217 🟢-2: the register value must never be used as a fill. The
    # guarantee is structural (``resolve_custom_evidence`` + the
    # ``unverifiable`` branch of ``_record_version_key``), so it is ALSO
    # recomputed here into counters that must stay 0 on every run — the
    # boolean below is a rule declaration, these two are the self-check.
    reg_custom = dict(p.split("=", 1) for p in
                      REGISTER_ANALYSIS_VERSION_ID.split("|")[1:])["custom"]
    unver = (df_full["custom_basis"] == CUSTOM_BASIS_UNVERIFIABLE
             if "custom_basis" in df_full.columns
             else pd.Series(False, index=df_full.index))
    unver_shas = (df_full["custom_sha256"]
                  if "custom_sha256" in df_full.columns
                  else pd.Series(None, index=df_full.index, dtype=object))
    unverifiable_with_sha = int((unver & unver_shas.notna()).sum())
    unverifiable_with_register_value = int(
        (unver & (unver_shas == reg_custom)).sum())
    db: dict[str, Any] = {"path": None, "available": False}
    if reader is not None:
        db = {"path": str(reader.db_path), "available": bool(reader.available)}
        if reader.db_path.exists():
            st = reader.db_path.stat()
            db.update({
                "size_bytes": int(st.st_size),
                "mtime_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                "uri": f"file:{reader.db_path}?mode=ro&immutable=1",
            })
        if reader.unavailable_reason:
            db["unavailable_reason"] = reader.unavailable_reason
    return {
        "rule": "账本自带 custom_prompt_snapshot→custom_basis=ledger；账本缺、"
                "报告 result_data.custom_prompt_snapshot 有→"
                "report_snapshot（登记 §3 规范化后算哈希）；两处都有但不"
                "一致→custom_conflict 排除；两处都缺→unverifiable"
                "（不得用登记值填补）",
        "ledger_rows": int(len(df_full)),
        "basis_counts": basis,
        "report_lookup_counts": lookups,
        "report_lookup_counts_note":
            "按去重 report_id 计（缓存命中不重复计数）",
        "report_snapshot_db": db,
        "register_value_used_as_fill": False,
        "register_value_used_as_fill_note":
            "规则声明；实际保证在 resolve_custom_evidence 与 "
            "_record_version_key 的 unverifiable 分支，两个计数为其自检"
            "量（均应恒为 0）",
        "unverifiable_rows_with_sha256": unverifiable_with_sha,
        "unverifiable_rows_carrying_register_value":
            unverifiable_with_register_value,
        "excluded": list((gate or {}).get("excluded") or []),
        "reason_codes": dict((gate or {}).get("reason_codes") or {}),
        "alert": _custom_evidence_alert(gate or {}),
        "evidence_file": CUSTOM_EVIDENCE_FILE,
    }


def _label_integrity_alert(integrity: Mapping[str, Any]) -> str | None:
    """First-screen alarm text, or None when nothing needs flagging.

    Covers the DAV-2198 ④ ``label_conflict`` (two differing ``evaluated_ok``
    rows) and the ① hash mismatch. A run with either must say so on the
    report's first screen — never bury it in a later block.
    """
    conflicts = list(integrity.get("conflict_reports") or [])
    counts = dict(integrity.get("reason_codes") or {})
    mismatch = int(counts.get(REASON_LABEL_RECORD_SHA_MISMATCH, 0))
    if not conflicts and not mismatch:
        return None
    bits: list[str] = []
    if conflicts:
        bits.append(
            f"label_conflict: {len(conflicts)} report(s) carry two "
            "evaluated_ok label rows with differing return fields — "
            "excluded from the formal queue "
            f"({', '.join(sorted(conflicts))})")
    if mismatch:
        bits.append(
            f"label_record_sha256_mismatch: {mismatch} ledger row(s) "
            "excluded (label row does not carry the sealed record hash)")
    return " | ".join(bits)


def freeze_block(analysis_anchor: str | None,
                 report_kind: str | None,
                 df: pd.DataFrame,
                 formal: pd.DataFrame | None = None,
                 multi_model_sensitivity: dict | None = None,
                 label_integrity: dict | None = None,
                 formal_start: dict | None = None,
                 pit_provenance: dict | None = None,
                 custom_evidence: dict | None = None) -> dict:
    """Report first-screen freeze block (DAV-2160 §report requirements):
    plan sha256, descriptive-vs-confirmatory label, the served_model-
    non-unique report list, and the register §3 analysis_version_id the run
    reconciles against.

    ``df`` is the FULL pre-select_version input frame (all ledger rows):
    multi-model disclosure is report-level, independent of which queue the
    run reads. ``formal`` is the post-admit formal cohort — when supplied
    the model-distribution block is emitted on BOTH denominators
    (``*_formal_cohort`` vs ``*_all_ledger_rows``, each annotated with
    ``n_rows`` / ``n_reports``) so the register §4 口径区分 is explicit
    (DAV-2166 🟡-3).

    ``multi_model_sensitivity`` is the register §4 必做剔除敏感性臂
    outcome — computed by the caller after the formal queue exists; a
    0-multi-model cohort still writes an explicit ``excluded_reports: 0``
    arm rather than skipping the block.

    ``custom_evidence`` (DAV-2214) is the per-basis distribution of the
    custom-prompt evidences (ledger / report_snapshot / unverifiable)
    plus the ``custom_conflict`` exclusions and the snapshot provenance —
    emitted on the first screen so the register §3 reconciliation is
    auditable without re-deriving it from the ledger."""

    def _model_block(frame: pd.DataFrame) -> dict:
        """Per-denominator served-model distribution: counted on the
        report (run_id) axis — a report's model SET is one observation,
        rows of the same report do not multiply its weight (DAV-2166
        🟡-4). Reports with no ``run_id`` are bucketed separately under
        ``no_run_id`` since they cannot be deduplicated by report."""
        n_rows = int(len(frame))
        if frame.empty or "run_id" not in frame.columns:
            return {"n_rows": n_rows, "n_reports": 0,
                    "served_model_distribution": {},
                    "no_run_id_rows": n_rows,
                    "served_model_missing_total": 0,
                    "multi_model_reports": [], "multi_model_count": 0}
        work = frame.copy()
        work["_rid"] = work["run_id"].map(
            lambda x: str(x) if isinstance(x, str) and x else None)
        has_id = work[work["_rid"].notna()]
        no_id = work[work["_rid"].isna()]
        # one row per distinct report keeps the report-axis denominator.
        first = (has_id.sort_index()
                 .drop_duplicates(subset=["_rid"], keep="first"))
        dist: dict[str, int] = {}
        missing_total = 0
        for _i, row in first.iterrows():
            models = row.get("served_models")
            key = "+".join(models) if models else "none"
            dist[key] = dist.get(key, 0) + 1
            try:
                missing_total += int(row.get("served_model_missing") or 0)
            except (TypeError, ValueError):
                pass
        if len(no_id):
            # 缺 run_id 的报告无法按报告去重 —— 单列桶，分母口径写明。
            dist["no_run_id"] = dist.get("no_run_id", 0) + int(len(no_id))
            for _i, row in no_id.iterrows():
                try:
                    missing_total += int(row.get("served_model_missing") or 0)
                except (TypeError, ValueError):
                    pass
        multi = sorted(
            str(r) for r in
            first.loc[first["multi_model"] == True, "_rid"])  # noqa: E712
        return {
            "n_rows": n_rows,
            "n_reports": int(len(first)),
            "served_model_distribution": dist,
            "no_run_id_rows": int(len(no_id)),
            "served_model_missing_total": missing_total,
            "multi_model_reports": multi,
            "multi_model_count": len(multi),
        }

    all_block = _model_block(df)
    formal_block = _model_block(formal) if formal is not None else None

    out = {
        "plan_file": str(FREEZE_PLAN_FILE.relative_to(REPO_ROOT)),
        "plan_sha256": _freeze_file_sha(FREEZE_PLAN_FILE),
        "plan_sha256_signed": FREEZE_PLAN_SHA256_SIGNED,
        "register_file": str(FREEZE_REGISTER_FILE.relative_to(REPO_ROOT)),
        "register_sha256": _freeze_file_sha(FREEZE_REGISTER_FILE),
        "register_sha256_signed": FREEZE_REGISTER_SHA256_SIGNED,
        "register_analysis_version_id": REGISTER_ANALYSIS_VERSION_ID,
        # DAV-2198 ①/②/④ first-screen disclosure: the enforced cohort start
        # (sourced from the register) and the label association integrity
        # block including the label_conflict alarm.
        "formal_cohort_start": formal_start,
        "label_integrity": label_integrity,
        "custom_evidence": custom_evidence,
        "pit_provenance": pit_provenance,
        "report_kind": report_kind or "descriptive",
        # DAV-2166 🟡-5: the single-confirmatory guarantee is owned by the
        # caller — the script cannot enforce it. Stamped explicitly so a
        # confirmatory run.json carries the provenance obligation.
        "confirmatory_uniqueness": (
            "unique_per_caller" if report_kind == "confirmatory"
            else "not_applicable"),
        "confirmatory_uniqueness_note": (
            "冻结计划允许恰好一次确认读数；本脚本不裁决亦不记忆第几次 "
            "确认运行 —— 唯一性由调用方按总控调度保证并在交付评论留痕。"),
        "analysis_anchor": analysis_anchor,
        "model_distribution_all_ledger_rows": all_block,
        "model_distribution_formal_cohort": formal_block,
        "multi_model_sensitivity": multi_model_sensitivity,
        "note": "描述性读数不进入确认族；唯一确认读数仅在冻结截点跑一次。"
                "多模型报告必标记并单列；剔除敏感性臂（登记 §4 必做）按 "
                "正式 cohort 全量重算，0 多模型也显式写出。",
    }
    # Back-compat: the earlier first-screen keys stay populated on the
    # all-ledger denominator so existing consumers/tests keep working.
    out["multi_model_reports"] = all_block["multi_model_reports"]
    out["multi_model_count"] = all_block["multi_model_count"]
    out["served_model_distribution"] = all_block["served_model_distribution"]
    return out


def _chunks_manifest_sha(chunks_dir: Path) -> str | None:
    """sha256 of the partition set manifest (stats.json) when present — the
    report's 基率来源 fingerprint."""
    p = Path(chunks_dir).parent / "stats.json"
    if not p.exists():
        # tolerate being pointed at the parent dir that already holds stats.json
        p = Path(chunks_dir) / "stats.json"
    if not p.exists():
        return None
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _panel_n_distribution(panel: pd.DataFrame) -> dict:
    """Per-industry per-day matured-count distribution (report's 面板规模)."""
    if panel.empty:
        return {"industries": 0, "signal_days": 0}
    grp = panel.groupby(["signal_date", "l1"]).size()
    per_l1 = grp.groupby("l1").agg(["mean", "min", "max", "count"])
    return {
        "industries": int(panel["l1"].nunique()),
        "signal_days": int(panel["signal_date"].nunique()),
        "matured_labels": len(panel),
        "n_per_cell_mean": float(grp.mean()),
        "n_per_cell_min": int(grp.min()),
        "n_per_cell_max": int(grp.max()),
        "per_industry": {
            str(l1): {"mean_n": float(r["mean"]), "min_n": int(r["min"]),
                      "max_n": int(r["max"]), "days": int(r["count"])}
            for l1, r in per_l1.iterrows()
        },
    }


def base_q_map(panel: pd.DataFrame, m: int = BASE_M, W: int = BASE_W) -> dict:
    """(signal_date, l1) -> shrunken base rate p_m{m} for the T+10 panel."""
    if panel.empty:
        return {}
    base, _summary = build_pit_base_rates(
        panel, shrink_ms=(m,), window_days=(W,), min_cell_n=1)
    col = f"p_m{m}"
    sub = base.loc[base["W"] == W, ["signal_date", "l1", col]]
    return {(str(d), str(l1)): float(q)
            for d, l1, q in zip(sub["signal_date"], sub["l1"], sub[col])}


# ---------------------------------------------------------------------------
# 3. D-075 paired increment
# ---------------------------------------------------------------------------

def paired_increment(formal: pd.DataFrame,
                     qmap: Mapping[tuple[str, str], float],
                     days_index: list[str]) -> dict:
    """Daily IC_model − IC_base on the same cross-sections + NW60/120 CI.

    The paired set per day = formal rows whose (signal_date, l1) cell has a
    computable base rate — BOTH legs are computed on that identical subset
    (DAV-2025 🟡-1: before this fix the model leg used the day's full
    cross-section while the base leg used only the cell-covered subset, so
    `diff` mixed two samples and the "same cross-sections" contract did not
    hold whenever some rows had no base rate). Days failing the n>=10 /
    non-degenerate rules on EITHER side stay NaN in the diff series (real
    gaps kept — never compressed, 签收 item 3). Rows with q & r but no
    q_base are dropped from BOTH legs and reported separately in
    ``no_base_rows`` (总控 10-13 漏斗单列); ``paired_day_n`` records the
    shared cross-section size of each paired day.

    The SW L1 leg comes from the label row the (report_id, 档位) join
    selected (frame column ``label_l1``; empty when the row had none) —
    this function never re-joins labels by symbol+date (DAV-2198 ①).
    """
    d = formal.copy()
    d["_ymd"] = pd.to_datetime(d["signal_date"]).dt.strftime("%Y-%m-%d")
    # day_index entries are YYYYMMDD (DayRow.date); map to the ISO keys used
    # by `ic_model` / `ic_base`.
    def _iso(ymd: str) -> str:
        s = str(ymd)
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if "-" not in s else s
    # DAV-2025 🟡-2: a label row whose `sw` was an explicit JSON null must
    # degrade to l1="" (no base rate found -> NaN q_base -> dropped from the
    # paired subset), never AttributeError — load_ledger_signals already
    # normalises it to "" at the join, this keeps the frame-contract guard.
    if "label_l1" in d.columns:
        l1 = d["label_l1"].fillna("").astype(str)
    else:
        l1 = pd.Series([""] * len(d), index=d.index)
    key = list(zip(d["_ymd"], l1))
    d["q_base"] = [qmap.get(k, np.nan) for k in key]

    ic_model: dict[str, float] = {}
    ic_base: dict[str, float] = {}
    paired_n: dict[str, int] = {}          # per-day paired cross-section size
    total_model_rows = 0                   # 总控 10-13: rows with q & r
    total_paired_rows = 0                  # 总控 10-13: rows also with q_base
    for day, g in d.groupby("_ymd", sort=True):
        ok = g.dropna(subset=["q", "r"])
        total_model_rows += int(len(ok))
        # DAV-2025 🟡-1 / 总控 10-13 按交易日配对: only records carrying BOTH
        # p_rel_t10 (q) and a cell base rate (q_base) enter the day's cross-
        # section; BOTH IC legs are computed on that identical subset, and
        # a day whose paired subset falls under MIN_DAILY_N never counts as
        # a valid paired day (缺基率的记录两侧都不用).
        paired = ok.dropna(subset=["q_base"])
        total_paired_rows += int(len(paired))
        if len(paired) < m1.MIN_DAILY_N:
            continue
        paired_n[day] = int(len(paired))
        ic_m, dm = m1.spearman_ic(paired["q"].to_numpy(float),
                                  paired["r"].to_numpy(float))
        ic_b, db = m1.spearman_ic(paired["q_base"].to_numpy(float),
                                  paired["r"].to_numpy(float))
        if not dm:
            ic_model[day] = ic_m
        if not db:
            ic_base[day] = ic_b

    # a diff day is valid only when BOTH legs evaluated that day
    paired_valid = sorted(d for d in ic_model if d in ic_base)
    diff_series = np.array([ic_model[d] - ic_base[d] for d in paired_valid])

    days_iso = [_iso(day) for day in days_index]
    paired_set = set(paired_valid)
    slot_series = np.array([ic_model[di] - ic_base[di]
                            if di in paired_set else np.nan
                            for di in days_iso])
    mean60, lo60, hi60, n60 = m1.newey_west_ci(slot_series, m1.NW_PRIMARY)
    _, lo120, _, _ = m1.newey_west_ci(slot_series, m1.NW_ROBUST)

    return {
        "paired_days": len(paired_valid),
        # 总控 10-13 funnel 单列: 缺基率的记录两侧都不用, 行数单列计数。
        # model_rows = 同时有 q & r 的记录数; paired_rows = 再叠加有基率
        # (q_base) 的记录数; no_base_rows = 两者之差 (membership gap /
        # sw.index_code 空 / 该日该行业 P1 面板无成熟标签)。
        "model_rows": total_model_rows,
        "paired_rows": total_paired_rows,
        "no_base_rows": int(total_model_rows - total_paired_rows),
        # 每个配对日两腿共用的横截面大小 (same-sample 自证: 两侧 n 相等).
        "paired_day_n": {day: paired_n[day] for day in paired_valid},
        "min_paired_n": (min(paired_n.values()) if paired_n else 0),
        "model_ic_mean": float(np.nanmean(list(ic_model.values())))
        if ic_model else np.nan,
        "base_ic_mean": float(np.nanmean(list(ic_base.values())))
        if ic_base else np.nan,
        "diff_mean": mean60,
        "nw60": {"lo": lo60, "hi": hi60, "n_slots": n60},
        "nw120_lo": lo120,
        "verdict": ("C" if (len(paired_valid) >= m1.C_GRADE_MIN_DAYS
                            and lo60 > 0 and lo120 > 0)
                    else "B" if len(paired_valid) >= 1 else "A"),
        "note": "D-075 增量判定：NW60/NW120 下界都 >0 且配对日>=60 才能宣称"
                "超出基率；主基准固定 m200/W0，其余参数格仅敏感性展示。",
    }


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def run(ledger_path: Path, labels_path: Path, out_dir: Path,
        *, version_key: str | None = None,
        calendar_path: Path | None = None,
        base_chunks: Path | None = None,
        user_id: str = DEFAULT_USER_ID,
        reattest_path: Path | None = None,
        analysis_anchor: str | None = None,
        report_kind: str | None = None,
        snapshot_db: Path | str | None = None) -> dict:
    """Run the evaluation. ``snapshot_db`` (DAV-2214) is the read-only
    shared snapshot supplying the report-side custom_prompt_snapshot; the
    CLI passes ``~/ta-snapshot/current.db`` by default, ``None`` = run
    ledger-only (programmatic/test callers stay hermetic and never touch
    an operator's snapshot implicitly)."""
    t0 = time.time()
    # DAV-2198 ②: fail-close BEFORE any work when the frozen cohort start is
    # unreadable or disagrees with the code constant.
    formal_start = _formal_start_gate()
    log(f"formal cohort start {formal_start['start']} (freeze register "
        f"{formal_start['source_file']}, match=True)")
    log(f"loading labels {labels_path}")
    labels = load_labels(labels_path)
    label_integrity = dict(labels.integrity)
    log(f"{len(labels)} (report_id, {SHORT_HORIZON}) label key(s) from "
        f"{label_integrity['n_short_rows']} short row(s)")
    early_alert = _label_integrity_alert(label_integrity)
    if early_alert:
        # 首屏告警: the first thing the operator/report sees (DAV-2198 ④).
        log("!! LABEL INTEGRITY ALERT — " + early_alert)
    reattest_index = None
    rp: Path | None = None
    reattest_src: dict | None = None
    if reattest_path is not None:
        rp = Path(reattest_path)
        if not rp.exists():
            raise elp.PipelineError(f"reattest file not found: {rp}")
    else:
        # DAV-2198 ③ / plan §2.2: 账本 UNVERIFIED 查 pit_reattest.jsonl — the
        # ledger's sibling file is the default source (an explicit
        # --pit-reattest still wins). Absent file = no overlay, UNVERIFIED
        # stands (never a silent VERIFIED).
        sibling = Path(ledger_path).parent / pit_reattest.REATTEST_FILE
        rp = sibling if sibling.exists() else None
    if rp is not None:
        reattest_index = pit_reattest.load_reattest_index(rp)
        reattest_src = {
            "file": str(rp),
            "sha256": hashlib.sha256(Path(rp).read_bytes()).hexdigest(),
            "adjudicated_lines": len(reattest_index),
            "note": "no_evidence lines are diagnostics and never enter the "
                    "index (DAV-2097③)",
        }
        log(f"reattest index: {len(reattest_index)} adjudicated line(s) "
            f"from {rp}")
    else:
        reattest_src = None

    # DAV-2214: the report-side custom_prompt_snapshot comes from the
    # shared static snapshot. An explicit snapshot path that does not exist
    # fails closed — "evidence silently missing" is exactly the failure
    # mode the evidence bases exist to make visible; --no-report-snapshot
    # is the deliberate opt-out (rows then stay ledger/unverifiable).
    snapshot_reader: ReportSnapshotReader | None = None
    if snapshot_db is not None:
        snap_path = Path(snapshot_db).expanduser()
        if not snap_path.exists():
            raise elp.PipelineError(
                f"report snapshot db not found: {snap_path}; refusing to "
                "run with custom evidence silently missing — pass "
                "--no-report-snapshot to accept ledger-only evidence "
                "(rows keep custom_basis=unverifiable)")
        snapshot_reader = ReportSnapshotReader(snap_path)
        log(f"report snapshot for custom evidence: {snap_path} "
            "(file:…?mode=ro&immutable=1, query_only=ON)")
    # The reader is only needed for the per-row evidence resolution inside
    # load_ledger_signals; close it as soon as that frame exists (DAV-2217
    # 🟢-1 — the CLI process exit used to be the only thing releasing it).
    try:
        df = load_ledger_signals(ledger_path, labels, user_id,
                                 reattest_index=reattest_index,
                                 analysis_anchor=analysis_anchor,
                                 snapshot=snapshot_reader)
    finally:
        if snapshot_reader is not None:
            snapshot_reader.close()
    ledger_rows_input = len(df)
    log(f"ledger rows {ledger_rows_input}")

    # Full input frame preserved for the freeze block's all-ledger
    # denominator (multi-model disclosure is report-level, independent of
    # which queue the run reads, and the DAV-2214 custom-conflict rows are
    # disclosed there too) — the gates/select_version below narrow `df`.
    df_full = df.copy()

    # DAV-2214: custom-prompt evidence gate. Runs BEFORE select_version so
    # an excluded conflict row cannot force the multi-version fail-close on
    # a queue it never enters.
    df, custom_join = _apply_custom_gate(df)
    custom_evidence = _custom_evidence_summary(df_full, snapshot_reader,
                                               custom_join)
    if custom_join["reason_codes"]:
        log(f"custom evidence gate: {custom_join['reason_codes']}")
    if custom_evidence["alert"]:
        log("!! CUSTOM EVIDENCE ALERT — " + custom_evidence["alert"])
    version_input_rows = len(df)

    # single-version queue (fail-close multi-version, D-075/DAV-1682)
    df = m1.select_version(df, version_key)
    post_version_rows = len(df)
    # DAV-2214 / DAV-2217 🟡-1: the custom-basis distribution of the
    # VERSION-SELECTED queue is counted HERE — after select_version and
    # before the cohort / label gates narrow `df`. Counting it later (as
    # the first cut did) silently describes admit_input_rows instead, so
    # the field would drift below its own declared denominator whenever a
    # run drops pre-cohort or label-gated rows. Snapshotted into a plain
    # dict immediately so no later step can shift the denominator.
    selected_queue_basis_counts = (
        {str(k): int(v) for k, v in df["custom_basis"].value_counts().items()}
        if "custom_basis" in df.columns else {})

    # DAV-2198 ③: PIT provenance of the selected queue (ledger first, the
    # sidecar only for non-final ledger statuses; the label row's own
    # input_pit_status is never read).
    pit_provenance = _pit_provenance(df, reattest_src)
    log(f"pit provenance: {pit_provenance['effective']} "
        f"overlay={pit_provenance['overlay_applied']}")

    # DAV-2198 ②: formal cohort start, enforced in code from the frozen
    # register — rows before 2026-10-08 never reach the formal queue.
    df, formal_start = _apply_formal_start(df, formal_start)
    log(f"cohort start filter: -{formal_start['excluded_rows']} row(s) "
        f"before {formal_start['start']} "
        f"({formal_start['excluded_min_signal_date']}.."
        f"{formal_start['excluded_max_signal_date']})")

    # DAV-2198 ①/④: label association + duplicate-label integrity gate —
    # conflicted / unverifiable links leave the queue under a reason code.
    df, label_join = _apply_label_gate(df)
    if label_join["counts"]:
        log(f"label gate: {label_join['counts']}")
    admit_input_rows = len(df)

    # 封存时点门统一为 m1_eval 的 DAV-1991 实现（总控 10-14 令）——本卡不再
    # 自养一套 _sealed_at_utc/apply_seal_gate，改为把交易日历传进
    # m1.admit(calendar=...) 由它判 sealed_at < T+1 09:30 Asia/Shanghai。
    # 门前的 select_version 分流仍在 funnel 里单列 excl_version。
    #
    # DAV-2052 🟡-1 产物内守恒式（审计对账无需外部数据）:
    #   ledger_rows         = excl_custom_conflict + excl_version
    #       + excl_pre_cohort
    #       + excl_label_conflict + excl_label_record_sha256_mismatch
    #       + admit_input_rows
    #   admit_input_rows（进入 m1.admit 的行数）
    #       = post_gate_rows(=funnel.formal_rows)
    #       + excl_wrong_user + excl_pit_failed + excl_non_f0
    #       + excl_unverified + excl_late_seal + excl_not_completed
    #       + excl_bad_prob + excl_immature_label + excl_dup_run
    #   seal_gate.input_rows（进入封存门的行数）= post_gate_rows
    #       + excl_late_seal + excl_not_completed + excl_bad_prob
    #       + excl_immature_label + excl_dup_run
    # DAV-2214 在 select_version 之前新增一个门前桶（custom_conflict），
    # 因此 excl_version 只在检出 custom 冲突后的行数上计算；
    # DAV-2198 ②/① 新增的三个门前桶（cohort 起点 / label_conflict /
    # record_sha256 不一致）同样在 admit 之前分流，同样计入 admit_input_rows。
    # 注意: m1 把「晚封 | sealed 解析失败 | T+1 截止不可解析」单掩码全计入
    # excl_late_seal — excl_seal_t1_unresolved / seal_unparseable /
    # excl_seal_unresolvable 都是它的子集披露，绝不再相加（相加即重复
    # 计数，旧守恒式的 bug）。
    calendar_iso = elp._load_calendar(
        Path(calendar_path) if calendar_path else None)
    cal_ymd = [d.replace("-", "") for d in calendar_iso]
    pre_admit_ids = set(df["run_id"]) if "run_id" in df.columns else set()

    formal, funnel = m1.admit(df, user_id=user_id, calendar=cal_ymd)
    # DAV-2214: custom-basis distributions of the three denominators the
    # card's 交付附 and the first screen distinguish — all ledger rows
    # (``basis_counts``), the version-selected queue (counted right after
    # select_version, before the cohort/label gates) and the admitted
    # formal cohort (DAV-2217 🟡-1: the queue count must NOT be taken from
    # the post-gate frame).
    custom_evidence["selected_queue_basis_counts"] = dict(
        selected_queue_basis_counts)
    custom_evidence["formal_cohort_basis_counts"] = (
        {str(k): int(v) for k, v in
         formal["custom_basis"].value_counts().items()}
        if "custom_basis" in formal.columns else {})

    # DAV-2198: the three pre-admit buckets live in the same funnel so the
    # product-internal conservation identity closes without external data.
    funnel["excl_pre_cohort"] = int(formal_start["excluded_rows"])
    # DAV-2214: custom_prompt_snapshot 两处证据不一致（custom_conflict）
    # 在 select_version 之前排除 — 单列桶使内守恒式仍可闭合。
    funnel["excl_custom_conflict"] = int(
        custom_join["reason_codes"].get(REASON_CUSTOM_CONFLICT, 0))
    funnel["excl_label_conflict"] = int(
        label_join["counts"].get(REASON_LABEL_CONFLICT, 0))
    funnel["excl_label_record_sha256_mismatch"] = int(
        label_join["counts"].get(REASON_LABEL_RECORD_SHA_MISMATCH, 0))
    funnel["formal_start"] = formal_start["start"]
    funnel["admit_input_rows"] = int(admit_input_rows)
    # The overlay happened in load_ledger_signals (ledger-first order), not
    # inside m1.admit — report the real count under the same key.
    funnel["pit_reattest_applied"] = int(pit_provenance["overlay_applied"])

    # Label-integrity first-screen block: join exclusions + the alarm.
    label_integrity["join_exclusions"] = label_join
    label_integrity["reason_codes"] = {
        **(label_integrity.get("reason_codes") or {}),
        **label_join["counts"],
    }
    label_integrity["alert"] = _label_integrity_alert(label_integrity)

    # Multi-model exclusion sensitivity arm (register §4 必做, DAV-2166
    # 🟡-3): recompute the formal cohort with every multi-model report
    # dropped and re-run the same day-series. 0 multi-model reports still
    # produces an explicit arm (excluded_reports: 0, identical metrics)
    # rather than a missing block — the arm exists to prove the main
    # number is stable under the exclusion, not only when it changes.
    formal_mm = formal[formal["multi_model"] == True] \
        if "multi_model" in formal.columns else pd.DataFrame()
    mm_sens: dict = {
        "excluded_reports": int(formal_mm["run_id"].nunique())
        if len(formal_mm) else 0,
        "excluded_rows": int(len(formal_mm)),
        "cohort_rows_before": int(len(formal)),
        "cohort_rows_after": int(len(formal) - len(formal_mm)),
    }
    if len(formal_mm):
        formal_excl = formal[formal["multi_model"] != True]
        days_excl = m1.eval_days(formal_excl, calendar=cal_ymd)
        # grade() embeds the passed funnel into its return — hand it an
        # empty dict so the sensitivity arm doesn't smuggle the main
        # funnel's counts under its own label.
        v_excl = m1.grade(days_excl, {})
        mm_sens.update({
            "valid_days_excl": int(v_excl["valid_days"]),
            "mean_ic_excl": float(v_excl["mean_ic"])
            if np.isfinite(v_excl["mean_ic"]) else None,
            "grade_excl": v_excl["grade"],
        })
    else:
        mm_sens.update({
            "valid_days_excl": None, "mean_ic_excl": None,
            "grade_excl": None,
            "note": "正式 cohort 无多模型报告；敏感性与主样本相同。",
        })

    # Build the report first-screen freeze block now that the formal
    # cohort exists — model distribution is emitted on both denominators
    # (all ledger rows vs formal cohort, each n_rows / n_reports
    # annotated) and the multi-model sensitivity arm is attached
    # (DAV-2166 🟡-3 / 🟡-4).
    freeze = freeze_block(analysis_anchor, report_kind, df_full,
                          formal=formal,
                          multi_model_sensitivity=mm_sens,
                          label_integrity=label_integrity,
                          formal_start=formal_start,
                          pit_provenance=pit_provenance,
                          custom_evidence=custom_evidence)

    # seal_gate 块（DAV-1993 🟡 M-1）: 口径 + 被分流 run_id 落盘，verdict.model
    # 与 run.json 顶层各一份同值。计数取自 m1.admit 的 funnel：
    #   excl_late_seal          = 晚封 + 不可判定（seal/signal 解析失败或
    #                           T+1 不可解析）合计 — m1 将它们都 fail-closed
    #                           排进该桶；
    #   excl_seal_t1_unresolved = T+1 截止无法解析的子集（signal 非交易日 /
    #                           早于日历首日 / 日历先尽）；
    #   seal_unparseable        = sealed_at 解析失败的子集。
    # 兼容口径别名：excl_seal_late(=excl_late_seal 总桶) /
    #   excl_seal_unresolvable(=t1_unresolved+unparseable，不可判定子集)。
    post_admit_ids = set(formal["run_id"]) if "run_id" in formal.columns else set()
    # DAV-2052 🟡-2: run_id 可含 None（账本行缺 report_id）——裸 sorted()
    # 对 str/None 混排直接 TypeError，uncaught 崩出而不走 fail-close。
    # 与 select_version 的 <NaN> 处理同族：先 str() 再排，保证落盘确定性。
    admit_excluded_ids = sorted(str(x) for x in pre_admit_ids - post_admit_ids)
    # 分桶 run_id（产物用，供事后复核）：重放 m1.admit 门内判定——先按
    # admit 顺序剔除 seal 门之前已被排除的行（wrong_user / pit_failed /
    # non_f0 / unverified），剩余行中 sealed>=entry → 晚封桶；sealed/entry
    # NaT → 不可判定桶。复用 m1 的 _sealed_at_sh/_seal_deadline_cn，不自养
    # 解析口径。
    d0 = df.copy()
    if "user_id" in d0.columns and user_id:
        d0 = d0[d0["user_id"].astype(str) == str(user_id)]
    d0 = d0[d0["input_pit_status"].astype(str).str.upper() != "FAILED"]
    d0 = d0[d0["timing_class"].astype(str).str.upper() == "F0"]
    d0 = d0[d0["input_pit_status"].astype(str).str.upper() == "VERIFIED"]
    rid = d0["run_id"] if "run_id" in d0.columns else pd.Series([], dtype=str)
    sealed_sh = m1._sealed_at_sh(d0["sealed_at"]) if "sealed_at" in d0.columns \
        else pd.Series(pd.NaT, index=d0.index)
    entry_cn = m1._seal_deadline_cn(d0["signal_date"], cal_ymd)
    late_ids = sorted(str(r) for r, m in zip(rid, sealed_sh.notna()
                        & entry_cn.notna() & (sealed_sh >= entry_cn)) if m)
    unres_ids = sorted(str(r) for r, m in zip(
        rid, sealed_sh.isna() | entry_cn.isna()) if m)
    # DAV-2052 🟡-3: 披露回填 — m1 的 _sealed_at_sh 把裸 "YYYY-MM-DD" 读成
    # 00:00 UTC（按准点收），与 33b1f5b 前自养实现的准入行为一致；此键仅
    # 记数不改判，入 seal_gate 块与 funnel 两处（date-only 戳该排除还是
    # 读成午夜属 m1_eval 口径决策，DAV-1991 线裁定，本卡不擅动）。
    n_dateonly = int(d0["sealed_at"].astype(str).str.strip()
                     .str.match(_DATEONLY_RE).sum()) \
        if "sealed_at" in d0.columns else 0
    seal_gate = {
        "field": "sealed_at",
        "field_equivalence_note":
            "sealed_at == seal_basis_at 于本账本实测同值；"
            "seal_basis_at = max(created_at, sealed_at)，补录行可能不等，"
            "本实现取 sealed_at",
        "threshold":
            "signal_date 次一交易日 09:30 Asia/Shanghai"
            "（严格小于，sealed < entry_open；实现=m1_eval DAV-1991 门）",
        "impl": "m1_eval.admit(calendar=cal_ymd)",
        # DAV-2052 🟡-1: input_rows 恢复为「进入封存门的行数」——通过
        # 门前四桶（wrong_user/pit_failed/non_f0/unverified）后由封存门
        # 判别的行数 len(d0)；admit_input_rows 另列 admit 入口行数
        # (=post_version_rows) 供全链路对账。守恒式见上方块注释。
        "input_rows": int(len(d0)),
        "admit_input_rows": int(admit_input_rows),
        "excl_seal_late": int(funnel["excl_late_seal"]),
        "seal_late_ids": late_ids,
        # excl_late_seal 总桶已含不可判定子集（m1 单掩码）— 自证标记。
        "excl_seal_late_includes_unresolvable": True,
        "excl_seal_unresolvable": int(
            funnel["excl_seal_t1_unresolved"] + funnel["seal_unparseable"]),
        "seal_unresolvable_ids": unres_ids,
        "seal_unparseable": int(funnel["seal_unparseable"]),
        "excl_seal_t1_unresolved": int(funnel["excl_seal_t1_unresolved"]),
        "seal_dateonly_fallback": n_dateonly,
        # DAV-2052 🟡-1/🟢-1: admit 全量排除集（含封存门前后所有桶），非
        # 仅封存门分流 — 命名写清口径。
        "admit_excluded_ids": admit_excluded_ids,
    }
    funnel["excl_seal_late"] = seal_gate["excl_seal_late"]
    funnel["excl_seal_unresolvable"] = seal_gate["excl_seal_unresolvable"]
    funnel["excl_version"] = int(version_input_rows - post_version_rows)
    funnel["seal_dateonly_fallback"] = n_dateonly
    funnel["multi_model_reports"] = freeze["multi_model_count"]
    funnel["version_key"] = version_key or (
        str(df["version_key"].iloc[0])
        if "version_key" in df.columns and len(df) else "default")
    log(f"formal rows {len(formal)} / {seal_gate['input_rows']} "
        f"(seal_late={seal_gate['excl_seal_late']} "
        f"seal_unresolvable={seal_gate['excl_seal_unresolvable']})")

    seal_gate_block = dict(seal_gate)
    seal_gate_block["post_gate_rows"] = int(len(formal))

    days = m1.eval_days(formal, calendar=cal_ymd)
    rel = m1.reliability_table(formal) if len(formal) else []
    verdict = m1.grade(days, funnel)
    verdict["seal_gate"] = seal_gate_block

    # D-075: paired increment vs the WHOLE-MARKET m200/W0 base rate.
    # The base panel comes from P1 T+10 partitions (--base-chunks /
    # build-base), never from the P4 ledger's own labels (DAV-1890 fix).
    base_source: dict = {}
    if base_chunks is not None:
        panel = build_base_panel(Path(base_chunks))
        base_source = {
            "kind": "p1_t10_partitions",
            "chunks_dir": str(base_chunks),
            "manifest_sha256": _chunks_manifest_sha(Path(base_chunks)),
            "panel": _panel_n_distribution(panel),
        }
    else:
        panel = pd.DataFrame(columns=["signal_date", "mat", "l1", "y"])
        base_source = {"kind": "none", "note":
                       "no --base-chunks given — D-075 增量不可判定"}
    qmap = base_q_map(panel, m=BASE_M, W=BASE_W)
    days_index = [vars(x)["date"] for x in days]
    increment = paired_increment(formal, qmap, days_index)
    # 总控 10-13: 缺基率记录两侧都不用、漏斗单列 — 同步进主 funnel 落盘，
    # 使 run.json 顶层即可对账，不依赖 increment 块。
    funnel["paired_no_base_rows"] = increment["no_base_rows"]

    # sensitivity grid (other (m, W) cells on the SAME all-market panel;
    # descriptive only, never a substitute for the fixed m200/W0 primary)
    sensitivity = {}
    if not panel.empty:
        base, _ = build_pit_base_rates(
            panel, shrink_ms=SENSITIVITY_MS,
            window_days=SENSITIVITY_WS, min_cell_n=1)
        for W in SENSITIVITY_WS:
            for mm in SENSITIVITY_MS:
                if (mm, W) == (BASE_M, BASE_W):
                    continue
                col = f"p_m{mm}"
                sub = base.loc[base["W"] == W, ["signal_date", "l1", col]]
                qm = {(str(a), str(b)): float(c)
                      for a, b, c in zip(sub["signal_date"], sub["l1"], sub[col])}
                inc = paired_increment(formal, qm, days_index)
                sensitivity[f"m{mm}_W{W}"] = {
                    "paired_days": inc["paired_days"],
                    "base_ic_mean": inc["base_ic_mean"],
                    "diff_mean": inc["diff_mean"],
                    "nw60_lo": inc["nw60"]["lo"],
                    "nw120_lo": inc["nw120_lo"],
                    "verdict": inc["verdict"],
                }

    out = {
        "pipeline": PIPELINE_VERSION,
        "freeze": freeze,
        "input": {"ledger": str(ledger_path), "labels": str(labels_path)},
        "formal_cohort_start": formal_start,
        "label_integrity": label_integrity,
        "custom_evidence": custom_evidence,
        "label_join": label_join,
        "pit_provenance": pit_provenance,
        "base_source": base_source,
        "days": [vars(x) for x in days],
        "reliability": rel,
        "verdict": verdict,
        "increment_m200_W0": increment,
        "sensitivity": sensitivity,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    # DAV-2214 逐行证据: one line per ledger row (all input rows, excluded
    # ones included) so the custom_basis labels are auditable byte-for-byte.
    evidence_rows = _custom_evidence_rows(df_full)
    ev_path = out_dir / CUSTOM_EVIDENCE_FILE
    ev_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n"
                for r in evidence_rows), encoding="utf-8")
    custom_evidence["evidence_rows"] = len(evidence_rows)
    custom_evidence["evidence_file_sha256"] = hashlib.sha256(
        ev_path.read_bytes()).hexdigest()
    days_df = pd.DataFrame(
        [vars(x) for x in days],
        columns=["date", "n_t", "status", "ic", "quintile_diff",
                 "quintile_tied_n", "brier"])
    days_df.to_parquet(out_dir / "day_series.parquet", index=False)
    (out_dir / "verdict.json").write_text(
        json.dumps({"model": verdict, "freeze": freeze,
                    "increment_m200_W0": increment,
                    "base_source": base_source,
                    "sensitivity": sensitivity},
                   ensure_ascii=False, indent=2))
    (out_dir / "reliability.json").write_text(
        json.dumps(rel, ensure_ascii=False, indent=2))
    (out_dir / "run.json").write_text(json.dumps({
        "pipeline": PIPELINE_VERSION,
        "elapsed_s": round(time.time() - t0, 2),
        "peak_rss_gb": round(m1.peak_rss_gb(), 3),
        "freeze": freeze,
        "ledger_rows": ledger_rows_input,
        "formal_rows": len(formal),
        "seal_gate": seal_gate_block,
        "formal_cohort_start": formal_start,
        "label_integrity": label_integrity,
        "custom_evidence": custom_evidence,
        "label_join": label_join,
        "pit_provenance": pit_provenance,
        "labels": len(labels), "version_key": funnel["version_key"],
        # Register §3 reconciliation: the selected queue's key alongside
        # the signed analysis_version_id the report must match, plus a
        # per-segment diff so a reviewer sees exactly which leg disagrees
        # (`custom` on pre-DAV-2181 ledgers — those rows never sealed the
        # snapshot; empty diff once every queued row carries it).
        "selected_version_key": funnel["version_key"],
        "register_analysis_version_id": REGISTER_ANALYSIS_VERSION_ID,
        "version_key_matches_register": (
            funnel["version_key"] == REGISTER_ANALYSIS_VERSION_ID),
        "version_key_register_diff": _key_register_diff(
            funnel["version_key"]),
        "user_id": user_id,
        "base_source_kind": base_source.get("kind"),
        "funnel": funnel,
    }, ensure_ascii=False, indent=2))
    log(f"grade={verdict['grade']} valid_days={verdict['valid_days']} "
        f"increment={increment['verdict']} "
        f"(paired_days={increment['paired_days']}) -> {out_dir}")
    return out


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------

def _ledger_rec(rid: str, symbol: str, day: str, p: int | None,
                *, user: str = DEFAULT_USER_ID, tc: str = "F0",
                pit: str = "VERIFIED", created: str = "2026-01-05T09:00",
                sealed_at: str | None = None,
                commit: str = "abc123def456",
                served_models: Any = None,
                prompt_resolved: Any = None,
                custom_prompt: Any = None,
                contract: str = "v1",
                record_sha256: str | None = None) -> dict:
    versions: dict[str, Any] = {"generated_by_commit_sha": commit,
                                "evidence_contract_version": contract,
                                "run_identity": {
                                    "commit_sha": commit,
                                    "prompt_resolved_hashes":
                                        prompt_resolved},
                                }
    if served_models is not None:
        versions["served_models"] = served_models
    if custom_prompt is not None:
        versions["custom_prompt_snapshot"] = custom_prompt
    rec = {
        "ledger_version": 1, "report_id": rid, "user_id": user,
        "symbol": symbol, "signal_date": day, "created_at": created,
        "sealed_at": sealed_at,
        # DAV-2198 ①: the ledger's own chain field the label row must
        # echo; fixtures may stamp a real value or leave None (then
        # elp._record_identity recomputes the canonical hash).
        "record_sha256": record_sha256,
        # DAV-2166: the version-key contract is read at the probability
        # node — `forecast.contract` (register b1.v1-T10) — so the fixture
        # must seal it there, not only under versions.evidence_contract_.
        "status": "completed", "timing_class": tc,
        "input_pit_status": pit, "backfilled": False,
        "versions": versions,
        # DAV-2166: the version-key contract is read at the probability
        # node — `forecast.contract` (register b1.v1-T10) — so the fixture
        # must seal it there, not only under versions.evidence_contract_.
        "horizons": {"short": {"forecast": {"status": "valid" if p else "missing",
                                          "p_rel_t10": p,
                                          "contract": contract}},
                     "medium": {}},
    }
    return rec


# ---------------------------------------------------------------------------
# P1 all-market T+10 partition builder (DAV-1883 rework: D-075 全市场基率)
# ---------------------------------------------------------------------------

def build_base_t10(out_dir: Path, *, years: str = "", months: str = "",
                   max_rows: int = 1_500_000, max_rss_gb: float = 6.0,
                   force: bool = False) -> int:
    """Build P1 T+10 partitions reusing the DAV-1547 pipeline semantics.

    Same caliber as build_phase2_chunks.py (vendor_qfq, T+1 open entry,
    SW2021-L1 same-window leg, suspension/one-word-limit roll, matured
    actual_exit_date<=T) but the SHORT horizon profile: EVAL_OFFSET=10,
    MAX_ROLL=2 (HORIZON_PROFILE_V1["short"]). The production script reads
    both as module globals, so we import it and override — the whitelisted
    file itself is never edited. Writes the same year=/month= layout plus a
    stats.json manifest under --out-dir.

    Peak memory is bounded by the parent's --max-rss-gb guard (default
    6 GiB build budget). Resumable + --force like the parent.
    """
    import build_phase2_chunks as b1547
    from tradingagents.dataflows.return_labels import HORIZON_MAX_ROLL_DAYS
    from tradingagents.graph.horizon_profile import HORIZON_PROFILE_V1

    b1547.EVAL_OFFSET = int(HORIZON_PROFILE_V1["short"]["primary_eval_offset"])
    b1547.MAX_ROLL = int(HORIZON_MAX_ROLL_DAYS["short"])
    import sys as _s
    _s.argv = ["build_phase2_chunks.py", "--out-dir", str(out_dir)]
    if years:
        _s.argv += ["--years", str(years)]
    if months:
        _s.argv += ["--months", str(months)]
    _s.argv += ["--max-rows-per-partition", str(max_rows)]
    _s.argv += ["--max-rss-gb", str(max_rss_gb)]
    if force:
        _s.argv += ["--force"]
    return b1547.main()


def _label(sym: str, day: str, r_pct: float, l1: str = "801010.SI",
           *, report_id: str | None = None,
           record_sha256: str | None = None,
           horizon: str = SHORT_HORIZON,
           outcome_status: str = "evaluated_ok") -> dict:
    """Synthetic evaluated label row (DAV-1875 row contract).

    ``report_id`` / ``record_sha256`` are the DAV-2198 ① association key
    and the ledger-hash check — fixtures that feed ``load_labels`` must
    set both.
    """
    return {"horizon": horizon, "symbol": sym, "signal_date": day,
            "target_date": day,
            "report_id": report_id,
            "ledger_record_sha256": record_sha256,
            "outcome_status": outcome_status,
            "prediction": {"status": "evaluated", "return_pct": r_pct},
            "sw": {"index_code": l1,
                   "prediction": {"status": "ok", "return_pct": 0.0},
                   "execution": {"status": "ok"}},
            "sw_excess_pct_prediction": r_pct,
            "y_rel_prediction": bool(r_pct > 0)}


def _write_p1_chunks(chunks_dir: Path, panel_rows: list[dict]) -> None:
    """Write synthetic P1 partitions (year=YYYY/data.parquet) carrying the
    six columns m1_base_rate.load_panel reads."""
    df = pd.DataFrame(panel_rows)
    df["year"] = df["signal_date"].astype(str).str[:4]
    for year, g in df.groupby("year"):
        d = Path(chunks_dir) / f"year={year}"
        d.mkdir(parents=True, exist_ok=True)
        g.drop(columns="year").to_parquet(d / "data.parquet", index=False)


def _p1_row(ts_code: str, signal_date: str, l1: str, y: float,
            exit_date: str | None = None) -> dict:
    """One matured P1 T+10 label row (load_panel column contract)."""
    return {"signal_date": signal_date, "sw_l1_code": l1,
            "actual_exit_date": exit_date or signal_date,
            "outcome_status": "evaluated_ok", "universe_ok": True,
            "y_rel": y}


def selftest(tmp: Path) -> dict:
    rng = np.random.default_rng(7)
    checks: dict[str, Any] = {}

    # 80 days x 20 stocks in 4 industries: q monotone in r -> IC≈1.
    # The all-market T+10 base-rate panel is built from synthetic P1
    # partitions (industry win-rates drift over time so the base IC is
    # non-degenerate) — NOT from the P4 ledger's own labels.
    ledger = tmp / "forward_ledger.jsonl"
    lab_f = tmp / "labels_t10.jsonl"
    chunks = tmp / "chunks"
    days = pd.bdate_range("2026-10-08", periods=80).strftime("%Y-%m-%d")
    l1s = ["801010.SI", "801020.SI", "801030.SI", "801040.SI"]
    recs, labs, panel_rows = [], [], []
    for di, day in enumerate(days):
        drift = 0.02 * np.sin(di / 9.0)          # industry win-rate drifts
        for i in range(20):
            l1 = l1s[i % 4]
            q = 5 + i * 4
            # r monotone in q plus an industry drift common to that l1 cell
            r = 0.5 * i - 5.0 + drift * 10 + float(rng.normal(0, 0.3))
            rec = _ledger_rec(f"r{di}_{i}", f"S{i:03d}", day, q,
                              created=f"{day}T09:00",
                              sealed_at=f"{day} 08:00:00.000000")
            recs.append(rec)
            # DAV-2198 ①: association is report_id + 档位 and the label must
            # echo the sealed record's own sha256.
            labs.append(_label(f"S{i:03d}", day, r, l1=l1,
                               report_id=rec["report_id"],
                               record_sha256=elp._record_identity(rec)))
    # all-market panel: many more rows per (day, industry) than the
            # 20-symbol ledger sample — base must come from THIS, not ledger
            ymd = day.replace("-", "")
            for k in range(60):
                yr = 1.0 if rng.random() < (0.5 + drift * (1 + i % 4)) else 0.0
                panel_rows.append(_p1_row(f"M{k:04d}", ymd,
                                          l1, yr, exit_date=ymd))
    with open(ledger, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    with open(lab_f, "w") as fh:
        fh.writelines(json.dumps(l) + "\n" for l in labs)
    _write_p1_chunks(chunks, panel_rows)
    (chunks / "stats.json").write_text(json.dumps({"partitions": "synthetic"}))
    cal = tmp / "cal.txt"
    cal.write_text("\n".join(days) + "\n")

    out = run(ledger, lab_f, tmp / "out", calendar_path=cal,
              base_chunks=chunks, snapshot_db=None)
    checks.update(_selftest_custom_evidence(tmp))
    checks["grade"] = out["verdict"]["grade"]
    checks["inc_verdict"] = out["increment_m200_W0"]["verdict"]
    checks["inc_diff_mean"] = out["increment_m200_W0"]["diff_mean"]
    checks["valid_days"] = out["verdict"]["valid_days"]
    checks["mean_ic"] = out["verdict"]["mean_ic"]
    checks["paired_days"] = out["increment_m200_W0"]["paired_days"]
    checks["base_ic_mean"] = out["increment_m200_W0"]["base_ic_mean"]
    checks["base_kind"] = out["base_source"]["kind"]
    assert checks["grade"] in ("B", "C"), checks
    assert checks["mean_ic"] > 0.9, checks
    assert checks["paired_days"] > 0
    assert np.isfinite(checks["inc_diff_mean"])
    assert checks["mean_ic"] > checks["base_ic_mean"]
    assert checks["paired_days"] <= checks["valid_days"]
    # L-8: valid_days 钉死 — 合成日历末日 T+1 不可解析被 seal_gate 分流,
    # 上游统计量若漂移此处必须红(复审 10-12 实测 79; 该值非本轮改动)。
    assert checks["valid_days"] == 79, checks
    assert checks["base_kind"] == "p1_t10_partitions"
    print(f"[selftest] PASS {checks}", flush=True)
    return checks


def _selftest_custom_evidence(tmp: Path) -> dict:
    """DAV-2214: the four custom-evidence bases, exercised against a
    synthetic read-only snapshot db (no operator snapshot needed).

    Covers the four card cases: ledger-only, report-only, both-equal,
    both-conflicting (excluded with ``custom_conflict``), plus
    neither-present -> ``unverifiable``.
    """
    checks: dict[str, Any] = {}
    db = tmp / "selftest_snapshot.db"
    cps = {"enabled": True, "placement": "after_data",
           "roles": {"trader": {"resolved_hash": "h"}}}
    other = {"enabled": True, "placement": "after_data",
             "roles": {"trader": {"resolved_hash": "DIFFERENT"}}}
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, "
                "result_data TEXT, result_data_zst BLOB)")
    for rid, snap in (("rep_ok", cps), ("rep_only", cps),
                      ("rep_conflict", other), ("rep_none", None)):
        body = {"analysis_status": "completed"}
        if snap is not None:
            body["custom_prompt_snapshot"] = snap
        con.execute("INSERT INTO reports (id, result_data) VALUES (?, ?)",
                    (rid, json.dumps(body)))
    con.commit()
    con.close()
    reader = ReportSnapshotReader(db)

    def _rec(rid, *, ledger_snap=True):
        return _ledger_rec(rid, "S", "2026-10-08", 50,
                           custom_prompt=(cps if ledger_snap else None),
                           contract="b1.v1")

    cases = {
        "ledger_and_report_agree": (_rec("rep_ok"), CUSTOM_BASIS_LEDGER),
        "report_only": (_rec("rep_only", ledger_snap=False),
                        CUSTOM_BASIS_REPORT_SNAPSHOT),
        "conflict": (_rec("rep_conflict"), CUSTOM_BASIS_CONFLICT),
        "neither": (_rec("rep_none", ledger_snap=False),
                    CUSTOM_BASIS_UNVERIFIABLE),
    }
    basis = {}
    for name, (rec, want) in cases.items():
        ev = resolve_custom_evidence(rec, reader)
        basis[name] = ev["basis"]
        assert ev["basis"] == want, (name, ev)
    a = resolve_custom_evidence(cases["ledger_and_report_agree"][0], reader)
    b = resolve_custom_evidence(cases["report_only"][0], reader)
    c = resolve_custom_evidence(cases["conflict"][0], reader)
    d = resolve_custom_evidence(cases["neither"][0], reader)
    assert a["sha256"] == b["sha256"] == _canonical_json_sha(cps)
    assert c["reason"] == REASON_CUSTOM_CONFLICT and c["sha256"] is None
    assert d["sha256"] is None and d["report_lookup"] == "missing_field"
    # the report-only hash is what the version key stamps (never the
    # register's b0abab… value nor an "unverifiable" placeholder).
    key = _record_version_key(cases["report_only"][0], anchor="082be047",
                              custom_sha=b["sha256"])
    assert "custom=" + b["sha256"] in key
    # the conflict row leaves the queue with its reason code
    frame = pd.DataFrame(
        [{"run_id": r["report_id"], "symbol": "S", "signal_date": "20261008",
          "custom_basis": resolve_custom_evidence(r, reader)["basis"],
          "custom_sha256": resolve_custom_evidence(r, reader)["sha256"],
          "custom_reason": resolve_custom_evidence(r, reader)["reason"],
          "custom_reason_detail":
              resolve_custom_evidence(r, reader)["reason_detail"],
          "label_reason": None}
         for r, _w in cases.values()])
    kept, gate = _apply_custom_gate(frame)
    assert len(kept) == len(frame) - 1
    assert gate["reason_codes"] == {REASON_CUSTOM_CONFLICT: 1}
    assert _custom_evidence_alert(gate) is not None
    reader.close()
    checks["custom_basis"] = basis
    checks["custom_conflict_excluded"] = 1
    checks["custom_basis_counts"] = gate["basis_counts"]
    return checks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command")
    r = sub.add_parser("run", help="evaluate the p_rel_t10 ranking baseline")
    r.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    r.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    r.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    r.add_argument("--version-key", default=None)
    r.add_argument("--analysis-anchor", default=None,
                   help="frozen production-release anchor stamped on every "
                        "record's version key (DAV-2160); omit -> each "
                        "record's own sealed commit anchors its key")
    r.add_argument("--report-kind", default=None,
                   choices=["descriptive", "confirmatory"],
                   help="first-screen label: descriptive (default) or the "
                        "single frozen confirmatory read")
    r.add_argument("--calendar-file", default=None,
                   help="trading calendar (one date per line); default live")
    r.add_argument("--base-chunks", type=Path, default=None,
                   help="P1 all-market T+10 partition dir (year=*/data.parquet); "
                        "omit -> D-075 增量不可判定 (base_source=none)")
    r.add_argument("--user-id", default=DEFAULT_USER_ID)
    r.add_argument("--pit-reattest", type=Path, default=None,
                   help="optional pit_reattest.jsonl (DAV-2097③, DAV-2198 ③): "
                        "ledger input_pit_status=UNVERIFIED rows take the "
                        "sidecar's adjudicated verdict; VERIFIED/FAILED stay "
                        "ledger-final. Default: the ledger's sibling "
                        "pit_reattest.jsonl when present, else no overlay")
    r.add_argument("--snapshot-db", type=Path, default=DEFAULT_SNAPSHOT_DB,
                   help="read-only shared snapshot (default "
                        "~/ta-snapshot/current.db, opened "
                        "file:…?mode=ro&immutable=1) whose "
                        "reports.result_data.custom_prompt_snapshot supplies "
                        "the custom evidence for ledger rows that never "
                        "sealed it (DAV-2214); a missing file fails closed")
    r.add_argument("--no-report-snapshot", action="store_true",
                   help="opt out of the report snapshot: custom evidence is "
                        "ledger-only and such rows keep "
                        "custom_basis=unverifiable (never filled from the "
                        "register)")
    b = sub.add_parser("build-base",
                       help="build P1 all-market T+10 partitions (1547 caliber, "
                            "offset=10 roll=2) into --out-dir/chunks_t10")
    b.add_argument("--out-dir", type=Path, default=Path("data/phase2_t10"))
    b.add_argument("--years", default="", help="comma list; default all")
    b.add_argument("--months", default="", help="comma list")
    b.add_argument("--max-rows-per-partition", type=int, default=1_500_000)
    b.add_argument("--max-rss-gb", type=float, default=6.0)
    b.add_argument("--force", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        import tempfile
        selftest(Path(tempfile.mkdtemp(prefix="prel10_")))
        return 0
    if a.command == "run":
        try:
            run(a.ledger, a.labels, a.out_dir,
                version_key=a.version_key,
                calendar_path=a.calendar_file,
                base_chunks=a.base_chunks, user_id=a.user_id,
                reattest_path=a.pit_reattest,
                analysis_anchor=a.analysis_anchor,
                report_kind=a.report_kind,
                snapshot_db=(None if a.no_report_snapshot
                             else a.snapshot_db))
            return 0
        except elp.PipelineError as exc:
            print(f"FAIL-CLOSE: {exc}", file=sys.stderr)
            return 2
    if a.command == "build-base":
        return build_base_t10(a.out_dir, years=a.years,
                              months=a.months,
                              max_rows=a.max_rows_per_partition,
                              max_rss_gb=a.max_rss_gb, force=a.force)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
