"""SW2021 L1 industry index benchmark leg (P2 / DAV-1479, second step).

Industry-relative return leg for the unified return-label contract:
- Membership: point-in-time SW2021 L1 via ``resolve_sw_l1_benchmark`` (DAV-1453 口径A).
- Prices: Tushare ``sw_daily`` index daily bars (never ``index_daily`` — Tushare
  officially excludes SW index quotes there).

Design rules:
1. Fail-closed: any membership or price gap yields ``status`` != ``ok`` and
   ``r_sw`` = None; callers record the typed status, never fabricate numbers.
2. No credentials here: token is read only inside ``_query_tushare_api`` from
   the environment; never logged or persisted.
3. Deterministic and injectable: ``sw_daily_fetcher`` can be replaced by a
   fixture callable in tests (signature ``(index_code, start_date, end_date)``
   -> mapping of ISO date -> bar dict).
4. Caching of raw vendor rows lives outside the repository (caller-provided
   ``cache`` mapping or the process-local in-memory cache); raw market rows
   are never committed (D-040).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from collections import OrderedDict
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from tradingagents.dataflows.providers.industry_linkage_provider import _query_tushare_api
from tradingagents.dataflows.sw_industry import resolve_sw_l1_benchmark

logger = logging.getLogger(__name__)

_SW_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close"
_GAP_PREFIX = "【数据获取失败】申万一级指数行情："
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SwIndexWindowReturn:
    """Industry-index return over one window, plus its typed status."""

    status: str  # "ok" | typed gap reason
    index_code: Optional[str] = None
    index_name: Optional[str] = None
    window_start: Optional[str] = None  # entry-side date (open price used)
    window_end: Optional[str] = None    # exit-side date (close price used)
    index_entry_price: Optional[float] = None
    index_exit_price: Optional[float] = None
    r_sw: Optional[float] = None        # simple return (not percent)
    gap_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "index_code": self.index_code,
            "index_name": self.index_name,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "index_entry_price": self.index_entry_price,
            "index_exit_price": self.index_exit_price,
            "r_sw": self.r_sw,
            "gap_note": self.gap_note,
        }


# Process-local cache for sw_daily window frames: (index_code, start, end) -> {date: bar}.
# DAV-1493: bounded LRU — unbounded growth under long-running measurement runs
# wastes memory and keys carrying exact window bounds rarely repeat. The cap
# only controls retention; fail-closed semantics are unchanged.
_SW_DAILY_MEMO_MAX_ENTRIES: int = 64
_SW_DAILY_MEMO: "OrderedDict[Tuple[str, str, str], Dict[str, Dict[str, Any]]]" = OrderedDict()


def _memo_get(key: Tuple[str, str, str]) -> Optional[Dict[str, Dict[str, Any]]]:
    """LRU lookup: refreshes recency; returns None on miss (distinct from a
    cached ``{}`` empty-window result, which is a valid negative cache hit)."""
    try:
        value = _SW_DAILY_MEMO.pop(key)
    except KeyError:
        return None
    _SW_DAILY_MEMO[key] = value
    return value


def _memo_put(key: Tuple[str, str, str], value: Dict[str, Dict[str, Any]]) -> None:
    _SW_DAILY_MEMO[key] = value
    _SW_DAILY_MEMO.move_to_end(key)
    while len(_SW_DAILY_MEMO) > _SW_DAILY_MEMO_MAX_ENTRIES:
        _SW_DAILY_MEMO.popitem(last=False)  # evict least-recently-used


def _iso_to_tushare(d: str) -> str:
    return d.replace("-", "")


def _tushare_to_iso(d: Any) -> str:
    s = str(d).strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


def default_sw_daily_fetcher(
    index_code: str, start_date: str, end_date: str
) -> Dict[str, Dict[str, Any]]:
    """Fetch sw_daily rows for ``index_code`` over [start_date, end_date] (inclusive).

    Returns {ISO date: {"open": float, "high": float, "low": float, "close": float}}.
    Raises ValueError with a deterministic reason id on any failure.
    """
    key = (index_code, _iso_to_tushare(start_date), _iso_to_tushare(end_date))
    hit = _memo_get(key)
    if hit is not None:
        return hit

    frame, category, _note = _query_tushare_api(
        "sw_daily",
        ts_code=index_code,
        fields=_SW_DAILY_FIELDS,
        params={"start_date": key[1], "end_date": key[2]},
    )
    if category == "empty_rows":
        _memo_put(key, {})
        return {}
    if category or frame is None:
        # Never echo provider notes: they may contain endpoint/credential material.
        raise ValueError("sw_daily_unavailable")
    required = set(_SW_DAILY_FIELDS.split(","))
    if not required.issubset(set(frame.columns)):
        raise ValueError("sw_daily_schema_invalid")

    out: Dict[str, Dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        if str(row.get("ts_code", "")).strip() != index_code:
            raise ValueError("sw_daily_symbol_mismatch")
        d_iso = _tushare_to_iso(row.get("trade_date"))
        if not _ISO_DATE_RE.match(d_iso):
            raise ValueError("sw_daily_date_invalid")
        try:
            bar = {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        except (TypeError, ValueError, KeyError):
            raise ValueError("sw_daily_price_invalid") from None
        if d_iso in out and out[d_iso] != bar:
            raise ValueError("sw_daily_conflicting_rows")
        out[d_iso] = bar
    _memo_put(key, out)
    return out


def compute_sw_index_window_return(
    *,
    index_code: str,
    index_name: Optional[str],
    window_start: str,
    window_end: str,
    sw_daily_fetcher: Optional[
        Callable[[str, str, str], Mapping[str, Mapping[str, float]]]
    ] = None,
) -> SwIndexWindowReturn:
    """Return of one SW index over [window_start, window_end].

    Mirrors the stock-side price convention of the frozen spec:
    entry side uses the index OPEN on ``window_start`` (T+1 open execution),
    exit side uses the index CLOSE on ``window_end``.
    """
    fetcher = sw_daily_fetcher or default_sw_daily_fetcher
    if (
        not isinstance(window_start, str)
        or not isinstance(window_end, str)
        or not _ISO_DATE_RE.match(window_start)
        or not _ISO_DATE_RE.match(window_end)
        or window_end < window_start
    ):
        return SwIndexWindowReturn(
            status="window_invalid",
            index_code=index_code,
            index_name=index_name,
            window_start=window_start if isinstance(window_start, str) else None,
            window_end=window_end if isinstance(window_end, str) else None,
        )
    try:
        bars = fetcher(index_code, window_start, window_end)
    except ValueError as exc:
        reason = str(exc) if re.fullmatch(r"[A-Za-z0-9_]+", str(exc)) else "sw_daily_unavailable"
        return SwIndexWindowReturn(
            status=reason,
            index_code=index_code,
            index_name=index_name,
            window_start=window_start,
            window_end=window_end,
            gap_note=_GAP_PREFIX + reason,
        )
    except Exception:
        return SwIndexWindowReturn(
            status="sw_daily_unavailable",
            index_code=index_code,
            index_name=index_name,
            window_start=window_start,
            window_end=window_end,
            gap_note=_GAP_PREFIX + "sw_daily_unavailable",
        )

    start_bar = bars.get(window_start)
    end_bar = bars.get(window_end)
    if start_bar is None or end_bar is None:
        reason = "sw_daily_missing_start" if start_bar is None else "sw_daily_missing_end"
        return SwIndexWindowReturn(
            status=reason,
            index_code=index_code,
            index_name=index_name,
            window_start=window_start,
            window_end=window_end,
            gap_note=_GAP_PREFIX + reason,
        )
    entry_px = float(start_bar["open"])
    exit_px = float(end_bar["close"])
    if entry_px <= 0 or exit_px <= 0:
        return SwIndexWindowReturn(
            status="sw_daily_price_invalid",
            index_code=index_code,
            index_name=index_name,
            window_start=window_start,
            window_end=window_end,
            index_entry_price=entry_px if entry_px > 0 else None,
            index_exit_price=exit_px if exit_px > 0 else None,
            gap_note=_GAP_PREFIX + "sw_daily_price_invalid",
        )
    return SwIndexWindowReturn(
        status="ok",
        index_code=index_code,
        index_name=index_name,
        window_start=window_start,
        window_end=window_end,
        index_entry_price=round(entry_px, 4),
        index_exit_price=round(exit_px, 4),
        r_sw=round((exit_px - entry_px) / entry_px, 6),
    )


def resolve_sw_benchmark_leg(
    *,
    symbol: str,
    signal_date: str,
    prediction_window: Optional[Tuple[str, str]] = None,
    execution_window: Optional[Tuple[str, str]] = None,
    sw_daily_fetcher: Optional[
        Callable[[str, str, str], Mapping[str, Mapping[str, float]]]
    ] = None,
    membership_resolver: Optional[
        Callable[[str, str], Tuple[Optional[dict], Optional[str]]]
    ] = None,
) -> Dict[str, Any]:
    """Industry benchmark leg for one sample (P2 / DAV-1453 口径A).

    - Membership resolved strictly at signal_date T via resolve_sw_l1_benchmark.
    - Computes index returns for each provided window (prediction & execution).
    - Returns a serialisable dict; every failure is a typed status, never raises
      on data gaps (invalid symbol shapes still propagate as membership gap).
    """
    resolver = membership_resolver or resolve_sw_l1_benchmark
    try:
        benchmark, gap = resolver(symbol, signal_date)
    except Exception:
        benchmark, gap = None, "membership_resolver_exception"
    leg: Dict[str, Any] = {
        "membership_status": "ok" if benchmark else "gap",
        "index_code": None,
        "index_name": None,
        "membership_gap_note": gap,
        "prediction": None,
        "execution": None,
    }
    if not benchmark:
        return leg
    leg["index_code"] = benchmark.get("code")
    leg["index_name"] = benchmark.get("name")

    def _one(window: Optional[Tuple[str, str]]) -> Optional[Dict[str, Any]]:
        if window is None:
            return None
        start, end = window
        return compute_sw_index_window_return(
            index_code=leg["index_code"],
            index_name=leg["index_name"],
            window_start=start,
            window_end=end,
            sw_daily_fetcher=sw_daily_fetcher,
        ).to_dict()

    leg["prediction"] = _one(prediction_window)
    leg["execution"] = _one(execution_window)
    return leg
