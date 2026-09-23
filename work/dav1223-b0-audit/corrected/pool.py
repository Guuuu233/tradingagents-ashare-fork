"""DAV-1225 corrected snapshot price-pool parser.

根因修复：DAV-1223 B0 的拆账把 snapshot ``stock_data`` CSV 按**固定列号**解析，
而实际表头为 ``date,low,close,volume,open,high``（列序非 canonical），导致
open/high/low/close 错位、volume 被当价格进池，pool_hits 大面积假命中。

本模块一律按 header 名称取列，并对每个 snapshot 打印实际表头与可用字段清单；
date/open/high/low/close 任一缺失即 fail-closed（抛 SnapshotPoolError），
绝不生成空价格池后继续分类。

产出的 pool 为「字段级 provenance」结构：每个可用值都绑定一个具名字段
（``stock_data.<date>.<field>`` / ``stock_data.latest.<field>`` /
``indicators.<name>`` / ``derived.limit_up`` / ``derived.limit_down``），
供 source-backed 判定与 W4 严格桥接使用。纯数值相等只记 coincidence。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

REQUIRED_OHLC_COLUMNS = ("date", "open", "high", "low", "close")


class SnapshotPoolError(RuntimeError):
    """fail-closed：snapshot 缺少必需列或结构不可用。"""


@dataclass
class NamedValue:
    field: str          # 具名字段，如 stock_data.2026-08-14.close
    value: float
    basis_hint: str = "vendor_qfq"   # stock_data 头部声明的 price_basis
    as_of: Optional[str] = None      # bar 日期或 None（指标）


@dataclass
class SnapshotPool:
    sample: str
    symbol: str
    trade_date: str
    stock_data_header: List[str] = field(default_factory=list)
    bars: List[Dict[str, Any]] = field(default_factory=list)   # header 命名的 OHLCV 行
    indicators: Dict[str, float] = field(default_factory=dict)
    named_values: List[NamedValue] = field(default_factory=list)
    stock_data_basis: Optional[str] = None

    # ---- lookup helpers -------------------------------------------------
    def fields_matching(self, value: float, tol: float = 5e-3) -> List[NamedValue]:
        return [nv for nv in self.named_values if abs(nv.value - value) <= tol]

    def bar_by_date(self, date: str) -> Optional[Dict[str, Any]]:
        for b in self.bars:
            if b.get("date") == date:
                return b
        return None

    def price_range(self) -> Optional[Tuple[float, float]]:
        lows = [b["low"] for b in self.bars if isinstance(b.get("low"), (int, float))]
        highs = [b["high"] for b in self.bars if isinstance(b.get("high"), (int, float))]
        if not lows or not highs:
            return None
        return min(lows), max(highs)


_HEADER_LINE = re.compile(r"^#")
_LIMIT_RATIO_BY_PREFIX = (
    (("300", "301", "688", "689"), 0.20),   # 创业板/科创板 ±20%
    (("8", "4", "92"), 0.30),               # 北交所 ±30%（防御性）
)
_DEFAULT_LIMIT_RATIO = 0.10                 # 主板 ±10%


def _limit_ratio(symbol: str) -> float:
    code = symbol.split(".")[0]
    for prefixes, ratio in _LIMIT_RATIO_BY_PREFIX:
        if code.startswith(prefixes):
            return ratio
    return _DEFAULT_LIMIT_RATIO


def parse_stock_data_text(text: Any, symbol: str) -> Tuple[List[str], List[Dict[str, Any]], Optional[str]]:
    """按 header 名称解析 stock_data CSV。返回 (header, bars, price_basis)。

    fail-closed：缺 date/open/high/low/close 任一列 → SnapshotPoolError。
    """
    if not isinstance(text, str):
        raise SnapshotPoolError(f"{symbol}: stock_data 不是文本（{type(text).__name__}）")
    lines = text.splitlines()
    basis = None
    csv_lines: List[str] = []
    for ln in lines:
        if ln.startswith("#"):
            m = re.search(r"price_basis:\s*(\S+)", ln)
            if m:
                basis = m.group(1)
            continue
        if ln.strip():
            csv_lines.append(ln)
    if not csv_lines:
        raise SnapshotPoolError(f"{symbol}: stock_data 无 CSV 行")
    reader = csv.DictReader(io.StringIO("\n".join(csv_lines)))
    header = list(reader.fieldnames or [])
    missing = [c for c in REQUIRED_OHLC_COLUMNS if c not in header]
    if missing:
        raise SnapshotPoolError(
            f"{symbol}: stock_data 缺必需列 {missing}（实际表头 {header}），fail-closed"
        )
    bars: List[Dict[str, Any]] = []
    for row in reader:
        bar: Dict[str, Any] = {"date": (row.get("date") or "").strip()}
        ok = True
        for c in ("open", "high", "low", "close"):
            raw = (row.get(c) or "").strip()
            try:
                bar[c] = float(raw)
            except ValueError:
                ok = False
                bar[c] = None
        vol = (row.get("volume") or "").strip() if "volume" in header else ""
        try:
            bar["volume"] = float(vol) if vol else None
        except ValueError:
            bar["volume"] = None
        if ok and bar["date"]:
            bars.append(bar)
    if not bars:
        raise SnapshotPoolError(f"{symbol}: stock_data 解析后无有效 OHLC 行")
    return header, bars, basis


def build_pool(sample: str, symbol: str, trade_date: str, snapshot: Dict[str, Any]) -> SnapshotPool:
    pool = SnapshotPool(sample=sample, symbol=symbol, trade_date=trade_date)
    header, bars, basis = parse_stock_data_text(snapshot.get("stock_data"), symbol)
    pool.stock_data_header = header
    pool.bars = bars
    pool.stock_data_basis = basis or "vendor_qfq"

    for b in bars:
        d = b["date"]
        for f in ("open", "high", "low", "close"):
            if isinstance(b.get(f), (int, float)):
                pool.named_values.append(
                    NamedValue(field=f"stock_data.{d}.{f}", value=b[f],
                               basis_hint=pool.stock_data_basis, as_of=d)
                )
    # 末根 bar 的 latest 别名
    last = bars[-1]
    for f in ("open", "high", "low", "close"):
        if isinstance(last.get(f), (int, float)):
            pool.named_values.append(
                NamedValue(field=f"stock_data.latest.{f}", value=last[f],
                           basis_hint=pool.stock_data_basis, as_of=last["date"])
            )
    # 涨跌停价：由末根 bar 收盘 × 板块幅度计算的具名派生字段（字段级 provenance，
    # 不是裸数值巧合；标注 derived.* 以便与普通 bar 字段区分）。
    ratio = _limit_ratio(symbol)
    prev_close = last.get("close")
    if isinstance(prev_close, (int, float)):
        pool.named_values.append(
            NamedValue(field=f"derived.limit_up@{last['date']}",
                       value=round(prev_close * (1 + ratio) + 1e-9, 2),
                       basis_hint=pool.stock_data_basis, as_of=last["date"])
        )
        pool.named_values.append(
            NamedValue(field=f"derived.limit_down@{last['date']}",
                       value=round(prev_close * (1 - ratio) + 1e-9, 2),
                       basis_hint=pool.stock_data_basis, as_of=last["date"])
        )

    ind = snapshot.get("indicators")
    if isinstance(ind, dict):
        for k, v in ind.items():
            if isinstance(v, (int, float)):
                pool.indicators[k] = float(v)
                pool.named_values.append(
                    NamedValue(field=f"indicators.{k}", value=float(v),
                               basis_hint=pool.stock_data_basis, as_of=trade_date)
                )
    return pool


# ---------------------------------------------------------------------------
# 字段级 provenance：从 ref context 里识别「已命名 source field」
# ---------------------------------------------------------------------------

_INDICATOR_ALIASES: List[Tuple[re.Pattern, str]] = [
    # (pattern, indicators key or callable suffix)
    (re.compile(r"(?:10\s*日?\s*EMA|EMA\s*[-_]?\s*10|十\s*日\s*EMA)", re.I), "close_10_ema"),
    (re.compile(r"(?:50\s*日?\s*(?:SMA|均线|MA)|SMA\s*[-_]?\s*50|MA\s*50)", re.I), "close_50_sma"),
    (re.compile(r"(?:200\s*日?\s*(?:SMA|均线|MA)|SMA\s*[-_]?\s*200|MA\s*200|年线)", re.I), "close_200_sma"),
    (re.compile(r"VWMA|成交加权价|成交量加权", re.I), "vwma"),
    (re.compile(r"布林(?:带)?上轨|BOLL\s*上轨|boll_ub", re.I), "boll_ub"),
    (re.compile(r"布林(?:带)?下轨|BOLL\s*下轨|boll_lb", re.I), "boll_lb"),
    (re.compile(r"布林(?:带)?中轨|BOLL\s*中轨|BOLL\b|布林(?:带)?(?!上|下)", re.I), "boll"),
    (re.compile(r"\bATR\b|真实波幅", re.I), "atr"),
    (re.compile(r"\bRSI\b", re.I), "rsi"),
    (re.compile(r"\bMACD\b", re.I), "macd"),
]

_DATE_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?")
_MD_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日")

_OHLC_WORDS = {
    "open": ("开盘", "开于", "开盘价"),
    "close": ("收盘", "收于", "收盘价", "现价报收"),
    "high": ("最高", "高点", "上探", "冲高至"),
    "low": ("最低", "低点", "下探", "回踩"),
}


def named_field_hits(context: str, value: float, pool: SnapshotPool,
                     tol: float = 5e-3) -> List[str]:
    """返回 context 明确指名且值匹配的 pool 字段清单（字段级 provenance）。

    规则（D-034 严格桥接定义）：
    - context 明确 EMA10/VWMA/SMA/BOLL/ATR 等指标名且值匹配对应 frozen indicator；
    - 或明确日期 + OHLC 语义词匹配对应 frozen bar 字段；
    - 或明确涨跌停语义匹配 computed limit 字段。
    其它同值一律不计入（由调用方记 coincidence）。
    """
    hits: List[str] = []
    ctx = context or ""

    for pat, key in _INDICATOR_ALIASES:
        if pat.search(ctx) and key in pool.indicators:
            if abs(pool.indicators[key] - value) <= tol:
                hits.append(f"indicators.{key}")

    # 日期 + OHLC 语义
    dates = [f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
             for m in _DATE_RE.finditer(ctx)]
    year = pool.trade_date[:4] if pool.trade_date else "2026"
    for m in _MD_DATE_RE.finditer(ctx):
        dates.append(f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    for d in dates:
        bar = pool.bar_by_date(d)
        if not bar:
            continue
        for f, words in _OHLC_WORDS.items():
            if any(w in ctx for w in words) and isinstance(bar.get(f), (int, float)):
                if abs(bar[f] - value) <= tol:
                    hits.append(f"stock_data.{d}.{f}")

    # 涨跌停语义
    if re.search(r"涨停", ctx):
        for nv in pool.fields_matching(value, tol):
            if nv.field.startswith("derived.limit_up"):
                hits.append(nv.field)
    if re.search(r"跌停", ctx):
        for nv in pool.fields_matching(value, tol):
            if nv.field.startswith("derived.limit_down"):
                hits.append(nv.field)
    return sorted(set(hits))


# ---------------------------------------------------------------------------
# 自检：缺列 fail-closed
# ---------------------------------------------------------------------------

def selfcheck_missing_column() -> str:
    """构造缺 'close' 列的 stock_data 文本，断言 parse 抛 SnapshotPoolError。"""
    bad = (
        "# Stock data for 000001.SZ\n"
        "# price_basis: vendor_qfq\n"
        "date,open,high,low,volume\n"
        "2026-08-14,10.0,10.5,9.9,12345.0\n"
    )
    try:
        parse_stock_data_text(bad, "000001.SZ")
    except SnapshotPoolError as e:
        return f"OK fail-closed: {e}"
    raise AssertionError("缺 close 列未触发 fail-closed —— 自检失败")
