#!/usr/bin/env python3
"""DAV-1875 — Expiry return-label pipeline over the P4 forward ledger (T+10 / T+40).

Reads the P4 snapshot ledger (``work/phase2-ledger/forward_ledger.jsonl``,
fixed account only) plus forward-adjusted (前复权) daily bars and appends
per-horizon expiry labels to **separate label files** under
``work/phase2-labels/`` (``labels_t10.jsonl`` / ``labels_t40.jsonl`` — the
file names and ``(symbol, signal_date)`` key shape the M2 periodic report
already consumes). The ledger and the reports it came from are NEVER
written back; the pipeline is a pure reader of the ledger plus market data.

P2 口径 (DAV-1479 计划 v1.0 §4 P2, single truth source = return_labels):
* entry   : T+1 trading-day OPEN (executable entry; suspension / limit-lock
            at entry -> unexecutable_entry, no return);
* target  : signal date + N-th trading day CLOSE (short N=10, medium N=40,
            calendar-indexed — never weekday arithmetic);
* roll    : target-day suspension rolls to the first tradable exit within
            max_roll (short 2 / medium 5); exhausted -> suspension;
* legs    : CSI 300 (``000300.SH``) over the execution window (kept as the
            secondary benchmark, V-03b convention) and SW2021 L1 industry
            index (point-in-time membership at T, DAV-1453 口径A) over BOTH
            the prediction window (entry -> target) and the execution window
            (entry -> actual exit) — 行业腿与个股同窗;
* direction hit : the ledger horizon direction sign is applied when reading
            the long-side settlement (看多 hit = return > 0, 看空 hit =
            return < 0, 中性/无结论 -> null).

Fail-close rules:
* the trading calendar MUST be resolvable (``--calendar-file`` or the live
  akshare/fuyao route via ``require_cn_trade_dates``); unavailable calendar
  -> hard exit (exit 2), never a weekday fallback and never a truncated
  window;
* an unparseable ledger line aborts the run (integrity), never skipped;
* every matured ledger line yields exactly one label row — evaluated or a
  typed status row; 未到期 (not matured) yields NO row (counted only);
* label rows are append-only: a (report_id, horizon) key already present in
  the file is skipped and NEVER rewritten (idempotent re-runs).

Every row carries ``input_sha256`` — the sha256 over the canonical JSON of
the exact inputs (ledger record hash, consumed bar window, benchmark bars,
SW index bars, membership, config), so a label is verifiable against its
inputs; a re-run that finds a changed market input shows up as a hash
conflict instead of a silent overwrite (conflicts are counted, never
rewritten).

Typed statuses beyond the return_labels OutcomeStatus vocabulary appear only
for pre-settlement failures: ``signal_date_not_in_calendar``,
``calendar_insufficient_coverage``, ``symbol_unmappable``,
``symbol_excluded_bse``, ``stock_bars_unavailable``, ``label_resolution_failed``.

Determinism: the label file is a pure function of (ledger, market inputs,
calendar, as_of, config) — no timestamps inside rows, ledger-order append.
Memory: the ledger is streamed line by line, the offline bar files are read
through a per-symbol byte-offset index (never whole-file), and rows are
written as they are produced — peak RSS stays flat in the number of ledger
lines (bounded by distinct symbols' window bars only).

Usage:
  python scripts/phase2/expiry_label_pipeline.py run \
      --ledger work/phase2-ledger/forward_ledger.jsonl \
      --out-dir work/phase2-labels \
      [--as-of YYYY-MM-DD] [--as-of-market-open] \
      [--calendar-file PATH] [--bars-file PATH] [--hs300-file PATH] \
      [--sw-daily-file PATH] [--sw-industry-map PATH] \
      [--user-id UUID] [--price-basis vendor_qfq] [--return-type price_return]
  python scripts/phase2/expiry_label_pipeline.py verify --out-dir DIR
  python scripts/phase2/expiry_label_pipeline.py status --out-dir DIR

Production (network) mode: omit the offline file flags — stock/benchmark
bars come from the routed vendor daily-K route (前复权), SW index bars from
Tushare ``sw_daily`` and membership from ``index_classify`` +
``index_member_all`` (point-in-time). Offline mode (all four file flags) is
fully deterministic and touches no network — it is the replay/test path.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
# Sibling import keeps the fixed P4 account + ledger record helpers
# single-sourced with the ledger writer (DAV-1480).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_snapshot_ledger as dsl  # noqa: E402

from tradingagents.dataflows.return_labels import (  # noqa: E402
    OutcomeStatus,
    PRICE_BASIS_VENDOR_QFQ,
    ReturnType,
    resolve_horizon_calendar_window,
    resolve_horizon_return_label,
)
from tradingagents.dataflows.sw_benchmark import resolve_sw_benchmark_leg  # noqa: E402
from tradingagents.graph.horizon_profile import HORIZON_PROFILE_V1  # noqa: E402

PIPELINE_VERSION = "expiry_label_pipeline.v1"
DEFAULT_USER_ID = dsl.DEFAULT_USER_ID
DEFAULT_LEDGER = REPO_ROOT / "work" / "phase2-ledger" / "forward_ledger.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "work" / "phase2-labels"
DEFAULT_BENCHMARK_SYMBOL = "000300.SH"
HORIZONS = ("short", "medium")
LABEL_FILE_NAMES = {"short": "labels_t10.jsonl", "medium": "labels_t40.jsonl"}
STATE_FILE_NAME = "label_run_state.json"
LOG_FILE_NAME = "label_pipeline.log"

# Exit codes: 0 ok, 1 verify failure, 2 fail-close (calendar/ledger/inputs).
EXIT_OK = 0
EXIT_VERIFY_FAIL = 1
EXIT_FAIL_CLOSED = 2

# Direction buckets — DAV-1498 签收口径 (偏多并入看多、偏空并入看空),
# same mapping the M2 periodic report uses.
_DIRECTION_KEYS = (
    ("看多", ("看多", "偏多", "BUY", "BULL", "增持", "买入")),
    ("看空", ("看空", "偏空", "SELL", "BEAR", "减持", "卖出")),
    ("中性", ("中性", "NEUTRAL", "HOLD", "持有")),
)

# Pipeline-level typed statuses (pre-settlement failures only).
STATUS_SIGNAL_DATE_NOT_IN_CALENDAR = "signal_date_not_in_calendar"
STATUS_CALENDAR_INSUFFICIENT = "calendar_insufficient_coverage"
STATUS_SYMBOL_UNMAPPABLE = "symbol_unmappable"
# 北交所 .BJ: canonicalize_symbol 带 canonical_symbol 但状态为 excluded_bse ——
# 股票池规则（V-03/DAV-800）明确排除北交所，与 unmappable 一样必须走 typed
# 分支，绝不出 evaluated_ok 标签污染 T+10/T+40 统计口径（DAV-1880 复审 M-1）。
STATUS_SYMBOL_EXCLUDED_BSE = "symbol_excluded_bse"
STATUS_STOCK_BARS_UNAVAILABLE = "stock_bars_unavailable"
STATUS_LABEL_RESOLUTION_FAILED = "label_resolution_failed"


class PipelineError(RuntimeError):
    """Fail-closed pipeline error (calendar / ledger / input integrity)."""


# SW leg silent-degradation observability (DAV-2041) hardened to
# fail-close (DAV-2034 总控 10-14 令): an ONLINE run without TUSHARE_TOKEN
# labels every row with sw.membership_status=\"gap\" yet still exits 0 — the
# keys are append-only, so one unauthenticated night mints permanent gap
# rows that can never be re-labelled. ``run`` therefore hard-fails
# (PipelineError -> exit 2, zero rows appended) when the online SW leg
# cannot possibly be resolved. The verify-side gap-ratio check below stays
# as the second gate for a token that exists but is rejected by Tushare
# (degradation discovered per-row instead of at startup). Offline mode
# (file inputs) needs no token and skips the check entirely.


def _sw_leg_missing(token: Optional[str] = None) -> bool:
    """True when the ONLINE SW leg cannot possibly be resolved: no Tushare
    token (``resolve_sw_l1_benchmark`` membership + ``sw_daily`` bars both
    come from Tushare). Token presence is necessary, not sufficient — a bad
    token still degrades per-row and is caught by the verify gap-ratio
    check. In offline mode (file inputs) the SW leg needs no token and this
    check is skipped by the caller."""
    tok = os.getenv("TUSHARE_TOKEN", "").strip() if token is None else token
    return not tok


def _row_sw_gap(row: Mapping[str, Any]) -> bool:
    """True when the row's SW industry leg degraded to membership gap."""
    sw = row.get("sw")
    return isinstance(sw, Mapping) and sw.get("membership_status") == "gap"


