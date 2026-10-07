"""M2-A 影子组合·成交规则层（DAV-1675，计划 v1.0 §5 M2）。

输入：封存账本记录（``scripts/phase2/daily_snapshot_ledger.py`` 产出的
forward_ledger.jsonl 行）+ 日线行情（Phase-2 缓存 ``daily_by_day`` 逐日 pkl，
列为 ``ts_code, trade_date(YYYYMMDD), open, high, low, close, pre_close, vol``）。

规则（逐条可测）：
  R1  T+1 —— 最早入场日为 signal_date 之后第 1 个交易日（严格索引交易日历，
      禁止自然日加法）。
  R2  涨跌停不可成交 —— 一字板（open==high==low 且触板）在任何一侧都不可成交；
      非一字但开盘贴板的交易日按 ``allow_partial_limit_touch`` 策略决定（默认
      保守 True：贴板即不可成交）。涨跌停价由 pre_close 与板块涨跌幅推导：
      主板 10%、创业板/科创板 20%、北交所 30%（北交所默认被股票池剔除，仍实
      现以保持口径完整）、ST/*ST 一律 5%（PIT 区间判断，上市首日无涨跌幅限
      制，不设限）。浮点比较带 eps 容差。
  R3  停牌顺延 —— 目标交易日无 bar / vol<=0 / 价<=0 视为不可成交，顺延至下
      一交易日重试，最多 ``max_roll_days``；超限仍不可成交 → ``suspension``。
      超限后存在“双边证据”（窗口前有效收盘 + 窗口后有效 bar）判
      ``suspension``，否则判 ``data_missing``。对齐 return_labels / DAV-1547。
  R4  100 股一手 —— 买入数量为 ``floor(cash/(price*(1+buy_rate))/100)*100``；
      不足一手（或现金不足覆盖成本）→ ``insufficient_cash``，禁止 0 股静默
      成交。卖出按手数取整，余股不足一手仍可整仓卖出时一次卖出（卖出不存在
      手数约束，A 股卖出可非整手；为保持账本一致，卖出股数=持仓股数）。
  R5  V-03a 成本 —— 复用 ``tradingagents.eval.v03_return_measure.CostModel``
      冻结费率：佣金 2.5bps（含规费、无额外经手/监管费）、过户费 0.1bps 双边、
      印花税 5bps 卖出单边、滑点 5bps 单边。买入有效价 = open*(1+buy_rate)，
      卖出有效价 = price*(1-sell_rate)；金额口径同样逐笔核算费用明细。

退市边界：持仓窗口内再无新 bar（退市/长期停牌至数据末尾）且无后续 bar 可作
双边证据 → ``delisted_or_unresolved``，持仓标记为未平仓残余，收益不可伪造。

本模块纯函数 + 显式数据注入，不触网、不写生产库。
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from tradingagents.eval.v03_return_measure import CostModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOT_SIZE: int = 100  # A 股一手

LIMIT_RATE_MAIN: float = 0.10
LIMIT_RATE_GEM_STAR: float = 0.20
LIMIT_RATE_BSE: float = 0.30
LIMIT_RATE_ST: float = 0.05

# 涨跌停价推导的浮点容差（pre_close*rate 与行情 OHLC 均为 float64）
LIMIT_EPS: float = 1e-6

_GEM_STAR_PREFIXES = ("300", "301", "302", "688", "689")


def board_limit_rate(symbol: str) -> float:
    """按代码前缀推导板块默认涨跌幅（不含 ST 覆盖）。"""
    code = symbol.strip().upper()
    if code.endswith(".BJ") or code[:3] in ("832", "833", "834", "835", "836",
                                          "837", "838", "839", "920"):
        return LIMIT_RATE_BSE
    if code[:3] in _GEM_STAR_PREFIXES:
        return LIMIT_RATE_GEM_STAR
    return LIMIT_RATE_MAIN


def limit_rate_for_day(symbol: str, is_st: bool) -> float:
    """单日涨跌幅：ST/*ST 一律 5%（覆盖板块默认）。"""
    if is_st:
        return LIMIT_RATE_ST
    return board_limit_rate(symbol)


def limit_prices(pre_close: Optional[float], rate: float) -> tuple[Optional[float], Optional[float]]:
    """由 pre_close 推导当日涨/跌停价；pre_close 非法时返回 (None, None)。

    A 股涨跌停价为 pre_close*(1±rate) 四舍五入到分（0.01 元）。
    """
    if pre_close is None or not math.isfinite(pre_close) or pre_close <= 0:
        return None, None
    return round(pre_close * (1.0 + rate), 2), round(pre_close * (1.0 - rate), 2)


# ---------------------------------------------------------------------------
# Bar & calendar abstractions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyBar:
    """日线 bar。trade_date 统一 ISO YYYY-MM-DD。"""

    trade_date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    pre_close: Optional[float]
    vol: Optional[float]

    @staticmethod
    def _f(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f):
            return None
        return f

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any], trade_date: Optional[str] = None) -> "DailyBar":
        d = trade_date or str(m.get("trade_date", ""))
        # 兼容缓存里 YYYYMMDD 的 trade_date
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return cls(
            trade_date=d,
            open=cls._f(m.get("open")),
            high=cls._f(m.get("high")),
            low=cls._f(m.get("low")),
            close=cls._f(m.get("close")),
            pre_close=cls._f(m.get("pre_close")),
            vol=cls._f(m.get("vol", m.get("volume"))),
        )


def bar_is_suspended(bar: Optional[DailyBar]) -> bool:
    """停牌/不可交易判据：无 bar、量<=0、或开/收盘价缺失非正。"""
    if bar is None:
        return True
    if bar.vol is None or bar.vol <= 0:
        return True
    if (bar.open is None or bar.open <= 0) and (bar.close is None or bar.close <= 0):
        return True
    return False


def bar_has_valid_close(bar: Optional[DailyBar]) -> bool:
    return (
        bar is not None
        and bar.close is not None
        and bar.close > 0
        and bar.vol is not None
        and bar.vol > 0
    )


class OneWordLock(str, Enum):
    """一字板方向（open==high==low==涨停/跌停）。"""

    NONE = "none"
    LIMIT_UP = "limit_up"
    LIMIT_DOWN = "limit_down"


def _detect_lock_with_rate(bar: DailyBar, rate: float) -> OneWordLock:
    """一字板判定：open==high==low 且该价触及按 pre_close 推导的涨/跌停价。

    pre_close 缺失时无法推导涨跌停价，返回 NONE（一字判定 fail-open；
    停牌判定本身 fail-closed，不因缺 pre_close 伪造成交）。
    """
    o, h, l = bar.open, bar.high, bar.low
    if bar.pre_close is None or bar.pre_close <= 0 or h is None or h <= 0:
        return OneWordLock.NONE
    tol = LIMIT_EPS * h
    if abs(o - h) > tol or abs(l - h) > tol:
        return OneWordLock.NONE  # 开过板，不是一字
    lup, ldown = limit_prices(bar.pre_close, rate)
    if lup is not None and h >= lup - tol:
        return OneWordLock.LIMIT_UP
    if ldown is not None and h <= ldown + tol:
        return OneWordLock.LIMIT_DOWN
    return OneWordLock.NONE


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "filled"
    DEFERRED = "deferred"          # 停牌顺延中
    UNEXECUTABLE = "unexecutable"  # 涨跌停锁死等不可成交（重试耗尽或一字板）
    INSUFFICIENT_CASH = "insufficient_cash"
    REJECTED = "rejected"          # 输入非法（非交易日信号、数量非法等）


class PositionExitStatus(str, Enum):
    """持仓退出终态（对齐 return_labels OutcomeStatus 的子集语义）。"""

    PENDING = "pending"                  # 尚未到达退出日
    CLOSED = "closed"                    # 正常成交平仓
    SUSPENSION = "suspension"            # 顺延耗尽且有双边证据
    DATA_MISSING = "data_missing"        # 无 bar 且无法判定停牌
    DELISTED_UNRESOLVED = "delisted_or_unresolved"


@dataclass(frozen=True)
class ExecResult:
    """单笔委托执行结果。"""

    status: OrderStatus
    side: OrderSide
    requested_date: str            # 期望交易日（T+1 或顺延日）
    executed_date: Optional[str]   # 实际成交日（顺延后的）
    price: Optional[float]         # 成交单价（开盘价，未含成本）
    shares: int                    # 成交股数（买入为手的整数倍）
    gross_amount: float = 0.0      # price*shares
    fee: float = 0.0               # 本笔费用（含滑点口径折算金额）
    effective_price: Optional[float] = None  # 含成本有效价（buy*(1+r)/sell*(1-r)）
    roll_days_used: int = 0
    reason: Optional[str] = None


@dataclass
class Position:
    symbol: str
    shares: int
    entry_date: str
    entry_price: float             # 含买入成本的有效单价
    cost_detail: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Market data access (注入式；默认从 Phase-2 缓存读)
# ---------------------------------------------------------------------------


class DailyBarsSource:
    """日线行情来源协议。实现：Phase2 逐日 pkl 缓存目录。

    bar 的 trade_date 输入输出均为 ISO YYYY-MM-DD；缓存文件名与 trade_date
    列为 YYYYMMDD，读取时做格式转换。
    """

    def __init__(self, daily_dir: Path):
        self.daily_dir = Path(daily_dir)
        self._cache: Dict[str, Dict[str, DailyBar]] = {}

    def _load_day(self, iso_date: str) -> Dict[str, DailyBar]:
        if iso_date in self._cache:
            return self._cache[iso_date]
        compact = iso_date.replace("-", "")
        # 缓存文件两种命名均兼容：YYYY-MM-DD.pkl 与 YYYYMMDD.pkl
        path = self.daily_dir / f"{iso_date}.pkl"
        if not path.exists():
            path = self.daily_dir / f"{compact}.pkl"
        out: Dict[str, DailyBar] = {}
        if path.exists():
            import pandas as pd

            df = pd.read_pickle(path)
            for row in df.itertuples(index=False):
                d = str(row.trade_date)
                iso = f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 and d.isdigit() else d
                out[str(row.ts_code)] = DailyBar(
                    trade_date=iso,
                    open=DailyBar._f(row.open),
                    high=DailyBar._f(row.high),
                    low=DailyBar._f(row.low),
                    close=DailyBar._f(row.close),
                    pre_close=DailyBar._f(row.pre_close),
                    vol=DailyBar._f(row.vol),
                )
        self._cache[iso_date] = out
        return out

    def get_bar(self, symbol: str, iso_date: str) -> Optional[DailyBar]:
        return self._load_day(iso_date).get(symbol)


TradingCalendar = Sequence[str]  # 排序 ISO 交易日序列


def next_trading_day(calendar: TradingCalendar, after: str, offset: int = 1) -> Optional[str]:
    """严格索引：after 之后第 offset 个交易日；after 不在日历中按其后插入点计。"""
    idx = bisect.bisect_right(list(calendar), after)
    pos = idx + offset - 1
    if pos >= len(calendar):
        return None
    return calendar[pos]


def calendar_position(calendar: TradingCalendar, d: str) -> int:
    """d 在交易日历中的位置；非交易日返回其后一日的位置（bisect_left 插入点）。"""
    return bisect.bisect_left(list(calendar), d)


# ---------------------------------------------------------------------------
# ST 区间（PIT namechange）
# ---------------------------------------------------------------------------


def build_st_intervals(namechange_rows: Iterable[Mapping[str, Any]]) -> Dict[str, List[tuple[str, str]]]:
    """ts_code -> [(start,end)] ST 名称区间（闭区间，YYYYMMDD 比较）。"""
    out: Dict[str, List[tuple[str, str]]] = {}
    for r in namechange_rows:
        name = str(r.get("name") or "")
        if "ST" not in name:
            continue
        s = str(r.get("start_date") or "00000000")
        e = str(r.get("end_date") or "99999999")
        out.setdefault(str(r.get("ts_code")), []).append((s, e))
    return out


def is_st_on(st_intervals: Dict[str, List[tuple[str, str]]], symbol: str, iso_date: str) -> bool:
    compact = iso_date.replace("-", "")
    for s, e in st_intervals.get(symbol, []):
        if s <= compact <= e:
            return True
    return False


def is_first_listing_day(stock_basic_map: Mapping[str, Mapping[str, Any]],
                         symbol: str, iso_date: str) -> bool:
    """上市首日（无涨跌幅限制）。list_date 命中即 True。"""
    ld = (stock_basic_map.get(symbol) or {}).get("list_date")
    return bool(ld) and str(ld) == iso_date.replace("-", "")


# ---------------------------------------------------------------------------
# 成交规则引擎
# ---------------------------------------------------------------------------


@dataclass
class ExecutionEngine:
    """按 A 股规则执行买入/卖出委托。

    get_bar(symbol, iso_date) -> Optional[DailyBar] 可注入任意行情源；
    trading_calendar 为排序后的 ISO 交易日序列；
    st_intervals / stock_basic_map 为可选 PIT 辅助数据。
    """

    trading_calendar: TradingCalendar
    get_bar: Callable[[str, str], Optional[DailyBar]]
    cost_model: CostModel = field(default_factory=CostModel)
    max_roll_days: int = 5
    allow_partial_limit_touch: bool = True
    st_intervals: Dict[str, List[tuple[str, str]]] = field(default_factory=dict)
    stock_basic_map: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    # -- 内部工具 -----------------------------------------------------------

    def _rate(self, symbol: str, iso_date: str) -> float:
        if is_first_listing_day(self.stock_basic_map, symbol, iso_date):
            return math.inf  # 上市首日无涨跌幅限制 -> 永不触板
        return limit_rate_for_day(symbol, is_st_on(self.st_intervals, symbol, iso_date))

    def _classify_day(self, symbol: str, iso_date: str, side: OrderSide) -> tuple[str, Optional[DailyBar]]:
        """返回 (day_kind, bar)：tradable / suspended_or_missing / locked。"""
        bar = self.get_bar(symbol, iso_date)
        if bar_is_suspended(bar):
            return "suspended_or_missing", bar
        assert bar is not None
        rate = self._rate(symbol, iso_date)
        lock = _detect_lock_with_rate(bar, rate) if rate != math.inf else OneWordLock.NONE
        if lock == OneWordLock.LIMIT_UP and side == OrderSide.BUY:
            return "locked", bar
        if lock == OneWordLock.LIMIT_DOWN and side == OrderSide.SELL:
            return "locked", bar
        # 非一字但开盘贴板
        lup, ldown = limit_prices(bar.pre_close, rate) if rate != math.inf else (None, None)
        if side == OrderSide.BUY and lup is not None and bar.open is not None \
                and bar.open >= lup - LIMIT_EPS * lup:
            if not self.allow_partial_limit_touch:
                return "tradable", bar
            return "locked", bar
        if side == OrderSide.SELL and ldown is not None and bar.open is not None \
                and bar.open <= ldown + LIMIT_EPS * ldown:
            if not self.allow_partial_limit_touch:
                return "tradable", bar
            return "locked", bar
        return "tradable", bar

    # -- 买入 ---------------------------------------------------------------

    def execute_buy(self, symbol: str, signal_date: str, cash: float,
                    entry_offset: int = 1) -> ExecResult:
        """T+1 开盘买入；停牌/锁板顺延至 max_roll_days。"""
        if cash is None or cash <= 0:
            return ExecResult(OrderStatus.REJECTED, OrderSide.BUY, signal_date,
                              None, None, 0, reason="cash<=0")
        first = next_trading_day(self.trading_calendar, signal_date, entry_offset)
        if first is None:
            return ExecResult(OrderStatus.REJECTED, OrderSide.BUY, signal_date,
                              None, None, 0, reason="no_trading_day_after_signal")
        start_pos = calendar_position(self.trading_calendar, first)
        for r in range(self.max_roll_days + 1):
            pos = start_pos + r
            if pos >= len(self.trading_calendar):
                break
            d = self.trading_calendar[pos]
            kind, bar = self._classify_day(symbol, d, OrderSide.BUY)
            if kind != "tradable":
                continue
            assert bar is not None and bar.open is not None and bar.open > 0
            open_price = bar.open
            eff = open_price * (1.0 + self.cost_model.buy_cost_rate)
            shares = math.floor(cash / eff / LOT_SIZE) * LOT_SIZE
            if shares < LOT_SIZE:
                return ExecResult(OrderStatus.INSUFFICIENT_CASH, OrderSide.BUY,
                                  first, None, open_price, 0,
                                  roll_days_used=r,
                                  reason="cash_below_one_lot")
            gross = open_price * shares
            fee = gross * self.cost_model.buy_cost_rate
            return ExecResult(OrderStatus.FILLED, OrderSide.BUY, first, d,
                              open_price, shares, gross, fee, eff, r)
        # 顺延耗尽：区分停牌（有后续 bar 证据）与数据缺失/退市
        reason = self._resolve_no_fill_reason(symbol, start_pos)
        return ExecResult(OrderStatus.UNEXECUTABLE, OrderSide.BUY, first, None,
                          None, 0, roll_days_used=self.max_roll_days, reason=reason)

    # -- 卖出 ---------------------------------------------------------------

    def execute_sell(self, symbol: str, earliest_date: str, shares: int) -> ExecResult:
        """持仓退出：从 earliest_date 起顺延寻找首个可成交日，卖出全部持仓。

        shares<=0 视为非法委托。卖出不需要整手约束（A 股卖出可零散股），
        但本层持仓均为 100 的倍数，如实记录。
        """
        if shares is None or shares <= 0:
            return ExecResult(OrderStatus.REJECTED, OrderSide.SELL, earliest_date,
                              None, None, 0, reason="shares<=0")
        start_pos = calendar_position(self.trading_calendar, earliest_date)
        if start_pos >= len(self.trading_calendar):
            return ExecResult(OrderStatus.UNEXECUTABLE, OrderSide.SELL,
                              earliest_date, None, None, 0,
                              reason=self._resolve_no_fill_reason(symbol, start_pos))
        for r in range(self.max_roll_days + 1):
            pos = start_pos + r
            if pos >= len(self.trading_calendar):
                break
            d = self.trading_calendar[pos]
            kind, bar = self._classify_day(symbol, d, OrderSide.SELL)
            if kind != "tradable":
                continue
            assert bar is not None and bar.close is not None and bar.close > 0
            price = bar.close
            eff = price * (1.0 - self.cost_model.sell_cost_rate)
            gross = price * shares
            fee = gross * self.cost_model.sell_cost_rate
            return ExecResult(OrderStatus.FILLED, OrderSide.SELL, earliest_date, d,
                              price, shares, gross, fee, eff, r)
        reason = self._resolve_no_fill_reason(symbol, start_pos)
        return ExecResult(OrderStatus.UNEXECUTABLE, OrderSide.SELL, earliest_date,
                          None, None, shares,
                          roll_days_used=self.max_roll_days, reason=reason)

    def _resolve_no_fill_reason(self, symbol: str, start_pos: int) -> str:
        """顺延耗尽后的归因：双边证据 -> suspension；后续仍无 bar -> 退市/数据缺失。"""
        cal = self.trading_calendar
        after_pos = start_pos + self.max_roll_days + 1
        # 前方证据：start_pos 之前最近一个有效收盘
        pre_ok = False
        for p in range(start_pos - 1, max(start_pos - 40, -1), -1):
            if 0 <= p < len(cal) and bar_has_valid_close(self.get_bar(symbol, cal[p])):
                pre_ok = True
                break
        # 后方证据：顺延窗口之后是否存在有效 bar（限售/退市则没有）
        post_ok = False
        for p in range(after_pos, min(after_pos + 40, len(cal))):
            if 0 <= p < len(cal) and bar_has_valid_close(self.get_bar(symbol, cal[p])):
                post_ok = True
                break
        if pre_ok and post_ok:
            return "suspension"
        if pre_ok and not post_ok:
            return "delisted_or_unresolved"
        return "data_missing"


# ---------------------------------------------------------------------------
# 封存账本 -> 委托（M2-A 输入侧）
# ---------------------------------------------------------------------------


# 方向词表：匹配前一律大写归一（bear/bull 英文词大小写不敏感）。
# 判定规则：
#   1) 显式中性/无方向词（N/A、中性、HOLD、WAIT、NO_TRADE 等）→ None
#   2) 先按 bear 词表、再按 bull 词表计数命中；两侧同时命中（歧义/混合语义，
#      如「看多但有减持风险」）→ 保守返回 None，不猜方向
#   3) 唯一侧命中 → 对应方向；两侧均未命中 → None
# 注意「看多」「看空」均含「看」字但分属两侧词表，按整词匹配不拆分。
_BULL_WORDS = ("看多", "偏多", "谨慎偏多", "中性偏多", "增持", "BUY", "BULL")
_BEAR_WORDS = ("看空", "偏空", "减持", "SELL", "BEAR")
_NEUTRAL_WORDS = ("N/A", "NONE", "NULL", "中性", "HOLD", "WAIT", "NO_TRADE",
                  "ABSTAIN", "观望")


def ledger_direction(record: Mapping[str, Any], horizon: str = "medium") -> Optional[str]:
    """从封存账本行提取方向：优先 horizons[hz].direction，退化为顶层 direction。

    返回 'BUY' / 'SELL' / None（中性、NO_TRADE、缺失、歧义等不可下单方向）。
    匹配大小写不敏感；bull 与 bear 词同时命中视为歧义 → None（保守不产单）。
    """
    hz = (record.get("horizons") or {}).get(horizon) or {}
    raw = hz.get("direction") or record.get("direction_top")
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in _NEUTRAL_WORDS:
        return None
    bull_hit = any(w.upper() in s for w in _BULL_WORDS)
    bear_hit = any(w.upper() in s for w in _BEAR_WORDS)
    if bull_hit and bear_hit:
        return None  # 歧义：多空词同现，无法唯一判定
    if bear_hit:
        return "SELL"
    if bull_hit:
        return "BUY"
    return None


def ledger_trade_action(record: Mapping[str, Any], horizon: str = "medium") -> Optional[str]:
    hz = (record.get("horizons") or {}).get(horizon) or {}
    return hz.get("trade_action") or record.get("trade_action_top")


def signal_to_order(record: Mapping[str, Any], cash_per_order: float,
                    horizon: str = "medium") -> Optional[Dict[str, Any]]:
    """把一条封存账本记录映射为委托意图（不含执行）。

    方向与动作双门：direction 先定侧（BUY/SELL），trade_action 再按该侧
    允许集白名单过滤（BUY: BUY/ADD；SELL: SELL/REDUCE/EXIT）。
    返回 None 表示该记录不产生委托（中性/WAIT/NO_TRADE/HOLD/方向缺失/歧义）。
    """
    side = ledger_direction(record, horizon)
    action = ledger_trade_action(record, horizon)
    if side is None:
        return None
    act = str(action).strip().upper() if action is not None else None
    if side == "BUY":
        if act is not None and act not in ("BUY", "ADD"):
            return None
    else:  # SELL
        if act is not None and act not in ("SELL", "REDUCE", "EXIT"):
            return None
    return {
        "symbol": record.get("symbol"),
        "signal_date": record.get("signal_date"),
        "side": side,
        "cash": cash_per_order if side == "BUY" else 0.0,
        "report_id": record.get("report_id"),
        "timing_class": record.get("timing_class"),
    }