# Typed statuses whose ``_typed_row`` short-circuits BEFORE the SW leg is
# resolved — such rows are structurally membership_status=\"gap\" and would
# otherwise inflate the verify gap-ratio denominator into a false-positive
# SW-degradation alarm (DAV-2043 🟡: 9 BSE-excluded rows + 1 evaluated row
# produced a verify FAIL on a perfectly healthy Tushare route). Keep this
# set in sync with every ``_typed_row(...)`` call in ``compute_label_row``.
_PRE_SW_TYPED_STATUSES = frozenset({
    STATUS_SIGNAL_DATE_NOT_IN_CALENDAR,
    STATUS_CALENDAR_INSUFFICIENT,
    STATUS_SYMBOL_UNMAPPABLE,
    STATUS_SYMBOL_EXCLUDED_BSE,
    STATUS_STOCK_BARS_UNAVAILABLE,
    STATUS_LABEL_RESOLUTION_FAILED,
})


# ---------------------------------------------------------------------------
# small utils


def _log(out_dir: Path, msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / LOG_FILE_NAME, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _canonical(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, UTF-8, no extra whitespace."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_signal_date(raw: Any) -> Optional[str]:
    """Normalise a ledger signal_date/trade_date value to strict ISO YYYY-MM-DD."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    try:  # datetime-ish strings ("2026-08-01 09:30:00", ISO-8601)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _direction_bucket(raw: Any) -> str:
    """DAV-1498 bucket: 偏多->看多, 偏空->看空; unknown -> 无结论."""
    if raw is None:
        return "无结论"
    s = str(raw).strip().upper()
    if not s:
        return "无结论"
    for label, keys in _DIRECTION_KEYS:
        if any(k.upper() in s for k in keys):
            return label
    return "无结论"


def _horizon_eval_offset(horizon: str) -> int:
    """Canonical primary eval offset from HORIZON_PROFILE_V1 (T+10 / T+40)."""
    return int(HORIZON_PROFILE_V1[horizon]["primary_eval_offset"])


# ---------------------------------------------------------------------------
# ledger reading (streaming; wrapped {"record": ...} or bare record lines)


def _iter_ledger_records(ledger_path: Path) -> Iterator[Tuple[int, dict]]:
    """Yield (lineno, record) for every non-blank ledger line.

    Raises PipelineError on an unreadable line — a hash-chained ledger must
    never be partially trusted.
    """
    if not ledger_path.exists():
        raise PipelineError(f"ledger not found: {ledger_path}")
    with open(ledger_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(
                    f"ledger line {lineno} unreadable: {exc}") from exc
            if isinstance(obj, dict) and isinstance(obj.get("record"), dict):
                yield lineno, obj["record"]
            elif isinstance(obj, dict):
                yield lineno, obj
            else:
                raise PipelineError(
                    f"ledger line {lineno}: not a JSON object")


def _record_identity(record: Mapping[str, Any]) -> str:
    """The ledger's own record hash (chain field), recomputed if absent."""
    sha = record.get("record_sha256")
    if isinstance(sha, str) and sha:
        return sha
    return _sha256_bytes(_canonical(dict(record)))


# ---------------------------------------------------------------------------
# trading calendar (fail-close)


def _load_calendar(calendar_file: Optional[Path]) -> list[str]:
    """Sorted ISO trading dates; raise PipelineError when unavailable.

    ``--calendar-file`` wins (deterministic replay). Otherwise the live
    akshare/fuyao route via require_cn_trade_dates — which raises when the
    calendar cannot be loaded. No weekday fallback anywhere (fail-close).
    """
    if calendar_file is not None:
        if not calendar_file.exists():
            raise PipelineError(
                f"交易日历取不到，报错退出: calendar file not found: {calendar_file}")
        dates: list[str] = []
        with open(calendar_file, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                s = line.strip()
                if not s:
                    continue
                iso = _parse_signal_date(s)
                if iso is None:
                    raise PipelineError(
                        f"calendar file {calendar_file} line {lineno}: "
                        f"unparseable date {s!r}")
                dates.append(iso)
        if not dates:
            raise PipelineError(
                f"calendar file {calendar_file}: empty — 交易日历取不到，报错退出")
        out = sorted(dates)
        if len(set(out)) != len(out):
            raise PipelineError(
                f"calendar file {calendar_file}: duplicate dates")
        return out
    try:
        from tradingagents.dataflows.trade_calendar import require_cn_trade_dates

        dates_objs, _ = require_cn_trade_dates()
    except Exception as exc:
        raise PipelineError(
            f"交易日历取不到，报错退出: {type(exc).__name__}: {exc}") from exc
    if not dates_objs:
        raise PipelineError("交易日历取不到，报错退出: empty calendar")
    return sorted(d.isoformat() for d in dates_objs)


# ---------------------------------------------------------------------------
# offline market-data files (byte-offset indexed, never whole-file loads)

_BAR_KEYS = ("open", "high", "low", "close", "volume")


def _parse_bar_obj(obj: Mapping[str, Any], d_iso: str) -> Optional[dict]:
    """Normalise one JSONL bar row -> {date, open, high, low, close, volume, is_suspended}."""
    bar: dict[str, Any] = {"date": d_iso}
    for k in _BAR_KEYS:
        v = obj.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        bar[k] = float(v)
    susp = obj.get("is_suspended")
    bar["is_suspended"] = bool(susp) if susp is not None else False
    return bar


class OffsetBarIndex:
    """Streaming byte-offset index over a bar JSONL file: one pass records
    {symbol: [byte offsets]}, reads seek+parse only the requested symbol's
    lines. Peak memory is O(distinct symbols), never O(file size).

    A bounded LRU of parsed per-symbol frames (same pattern as
    sw_benchmark._SW_DAILY_MEMO) keeps repeated symbols cheap; the cap only
    controls retention — fail-closed semantics are unchanged.
    """

    _MEMO_MAX_ENTRIES: int = 256

    def __init__(self, path: Path, symbol_field: str = "symbol",
                 date_field: str = "date") -> None:
        self.path = path
        self.symbol_field = symbol_field
        self.date_field = date_field
        self._offsets: Dict[str, list[int]] = {}
        self._memo: "OrderedDict[str, Dict[str, dict]]" = OrderedDict()
        if not path.exists():
            raise PipelineError(f"bars file not found: {path}")
        with open(path, "rb") as fh:
            offset = 0
            for raw in fh:
                if raw.strip():
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise PipelineError(
                            f"bars file {path} unreadable line at byte "
                            f"{offset}: {exc}") from exc
                    sym = obj.get(self.symbol_field)
                    d_iso = _parse_signal_date(obj.get(self.date_field))
                    if isinstance(sym, str) and sym and d_iso:
                        self._offsets.setdefault(sym, []).append(offset)
                offset += len(raw)

    def bars_for(self, symbol: str) -> Dict[str, dict]:
        """All parsed bars for one symbol ({date: bar}); {} when absent."""
        cached = self._memo.pop(symbol, None)
        if cached is not None:
            self._memo[symbol] = cached  # refresh recency
            return cached
        out: Dict[str, dict] = {}
        offsets = self._offsets.get(symbol)
        if not offsets:
            return out
        with open(self.path, "rb") as fh:
            for off in sorted(offsets):
                fh.seek(off)
                raw = fh.readline()
                if not raw.strip():
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise PipelineError(
                        f"bars file {self.path} unreadable line at byte "
                        f"{off}: {exc}") from exc
                d_iso = _parse_signal_date(obj.get(self.date_field))
                bar = _parse_bar_obj(obj, d_iso) if d_iso else None
                if bar is not None:
                    if d_iso in out and out[d_iso] != bar:
                        raise PipelineError(
                            f"bars file {self.path}: conflicting rows for "
                            f"{symbol} on {d_iso}")
                    out[d_iso] = bar
        self._memo[symbol] = out
        self._memo.move_to_end(symbol)
        while len(self._memo) > self._MEMO_MAX_ENTRIES:
            self._memo.popitem(last=False)
        return out

    def window_bars(self, symbol: str, start: str, end: str) -> Dict[str, dict]:
        """Bars for one symbol inside [start, end] inclusive."""
        return {d: b for d, b in self.bars_for(symbol).items()
                if start <= d <= end}


def _load_flat_json(path: Path, what: str) -> dict:
    if not path.exists():
        raise PipelineError(f"{what} file not found: {path}")
    try:
        obj = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{what} file {path} unreadable: {exc}") from exc
    if not isinstance(obj, dict):
        raise PipelineError(f"{what} file {path}: expected a JSON object")
    return obj


def _make_sw_daily_fetcher(sw_index: OffsetBarIndex) -> Callable[
        [str, str, str], Mapping[str, Mapping[str, float]]]:
    """Offline sw_daily fetcher with the resolve_sw_benchmark_leg signature."""
    cache: Dict[str, Dict[str, dict]] = {}

    def fetcher(index_code: str, start_date: str, end_date: str) -> Dict[str, dict]:
        if index_code not in cache:
            cache[index_code] = sw_index.bars_for(index_code)
        bars = cache[index_code]
        out: Dict[str, dict] = {}
        for d, b in bars.items():
            if start_date <= d <= end_date:
                out[d] = {"open": b["open"], "high": b["high"],
                          "low": b["low"], "close": b["close"]}
        return out

    return fetcher


def _make_offline_membership(industry_map: Mapping[str, Any]) -> Callable[
        [str, str], Tuple[Optional[dict], Optional[str]]]:
    """Offline SW2021 L1 membership resolver ({symbol: {code, name}} map).

    Point-in-time semantics are the production resolver's job
    (resolve_sw_l1_benchmark, index_member_all is_new=Y+N); the offline map
    is a deterministic replay fixture and applies to every date.
    """
    gap_prefix = "【数据获取失败】申万一级行业基准："

    def resolver(symbol: str, analysis_date: str) -> Tuple[Optional[dict], Optional[str]]:
        entry = industry_map.get(symbol)
        if not isinstance(entry, Mapping):
            return None, gap_prefix + "membership_missing_or_ambiguous"
        code, name = entry.get("code"), entry.get("name")
        if not isinstance(code, str) or not code or not isinstance(name, str) or not name:
            return None, gap_prefix + "industry_classification_invalid"
        return {"src": "SW2021", "level": "L1", "code": code, "name": name}, None

    return resolver


# ---------------------------------------------------------------------------
# market data access


class MarketData:
    """Bars / SW index data for one run: offline files or the vendor route."""

    def __init__(
        self,
        *,
        calendar: list[str],
        bars_file: Optional[Path] = None,
        hs300_file: Optional[Path] = None,
        sw_daily_file: Optional[Path] = None,
        sw_industry_map: Optional[Path] = None,
        benchmark_symbol: str = DEFAULT_BENCHMARK_SYMBOL,
    ) -> None:
        self.calendar = calendar
        self.benchmark_symbol = benchmark_symbol
        self.offline = bool(bars_file or hs300_file or sw_daily_file
                            or sw_industry_map)
        if self.offline and not (bars_file and hs300_file and sw_daily_file
                                 and sw_industry_map):
            raise PipelineError(
                "offline mode requires all of --bars-file, --hs300-file, "
                "--sw-daily-file and --sw-industry-map (mixed online/offline "
                "market data is not deterministic)")
        self._bar_index = OffsetBarIndex(bars_file) if bars_file else None
        self._hs300_index = OffsetBarIndex(hs300_file) if hs300_file else None
        self._sw_index = OffsetBarIndex(
            sw_daily_file, symbol_field="index_code") if sw_daily_file else None
        self._industry_map = (_load_flat_json(sw_industry_map, "sw-industry-map")
                              if sw_industry_map else None)
        self._vendor_provider = None  # lazily built vendor route
        self._sw_daily_fetcher = None
        self._membership_resolver = None

    # -- stock bars ------------------------------------------------------

    def stock_window(self, symbol: str, start: str, end: str) -> Dict[str, dict]:
        """{date: bar} for one stock over [start, end] (offline or vendor)."""
        if self._bar_index is not None:
            return self._bar_index.window_bars(symbol, start, end)
        provider = self._get_vendor_provider()
        out: Dict[str, dict] = {}
        for d in _dates_between(self.calendar, start, end):
            bar = provider.get_bar(symbol, d)
            if bar is None:
                continue
            out[d] = {
                "date": d,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "is_suspended": bool(bar.is_suspended),
            }
        return out

    # -- benchmark -------------------------------------------------------

    def benchmark_window(self, start: str, end: str) -> Dict[str, dict]:
        if self._hs300_index is not None:
            return self._hs300_index.window_bars(self.benchmark_symbol, start, end)
        provider = self._get_vendor_provider()
        out: Dict[str, dict] = {}
        for d in _dates_between(self.calendar, start, end):
            bar = provider.get_bar(self.benchmark_symbol, d)
            if bar is None:
                continue
            out[d] = {"date": d, "open": float(bar.open),
                      "high": float(bar.high), "low": float(bar.low),
                      "close": float(bar.close), "volume": float(bar.volume),
                      "is_suspended": bool(bar.is_suspended)}
        return out

    # -- SW legs -----------------------------------------------------------

    def sw_daily_fetcher(self) -> Callable[[str, str, str], Mapping[str, Mapping[str, float]]]:
        if self._sw_daily_fetcher is None:
            if self._sw_index is not None:
                self._sw_daily_fetcher = _make_sw_daily_fetcher(self._sw_index)
            else:
                from tradingagents.dataflows.sw_benchmark import default_sw_daily_fetcher

                self._sw_daily_fetcher = default_sw_daily_fetcher
        return self._sw_daily_fetcher

    def membership_resolver(self) -> Callable[[str, str], Tuple[Optional[dict], Optional[str]]]:
        if self._membership_resolver is None:
            if self._industry_map is not None:
                self._membership_resolver = _make_offline_membership(self._industry_map)
            else:
                from tradingagents.dataflows.sw_industry import resolve_sw_l1_benchmark

                self._membership_resolver = resolve_sw_l1_benchmark
        return self._membership_resolver

    # -- internals ---------------------------------------------------------

    def _get_vendor_provider(self):
        if self._vendor_provider is None:
            from tradingagents.eval.v03_return_measure import VendorPriceDataProvider

            # forward_oos_end_date=None: the label pipeline needs bars up to
            # as_of (today), not the frozen V-03 forward-OOS boundary.
            self._vendor_provider = VendorPriceDataProvider(
                forward_oos_end_date=None)
        return self._vendor_provider


def _dates_between(calendar: list[str], start: str, end: str) -> list[str]:
    """Calendar days inside [start, end] inclusive (sorted)."""
    lo = bisect.bisect_left(calendar, start)
    hi = bisect.bisect_right(calendar, end)
    return calendar[lo:hi]


# ---------------------------------------------------------------------------
# per-record label computation


def _bar_open_close(bar: Optional[Mapping[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    if bar is None:
        return None, None
    o = bar.get("open")
    c = bar.get("close")
    return (float(o) if isinstance(o, (int, float)) else None,
            float(c) if isinstance(c, (int, float)) else None)


def _settle_prediction_leg(
    window_bars: Mapping[str, dict],
    entry_date: str,
    target_date: str,
) -> dict:
    """P2 prediction basis: T+1 open -> target close, NO roll (V-03b logic).

    The ``status`` strings below (``entry_bar_missing`` / ``unexecutable_entry``
    / ``target_bar_missing`` / ``target_suspended`` / ``evaluated``) are the
    V-03b prediction-basis sub-field vocabulary, single-sourced with
    ``v03_return_measure.py``; they live in the ``prediction`` sub-field only.
    The row-level ``outcome_status`` always stays the 7-state OutcomeStatus
    vocabulary (DAV-1880 S-1).
    """
    entry_bar = window_bars.get(entry_date)
    if entry_bar is None:
        return {"status": "entry_bar_missing"}
    entry_open, _ = _bar_open_close(entry_bar)
    if (entry_bar.get("is_suspended")
            or float(entry_bar.get("volume") or 0.0) <= 0.0
            or entry_open is None or entry_open <= 0):
        return {"status": "unexecutable_entry"}
    target_bar = window_bars.get(target_date)
    if target_bar is None:
        return {"status": "target_bar_missing"}
    _, target_close = _bar_open_close(target_bar)
    if target_bar.get("is_suspended") or target_close is None or target_close <= 0:
        return {"status": "target_suspended"}
    ret = (target_close - entry_open) / entry_open * 100.0
    return {
        "status": "evaluated",
        "entry_date": entry_date,
        "exit_date": target_date,
        "entry_price": round(entry_open, 4),
        "exit_price": round(target_close, 4),
        "return_pct": round(ret, 4),
    }


def _benchmark_leg(window_bars: Mapping[str, dict],
                   start: str, end: str) -> dict:
    """Simple open(start) -> close(end) return over one window, typed status."""
    start_bar = window_bars.get(start)
    end_bar = window_bars.get(end)
    if start_bar is None or end_bar is None:
        return {"status": "benchmark_bar_missing", "window_start": start,
                "window_end": end}
    entry_px, _ = _bar_open_close(start_bar)
    _, exit_px = _bar_open_close(end_bar)
    if entry_px is None or entry_px <= 0 or exit_px is None or exit_px <= 0:
        return {"status": "benchmark_price_invalid", "window_start": start,
                "window_end": end}
    return {
        "status": "ok",
        "window_start": start,
        "window_end": end,
        "entry_price": round(entry_px, 4),
        "exit_price": round(exit_px, 4),
        "return_pct": round((exit_px - entry_px) / entry_px * 100.0, 4),
    }


def _compute_input_hash(
    *,
    report_id: Any,
    symbol: str,
    signal_date: str,
    horizon: str,
    eval_offset: int,
    ledger_record_sha256: str,
    price_basis: str,
    return_type: str,
    direction_raw: Any,
    stock_bars: Mapping[str, dict],
    benchmark_bars: Mapping[str, dict],
    sw_bars: Mapping[str, Mapping[str, Mapping[str, float]]],
    sw_index_code: Optional[str],
) -> str:
    """sha256 over the canonical JSON of the exact consumed inputs."""
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "report_id": report_id,
        "symbol": symbol,
        "signal_date": signal_date,
        "horizon": horizon,
        "eval_offset": eval_offset,
        "ledger_record_sha256": ledger_record_sha256,
        "price_basis": price_basis,
        "return_type": return_type,
        "direction_raw": direction_raw,
        "stock_bars": {d: {k: stock_bars[d][k] for k in sorted(stock_bars[d])}
                       for d in sorted(stock_bars)},
        "benchmark_bars": {d: {k: benchmark_bars[d][k] for k in sorted(benchmark_bars[d])}
                           for d in sorted(benchmark_bars)},
        "sw_bars": {code: {d: {k: bars[d][k] for k in sorted(bars[d])}
                           for d in sorted(bars)}
                    for code, bars in sorted(sw_bars.items())},
        "sw_index_code": sw_index_code,
    }
    return _sha256_bytes(_canonical(payload))


def _typed_row(
    record: Mapping[str, Any],
    horizon: str,
    status: str,
    *,
    signal_date: Optional[str] = None,
    window: Any = None,
    canonical_symbol: Optional[str] = None,
    symbol_raw: Any = None,
) -> dict:
    """Typed-status label row for a matured-but-unevaluable sample."""
    return {
        "pipeline": PIPELINE_VERSION,
        "report_id": record.get("report_id"),
        "symbol": canonical_symbol,
        "symbol_raw": (str(symbol_raw) if symbol_raw is not None
                       else (str(record.get("symbol"))
                             if record.get("symbol") is not None else None)),
        "signal_date": signal_date,
        "horizon": horizon,
        "eval_offset": int(getattr(window, "eval_offset", 0) or 0),
        "ledger_record_sha256": _record_identity(record),
        "timing_class": record.get("timing_class"),
        "input_pit_status": record.get("input_pit_status"),
        "backfilled": bool(record.get("backfilled")),
        "outcome_status": status,
        "entry_date": getattr(window, "executable_entry_date", None),
        "target_date": getattr(window, "target_calendar_date", None),
        "actual_exit_date": None,
        "roll_days_used": 0,
        "entry_price": None,
        "exit_price": None,
        "return_pct": None,
        "direction_signed_return_pct": None,
        "direction_hit": None,
        "prediction": {"status": status},
        "execution": {"status": status, "exit_date": None},
        "hs300": {"status": status},
        "hs300_excess_pct": None,
        "sw": {"membership_status": "gap", "prediction": {"status": status},
               "execution": {"status": status}},
        "sw_excess_pct": None,
        "sw_excess_pct_prediction": None,
        "y_rel_prediction": None,
        "input_sha256": None,
    }


def compute_label_row(
    record: Mapping[str, Any],
    *,
    horizon: str,
    calendar: list[str],
    as_of: str,
    as_of_market_closed: bool,
    market: MarketData,
    price_basis: str = PRICE_BASIS_VENDOR_QFQ,
    return_type: str = ReturnType.PRICE_RETURN.value,
) -> Optional[dict]:
    """One label row for one ledger record + horizon, or None when 未到期.

    Matured records always return a row — evaluated or typed status.
    Maturity is calendar-computed (never blind-trust of the ledger's
    timing_class, which is seal-time based): target = signal + N-th trading
    day; due when target < as_of, or target == as_of with the market closed.
    """
    from tradingagents.agents.utils.symbol_canonical import canonicalize_symbol

    signal_date = _parse_signal_date(record.get("signal_date")
                                     or record.get("trade_date"))
    if signal_date is None:
        return None  # counted by the caller as an invalid-signal-date line

    # --- maturity (strict, calendar-indexed) ---------------------------
    idx = bisect.bisect_right(calendar, signal_date) - 1
    if idx < 0 or calendar[idx] != signal_date:
        # signal date is not a trading day in this calendar
        return _typed_row(record, horizon,
                          STATUS_SIGNAL_DATE_NOT_IN_CALENDAR,
                          signal_date=signal_date)
    offset = _horizon_eval_offset(horizon)
    target_idx = idx + offset
    if target_idx >= len(calendar):
        # calendar does not reach the target day: the outcome is not yet
        # knowable — not matured (no label), never a truncated window.
        return None
    target_date = calendar[target_idx]
    if target_date > as_of or (target_date == as_of and not as_of_market_closed):
        return None  # 未到期不出标签

    # --- full calendar window (needs +max_roll coverage) ---------------
    try:
        window = resolve_horizon_calendar_window(
            signal_date=signal_date,
            horizon=horizon,
            trading_days=calendar,
            as_of=as_of,
            as_of_market_closed=as_of_market_closed,
        )
    except Exception:
        return _typed_row(record, horizon, STATUS_CALENDAR_INSUFFICIENT,
                          signal_date=signal_date)

    # --- canonical symbol (V-03/DAV-800 rules) --------------------------
    raw_symbol = record.get("symbol")
    canon = canonicalize_symbol(raw_symbol)
    if canon.status.value == "unmappable":
        return _typed_row(record, horizon, STATUS_SYMBOL_UNMAPPABLE,
                          signal_date=signal_date, window=window,
                          symbol_raw=raw_symbol)
    if canon.status.value == "excluded_bse":
        # BSE codes are excluded by the project stock-pool rule: typed
        # status only, never an evaluated label.
        return _typed_row(record, horizon, STATUS_SYMBOL_EXCLUDED_BSE,
                          signal_date=signal_date, window=window,
                          symbol_raw=raw_symbol)
    symbol = canon.canonical_symbol or str(raw_symbol)

    entry_date = window.executable_entry_date
    end_idx = min(idx + offset + window.max_roll_days + 1, len(calendar) - 1)
    fetch_start, fetch_end = signal_date, calendar[end_idx]

    # Stock bars over the whole consumable window (entry .. roll band).
    stock_bars = market.stock_window(symbol, fetch_start, fetch_end)
    if not stock_bars:
        return _typed_row(record, horizon, STATUS_STOCK_BARS_UNAVAILABLE,
                          signal_date=signal_date, window=window,
                          canonical_symbol=symbol, symbol_raw=raw_symbol)

    # Settlement (P2 single truth source): T+1 open entry, roll over
    # suspension/limit-lock within max_roll, long-side direction.
    try:
        label = resolve_horizon_return_label(
            symbol=symbol,
            signal_date=signal_date,
            horizon=horizon,
            trading_days=calendar,
            as_of=as_of,
            as_of_market_closed=as_of_market_closed,
            price_basis=price_basis,
            return_type=return_type,
            direction="BUY",
            bar_data=stock_bars,
        )
    except Exception:
        return _typed_row(record, horizon, STATUS_LABEL_RESOLUTION_FAILED,
                          signal_date=signal_date, window=window,
                          canonical_symbol=symbol, symbol_raw=raw_symbol)

    outcome_status = str(label.get("outcome_status"))
    actual_exit_date = label.get("actual_exit_date")
    entry_price = label.get("entry_price")
    exit_price = label.get("exit_price")

    # Prediction basis (no roll) over the same entry.
    prediction = _settle_prediction_leg(stock_bars, entry_date, target_date)
    pred_ret = prediction.get("return_pct")

    # SW2021 L1 leg: membership at T, same windows as the stock
    # (prediction: entry->target; execution: entry->actual exit).
    sw_leg: dict[str, Any] = {"membership_status": "gap"}
    sw_index_code: Optional[str] = None
    sw_bars_by_code: Dict[str, Mapping[str, Mapping[str, float]]] = {}
    try:
        sw_leg = resolve_sw_benchmark_leg(
            symbol=symbol,
            signal_date=signal_date,
            prediction_window=(entry_date, target_date)
            if prediction.get("status") == "evaluated" else None,
            execution_window=(entry_date, str(actual_exit_date))
            if actual_exit_date else None,
            sw_daily_fetcher=market.sw_daily_fetcher(),
            membership_resolver=market.membership_resolver(),
        )
    except Exception:
        sw_leg = {"membership_status": "gap",
                  "membership_gap_note": "sw_leg_exception"}
    if sw_leg.get("index_code"):
        sw_index_code = str(sw_leg["index_code"])
        try:
            fetched = market.sw_daily_fetcher()(
                sw_index_code, entry_date,
                str(actual_exit_date or target_date))
            sw_bars_by_code[sw_index_code] = {d: dict(v)
                                              for d, v in fetched.items()}
        except Exception:
            sw_bars_by_code = {}

    # CSI 300 leg over the execution window (V-03b secondary benchmark).
    bmk_bars: Dict[str, dict] = {}
    hs300_leg: dict[str, Any] = {"status": "not_evaluated"}
    if actual_exit_date:
        try:
            bmk_bars = market.benchmark_window(entry_date,
                                               str(actual_exit_date))
            hs300_leg = _benchmark_leg(bmk_bars, entry_date,
                                       str(actual_exit_date))
        except Exception:
            hs300_leg = {"status": "benchmark_unavailable"}

    # Direction (ledger horizon judgement; DAV-1498 bucket).
    hz_judgement = (record.get("horizons") or {}).get(horizon) or {}
    direction_raw = hz_judgement.get("direction")
    if direction_raw is None:
        direction_raw = record.get("direction_top")
    bucket = _direction_bucket(direction_raw)

    return_pct = label.get("return_pct")
    return_pct = float(return_pct) if isinstance(return_pct, (int, float)) else None

    # Excess legs. sw r_sw is a decimal simple return -> convert to percent.
    sw_pred = sw_leg.get("prediction") or {}
    sw_exec = sw_leg.get("execution") or {}
    sw_pred_pct = (float(sw_pred["r_sw"]) * 100.0
                   if isinstance(sw_pred.get("r_sw"), (int, float)) else None)
    sw_exec_pct = (float(sw_exec["r_sw"]) * 100.0
                   if isinstance(sw_exec.get("r_sw"), (int, float)) else None)
    hs300_ret = hs300_leg.get("return_pct")
    hs300_ret = float(hs300_ret) if isinstance(hs300_ret, (int, float)) else None

    def _excess(stock_ret: Optional[float], bench_ret: Optional[float]) -> Optional[float]:
        if stock_ret is None or bench_ret is None:
            return None
        return round(stock_ret - bench_ret, 4)

    sw_excess_exec = _excess(return_pct, sw_exec_pct)
    hs300_excess = _excess(return_pct, hs300_ret)
    sw_excess_pred = _excess(float(pred_ret) if isinstance(pred_ret, (int, float)) else None,
                             sw_pred_pct)
    pred_ret_f = float(pred_ret) if isinstance(pred_ret, (int, float)) else None
    y_rel_prediction = (bool(pred_ret_f - sw_pred_pct > 0)
                        if (pred_ret_f is not None and sw_pred_pct is not None)
                        else None)

    # Direction hit: ledger sign applied when reading the long-side return.
    if return_pct is None or bucket in ("中性", "无结论"):
        direction_hit: Optional[bool] = None
        signed_ret: Optional[float] = None
    elif bucket == "看多":
        direction_hit = bool(return_pct > 0)
        signed_ret = round(return_pct, 4)
    else:  # 看空
        direction_hit = bool(return_pct < 0)
        signed_ret = round(-return_pct, 4)

    input_sha256 = _compute_input_hash(
        report_id=record.get("report_id"),
        symbol=symbol,
        signal_date=signal_date,
        horizon=horizon,
        eval_offset=int(window.eval_offset),
        ledger_record_sha256=_record_identity(record),
        price_basis=price_basis,
        return_type=return_type,
        direction_raw=direction_raw,
        stock_bars=stock_bars,
        benchmark_bars=bmk_bars,
        sw_bars=sw_bars_by_code,
        sw_index_code=sw_index_code,
    )

    row = {
        "pipeline": PIPELINE_VERSION,
        "report_id": record.get("report_id"),
        "symbol": symbol,
        "symbol_raw": str(raw_symbol) if raw_symbol is not None else None,
        "signal_date": signal_date,
        "horizon": horizon,
        "eval_offset": int(window.eval_offset),
        "ledger_record_sha256": _record_identity(record),
        "timing_class": record.get("timing_class"),
        "input_pit_status": record.get("input_pit_status"),
        "backfilled": bool(record.get("backfilled")),
        "as_of": as_of,
        "as_of_market_closed": as_of_market_closed,
        "price_basis": price_basis,
        "return_type": return_type,
        "direction_raw": direction_raw,
        "direction_bucket": bucket,
        "outcome_status": outcome_status,
        "entry_date": entry_date,
        "target_date": target_date,
        "actual_exit_date": actual_exit_date,
        "roll_days_used": int(label.get("roll_days_used") or 0),
        "entry_price": round(float(entry_price), 4) if isinstance(entry_price, (int, float)) else None,
        "exit_price": round(float(exit_price), 4) if isinstance(exit_price, (int, float)) else None,
        "return_pct": round(return_pct, 4) if return_pct is not None else None,
        "direction_signed_return_pct": signed_ret,
        "direction_hit": direction_hit,
        "prediction": prediction,
        "execution": {
            "status": outcome_status,
            "entry_date": entry_date,
            "exit_date": actual_exit_date,
            "entry_price": round(float(entry_price), 4) if isinstance(entry_price, (int, float)) else None,
            "exit_price": round(float(exit_price), 4) if isinstance(exit_price, (int, float)) else None,
            "return_pct": round(return_pct, 4) if return_pct is not None else None,
        },
        "hs300": hs300_leg,
        "hs300_excess_pct": hs300_excess,
        "sw": {
            "membership_status": sw_leg.get("membership_status"),
            "index_code": sw_index_code,
            "index_name": sw_leg.get("index_name"),
            "membership_gap_note": sw_leg.get("membership_gap_note"),
            "prediction": {
                "status": sw_pred.get("status"),
                "window_start": sw_pred.get("window_start"),
                "window_end": sw_pred.get("window_end"),
                "return_pct": round(sw_pred_pct, 4) if sw_pred_pct is not None else None,
            },
            "execution": {
                "status": sw_exec.get("status"),
                "window_start": sw_exec.get("window_start"),
                "window_end": sw_exec.get("window_end"),
                "return_pct": round(sw_exec_pct, 4) if sw_exec_pct is not None else None,
            },
        },
        "sw_excess_pct": sw_excess_exec,
        "sw_excess_pct_prediction": sw_excess_pred,
        "y_rel_prediction": y_rel_prediction,
        "input_sha256": input_sha256,
    }
    return row


# ---------------------------------------------------------------------------
# label file IO (append-only, idempotent)


def _label_key(row: Mapping[str, Any]) -> Tuple[Any, str]:
    return (row.get("report_id"), str(row.get("horizon")))


def _load_existing_keys(label_path: Path) -> set:
    """{(report_id, horizon)} already present in an existing label file."""
    out: set = set()
    if not label_path.exists():
        return out
    with open(label_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PipelineError(
                    f"label file {label_path} line {lineno} unreadable: "
                    f"{exc}") from exc
            out.add(_label_key(obj))
    return out


def _append_rows(label_path: Path, rows: Iterable[dict]) -> int:
    """Append rows (ledger order), one write per row; fsync at the end."""
    n = 0
    with open(label_path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False,
                                separators=(",", ":")) + "\n")
            n += 1
        fh.flush()
        os.fsync(fh.fileno())
    return n


# ---------------------------------------------------------------------------
# commands


def _resolve_as_of(args: argparse.Namespace) -> str:
    if args.as_of:
        as_of = _parse_signal_date(args.as_of)
        if as_of is None:
            raise PipelineError(f"--as-of not a valid date: {args.as_of!r}")
        return as_of
    try:
        from tradingagents.dataflows.trade_calendar import cn_today_str

        return cn_today_str()
    except Exception as exc:
        raise PipelineError(
            f"as-of 解析失败（交易日历取不到，报错退出）: {exc}") from exc


def cmd_run(args: argparse.Namespace) -> int:
    ledger_path = Path(args.ledger)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _log(out_dir, f"run start pipeline={PIPELINE_VERSION} ledger={ledger_path}")

    offline_mode = bool(args.bars_file or args.hs300_file
                        or args.sw_daily_file or args.sw_industry_map)
    if _sw_leg_missing() and not offline_mode:
        # DAV-2034 总控 10-14 令: online run with no TUSHARE_TOKEN can only
        # produce membership_status=gap rows, and label keys are append-only
        # — one unauthenticated run permanently mints gap rows. Fail close
        # BEFORE the ledger pass so zero rows are appended (exit 2). The
        # wrapper's own pre-probe (exit 13) still fires first under launchd;
        # this check is the same gate inside the pipeline for direct/manual
        # invocations that bypass the wrapper.
        raise PipelineError(
            "online mode missing TUSHARE_TOKEN: the SW industry leg "
            "degrades to gap for every row and label keys are append-only "
            "— fail closed, 0 rows written, to prevent permanent gap rows "
            "(offline --*-file mode needs no token)")
    # DAV-2058 🟢-1: after the fail-close gate above, an online run without
    # a token can never reach this point and offline runs never needed one,
    # so ``sw_expected_off`` is now always False. The ``sw_leg_missing``
    # state field is kept (constant False) so dashboards/state consumers
    # keep the observability slot; it no longer flips at runtime.
    sw_expected_off = False

    calendar = _load_calendar(Path(args.calendar_file) if args.calendar_file else None)
    as_of = _resolve_as_of(args)
    as_of_closed = not bool(args.as_of_market_open)
    _log(out_dir, f"calendar={len(calendar)} days "
                  f"[{calendar[0]}..{calendar[-1]}] as_of={as_of} "
                  f"market_closed={as_of_closed}")

    market = MarketData(
        calendar=calendar,
        bars_file=Path(args.bars_file) if args.bars_file else None,
        hs300_file=Path(args.hs300_file) if args.hs300_file else None,
        sw_daily_file=Path(args.sw_daily_file) if args.sw_daily_file else None,
        sw_industry_map=Path(args.sw_industry_map) if args.sw_industry_map else None,
        benchmark_symbol=args.benchmark_symbol,
    )

    horizons = tuple(h.strip() for h in args.horizons.split(",") if h.strip())
    for h in horizons:
        if h not in HORIZONS:
            raise PipelineError(f"unsupported horizon {h!r}; expected {HORIZONS}")

    existing = {h: _load_existing_keys(out_dir / LABEL_FILE_NAMES[h])
                for h in horizons}

    # Pass 1 (streaming, no retention): every ledger line must parse before
    # a single label row is appended — a corrupt ledger aborts the run with
    # zero output, never a partially-labelled file.
    for _lineno, _rec in _iter_ledger_records(ledger_path):
        pass

    counts: Dict[str, int] = {
        "ledger_lines": 0,
        "excluded_other_user": 0,
        "excluded_invalid_signal_date": 0,
        "not_matured": 0,
        "labeled": 0,
        "typed_status": 0,
        "skipped_existing": 0,
    }
    per_horizon_appended: Dict[str, int] = {h: 0 for h in horizons}
    per_horizon_status: Dict[str, Dict[str, int]] = {h: {} for h in horizons}
    per_horizon_sw_gap: Dict[str, int] = {h: 0 for h in horizons}

    for lineno, record in _iter_ledger_records(ledger_path):
        counts["ledger_lines"] += 1
        if str(record.get("user_id")) != str(args.user_id):
            counts["excluded_other_user"] += 1
            continue
        signal_iso = _parse_signal_date(
            record.get("signal_date") or record.get("trade_date"))
        if signal_iso is None:
            counts["excluded_invalid_signal_date"] += 1
            continue
        for horizon in horizons:
            key = (record.get("report_id"), horizon)
            if key in existing[horizon]:
                counts["skipped_existing"] += 1
                continue
            row = compute_label_row(
                record,
                horizon=horizon,
                calendar=calendar,
                as_of=as_of,
                as_of_market_closed=as_of_closed,
                market=market,
                price_basis=args.price_basis,
                return_type=args.return_type,
            )
            if row is None:
                counts["not_matured"] += 1
                continue
            if row.get("outcome_status") == OutcomeStatus.EVALUATED_OK.value:
                counts["labeled"] += 1
            else:
                counts["typed_status"] += 1
            status = str(row.get("outcome_status"))
            per_horizon_status[horizon][status] = (
                per_horizon_status[horizon].get(status, 0) + 1)
            if _row_sw_gap(row):
                per_horizon_sw_gap[horizon] += 1
            n = _append_rows(out_dir / LABEL_FILE_NAMES[horizon], (row,))
            per_horizon_appended[horizon] += n
            existing[horizon].add(key)

    ledger_sha = _sha256_file(ledger_path)
    file_digests = {
        LABEL_FILE_NAMES[h]: {
            "appended": per_horizon_appended[h],
            "sw_gap_appended": per_horizon_sw_gap[h],
            "sha256": (_sha256_file(out_dir / LABEL_FILE_NAMES[h])
                       if (out_dir / LABEL_FILE_NAMES[h]).exists() else None),
            "status_counts": per_horizon_status[h],
        }
        for h in horizons
    }
    sw_gap_note = "; ".join(
        f"{LABEL_FILE_NAMES[h]} sw_gap={per_horizon_sw_gap[h]}/"
        f"{per_horizon_appended[h]}" for h in horizons)
    if any(per_horizon_sw_gap[h] == per_horizon_appended[h] > 0
           for h in horizons):
        # fail-loud in the log + state, even though run exits 0: verify's
        # gap-ratio check is the gate that turns this into a timer alert.
        _log(out_dir, f"WARN all appended rows have sw membership gap "
                      f"({sw_gap_note}) — verify will fail on this file")
    state = {
        "pipeline": PIPELINE_VERSION,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ledger_path": str(ledger_path),
        "ledger_sha256": ledger_sha,
        "user_id": args.user_id,
        "as_of": as_of,
        "as_of_market_closed": as_of_closed,
        "calendar_days": len(calendar),
        "calendar_source": (str(args.calendar_file) if args.calendar_file
                            else "require_cn_trade_dates"),
        "price_basis": args.price_basis,
        "return_type": args.return_type,
        "offline": market.offline,
        "sw_leg_missing": sw_expected_off,
        "counts": counts,
        "files": file_digests,
    }
    state_path = out_dir / STATE_FILE_NAME
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n",
                   "utf-8")
    tmp.replace(state_path)

    _log(out_dir, "run done " + json.dumps(counts, ensure_ascii=False))
    print(json.dumps({"counts": counts,
                      "files": {LABEL_FILE_NAMES[h]: per_horizon_appended[h]
                                for h in horizons},
                      "as_of": as_of}, ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    ok = True
    for h in HORIZONS:
        path = out_dir / LABEL_FILE_NAMES[h]
        if not path.exists():
            print(f"OK: {path.name} absent (nothing labelled yet)")
            continue
        keys: Dict[Tuple[Any, str], int] = {}
        statuses: Dict[str, int] = {}
        sw_gaps = 0
        # DAV-2043 🟡: the gap-ratio denominator counts only rows that
        # actually went through SW-leg resolution (evaluated rows + typed
        # rows from mid-pipeline). Pre-SW typed rows are structurally gap
        # and would otherwise false-positive a dead-Tushare alarm on a
        # BSE/suspension-dense night.
        sw_eligible = 0
        n = 0
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                n += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"FAIL: {path.name} line {lineno} unreadable: {exc}",
                          file=sys.stderr)
                    ok = False
                    continue
                key = _label_key(obj)
                keys[key] = keys.get(key, 0) + 1
                status = str(obj.get("outcome_status"))
                statuses[status] = statuses.get(status, 0) + 1
                if status not in _PRE_SW_TYPED_STATUSES:
                    sw_eligible += 1
                    if _row_sw_gap(obj):
                        sw_gaps += 1
        dupes = {k: c for k, c in keys.items() if c > 1}
        if dupes:
            print(f"FAIL: {path.name} has duplicate keys: {dupes}",
                  file=sys.stderr)
            ok = False
        if sw_eligible > 0 and sw_gaps == sw_eligible:
            # DAV-2041 🟡-1: an all-gap SW leg means the Tushare route was
            # dead for the whole run (bad token or equivalent) — those
            # keys are append-only and can never be re-labelled, so the
            # timer MUST fail loud here instead of staying green.
            print(f"FAIL: {path.name} all {sw_gaps} sw-eligible rows have "
                  f"sw.membership_status=gap — SW industry leg degraded "
                  f"(bad TUSHARE_TOKEN or tushare route down?); "
                  f"labels are append-only and will never be rewritten",
                  file=sys.stderr)
            ok = False
        elif sw_eligible > 0 and sw_gaps / sw_eligible >= 0.9:
            print(f"FAIL: {path.name} {sw_gaps}/{sw_eligible} sw-eligible rows "
                  f"have sw.membership_status=gap (>=90%) — SW industry leg "
                  f"degraded; labels are append-only and will never be "
                  f"rewritten", file=sys.stderr)
            ok = False
        elif sw_gaps:
            print(f"WARN: {path.name} {sw_gaps}/{sw_eligible} sw-eligible "
                  f"rows have sw.membership_status=gap")
        digest = _sha256_file(path)
        print(f"{'OK' if ok else 'FAIL'}: {path.name} lines={n} "
              f"unique_keys={len(keys)} sw_gaps={sw_gaps}/"
              f"{sw_eligible} sha256={digest}")
        print(f"  status_counts={json.dumps(statuses, ensure_ascii=False)}")
    state_path = out_dir / STATE_FILE_NAME
    if state_path.exists():
        state = json.loads(state_path.read_text("utf-8"))
        for h in HORIZONS:
            name = LABEL_FILE_NAMES[h]
            recorded = (state.get("files") or {}).get(name, {}).get("sha256")
            path = out_dir / name
            if recorded and path.exists() and recorded != _sha256_file(path):
                print(f"FAIL: {name} digest drifted from last run state",
                      file=sys.stderr)
                ok = False
    return EXIT_OK if ok else EXIT_VERIFY_FAIL


def cmd_status(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    for h in HORIZONS:
        path = out_dir / LABEL_FILE_NAMES[h]
        if not path.exists():
            print(f"{path.name}: absent")
            continue
        n = sum(1 for line in open(path, "r", encoding="utf-8") if line.strip())
        print(f"{path.name}: {n} rows sha256={_sha256_file(path)}")
    state_path = out_dir / STATE_FILE_NAME
    if state_path.exists():
        state = json.loads(state_path.read_text("utf-8"))
        print(json.dumps({"run_at": state.get("run_at"),
                          "as_of": state.get("as_of"),
                          "counts": state.get("counts")},
                         ensure_ascii=False, indent=1))
    else:
        print("label_run_state.json: absent")
    return EXIT_OK


# ---------------------------------------------------------------------------
# CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DAV-1875 expiry return-label pipeline over the P4 ledger")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="append matured T+10/T+40 labels")
    run.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    run.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    run.add_argument("--user-id", default=DEFAULT_USER_ID,
                     help="fixed P4 account (default: ledger writer's account)")
    run.add_argument("--as-of", default=None,
                     help="evaluation base date YYYY-MM-DD (default: CN today)")
    run.add_argument("--as-of-market-open", action="store_true",
                     help="as-of market still open (default: closed)")
    run.add_argument("--calendar-file", default=None,
                     help="trading-calendar file (one date per line); "
                          "default: live route, fail-close when unavailable")
    run.add_argument("--bars-file", default=None,
                     help="offline stock daily bars JSONL "
                          "{symbol,date,open,high,low,close,volume,is_suspended}")
    run.add_argument("--hs300-file", default=None,
                     help="offline CSI 300 bars JSONL (same row shape)")
    run.add_argument("--sw-daily-file", default=None,
                     help="offline sw_daily bars JSONL "
                          "{index_code,date,open,high,low,close}")
    run.add_argument("--sw-industry-map", default=None,
                     help="offline SW2021 L1 membership map JSON "
                          "{symbol: {code, name}}")
    run.add_argument("--benchmark-symbol", default=DEFAULT_BENCHMARK_SYMBOL)
    run.add_argument("--horizons", default="short,medium")
    run.add_argument("--price-basis", default=PRICE_BASIS_VENDOR_QFQ,
                     choices=[PRICE_BASIS_VENDOR_QFQ, "raw"],
                     help="price basis declaration (vendor_qfq = 前复权 daily K)")
    run.add_argument("--return-type", default=ReturnType.PRICE_RETURN.value,
                     choices=[ReturnType.PRICE_RETURN.value,
                              ReturnType.TOTAL_RETURN.value])
    run.set_defaults(func=cmd_run)

    verify = sub.add_parser("verify", help="check label file integrity")
    verify.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    verify.set_defaults(func=cmd_verify)

    status = sub.add_parser("status", help="label file summary")
    status.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    status.set_defaults(func=cmd_status)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except PipelineError as exc:
        print(f"FAIL-CLOSE: {exc}", file=sys.stderr)
        return EXIT_FAIL_CLOSED


if __name__ == "__main__":
    raise SystemExit(main())
