"""M2-B 影子组合 · 策略与记账层（DAV-1676，计划 v1.0 §5 M2）。

同一份封存信号（``forward_ledger.jsonl`` 行）上并行运行三套策略：

  S1 ``direction``       —— 按经理方向/概率下单（忽略风控输出层）。
  S2 ``risk_adjusted``   —— 按风控后的最终动作下单：优先 horizons[hz]
                           .trade_action（缺失退 decision → direction），
                           叠加组合层离线风控规则（RiskRules，可换版本重放）。
  S3 ``high_confidence`` —— 高置信精选：S1 信号 ∩ confidence ≥ 阈值
                           （rules_v2 起追加 risk_status ∈ {OK, UNKNOWN}）。

通用持仓口径（中线 horizon 语义）：
  - 看多 / BUY      → 目标持有该标的；未持有则 T+1 开盘买入（M2-A 规则）。
  - 看空 / SELL     → 清仓目标：持有则 T+1 收盘卖出（M2-A 卖出走收盘价）。
  - 中性 / 无信号   → 不动。
  - 同一标的每个信号窗口只开一仓；同标的信号重叠时抢占（先平后开）。
  - Long-only：看空不产生空头头寸（A 股融券不在账本口径内）。

记账（账本确定性计算）：
  - Ledger 逐笔记 BUY/SELL 成交与费用（V-03a CostModel，复用 M2-A）。
  - 逐日收盘 mark-to-market：cash + Σ shares×close；无 bar 沿用 last_price。
  - 输出净值序列、换手（成交额 / 期间平均权益）、最大回撤、胜率等。

风控规则离线重放：
  ``RiskRules`` 为纯数据版本对象；``risk_rules_v1`` / ``risk_rules_v2``
  为已冻结的版本实例，同一份封存信号可用不同版本离线重放对比。

本模块纯函数 + 注入式行情/日历，不触网、不写生产库。
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tradingagents.eval.trade_execution import (
    ExecutionEngine,
    OrderSide,
    OrderStatus,
    TradingCalendar,
    calendar_position,
    ledger_direction,
)

# ---------------------------------------------------------------------------
# 账本输入
# ---------------------------------------------------------------------------


def load_forward_ledger(path: Path | str) -> List[Dict[str, Any]]:
    """读 forward_ledger.jsonl，兼容裸记录行与 {'record': {...}} 包装行。"""
    out: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rec = obj.get("record") if isinstance(obj, dict) else None
            out.append(rec if isinstance(rec, dict) else obj)
    return out


def _norm_action(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("BUY", "SELL", "HOLD", "WAIT", "NO_TRADE") else None


def _norm_decision(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("BUY", "SELL", "HOLD", "WAIT", "NO_TRADE") else None


def ledger_confidence(record: Mapping[str, Any], horizon: str = "medium") -> Optional[float]:
    hz = (record.get("horizons") or {}).get(horizon) or {}
    v = hz.get("confidence")
    if v is None:
        v = record.get("confidence_top")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ledger_probability(record: Mapping[str, Any], horizon: str = "medium") -> Optional[float]:
    hz = (record.get("horizons") or {}).get(horizon) or {}
    v = hz.get("probability")
    if v is None:
        v = record.get("probability_top")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def ledger_risk_status(record: Mapping[str, Any], horizon: str = "medium") -> Optional[str]:
    hz = (record.get("horizons") or {}).get(horizon) or {}
    v = hz.get("risk_status")
    if v is None:
        v = record.get("risk_status_top")
    s = str(v).strip().upper() if v is not None else ""
    return s or None


def ledger_final_action(record: Mapping[str, Any], horizon: str = "medium") -> Optional[str]:
    """风控后的最终动作：trade_action → decision → direction 映射。"""
    hz = (record.get("horizons") or {}).get(horizon) or {}
    act = _norm_action(hz.get("trade_action")) or _norm_action(record.get("trade_action_top"))
    if act is not None:
        return act
    dec = _norm_decision(hz.get("decision")) or _norm_decision(record.get("decision_top"))
    if dec is not None:
        return dec
    d = ledger_direction(record, horizon)
    return d  # BUY / SELL / None


# ---------------------------------------------------------------------------
# 风控规则（代码派生、版本化、离线可重放）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskRules:
    """组合层风控规则版本（纯数据；改版本 = 换实例，不动封存信号）。

    - ``max_positions``        : 组合最大同时持仓数（0 = 不限）
    - ``max_new_per_day``      : 单个信号日最多新开的仓数（0 = 不限）
    - ``position_weight``      : 每个新仓的目标仓位权重（占用资金比例上限）
    - ``min_confidence``       : 信号入选最低 confidence（None = 不看 confidence）
    - ``require_risk_clear``   : True 要求 horizons.risk_status ∈ {OK, UNKNOWN, None}
                                 才允许开仓（BLOCKED/ELEVATED 抑制开仓）
    - ``respect_blocked``      : True 时 risk_status==BLOCKED 抑制任何新动作
                                 （含平仓信号同样放行——平仓不被风控阻断）
    - ``cooldown_per_symbol``  : 同标的相邻两次开仓的最小间隔交易日数（0=不限）
    """

    name: str
    max_positions: int = 0
    max_new_per_day: int = 0
    position_weight: float = 0.10
    min_confidence: Optional[float] = None
    require_risk_clear: bool = False
    respect_blocked: bool = True
    cooldown_per_symbol: int = 0


def risk_rules_v1() -> RiskRules:
    """v1 宽松版：不限仓数，confidence 无门槛，仅尊重 BLOCKED 抑制新开仓。"""
    return RiskRules(
        name="risk_v1",
        max_positions=0,
        max_new_per_day=0,
        position_weight=0.20,
        min_confidence=None,
        require_risk_clear=False,
        respect_blocked=True,
        cooldown_per_symbol=0,
    )


def risk_rules_v2() -> RiskRules:
    """v2 收紧版：限 5 仓 / 每日最多 2 新开 / 单仓 10% / confidence≥60 /
    risk_status 必须清白 / 同标的冷却 5 个交易日。"""
    return RiskRules(
        name="risk_v2",
        max_positions=5,
        max_new_per_day=2,
        position_weight=0.10,
        min_confidence=60.0,
        require_risk_clear=True,
        respect_blocked=True,
        cooldown_per_symbol=5,
    )


RISK_RULES_VERSIONS: Dict[str, RiskRules] = {
    r.name: r for r in (risk_rules_v1(), risk_rules_v2())
}


# ---------------------------------------------------------------------------
# 策略意图
# ---------------------------------------------------------------------------


class Intent(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class SignalIntent:
    """策略层从一条封存记录产生的意图（尚未经风控与执行层过滤）。"""

    record: Mapping[str, Any]
    symbol: str
    signal_date: str
    intent: Intent
    weight: float = 1.0         # 目标仓位权重（由策略设定，风控可再压缩）
    confidence: Optional[float] = None
    probability: Optional[float] = None
    horizon: str = "medium"
    reason: str = ""


def _weight_from_probability(prob: Optional[float]) -> float:
    """概率 → 仓位权重：线性映射到 [0.05, 1.0]，缺概率给中性 0.5。"""
    if prob is None:
        return 0.5
    p = min(max(prob, 0.0), 1.0)
    return 0.05 + 0.95 * p


def strategy_direction_intent(record: Mapping[str, Any],
                              horizon: str = "medium") -> Optional[SignalIntent]:
    """S1 方向/概率策略：只看 direction，不看风控输出。"""
    d = ledger_direction(record, horizon)
    if d is None:
        return None
    intent = Intent.BUY if d == "BUY" else Intent.SELL
    prob = ledger_probability(record, horizon)
    return SignalIntent(
        record=record,
        symbol=str(record.get("symbol") or ""),
        signal_date=str(record.get("signal_date") or ""),
        intent=intent,
        weight=_weight_from_probability(prob),
        confidence=ledger_confidence(record, horizon),
        probability=prob,
        horizon=horizon,
        reason=f"direction={d}",
    )


def strategy_risk_adjusted_intent(record: Mapping[str, Any],
                                  horizon: str = "medium") -> Optional[SignalIntent]:
    """S2 风控后最终动作：trade_action/decision 为准。"""
    act = ledger_final_action(record, horizon)
    if act is None or act in ("WAIT", "NO_TRADE", "HOLD"):
        # HOLD 语义 = 维持现状；本层无「已有仓位维持」概念，对未持有者
        # 不产生意图；对持仓者，HOLD 即不平仓（也无须动作）。故 None。
        return None
    intent = Intent.BUY if act == "BUY" else Intent.SELL
    prob = ledger_probability(record, horizon)
    return SignalIntent(
        record=record,
        symbol=str(record.get("symbol") or ""),
        signal_date=str(record.get("signal_date") or ""),
        intent=intent,
        weight=_weight_from_probability(prob),
        confidence=ledger_confidence(record, horizon),
        probability=prob,
        horizon=horizon,
        reason=f"final_action={act}",
    )


def strategy_high_confidence_intent(
        record: Mapping[str, Any],
        horizon: str = "medium",
        min_confidence: float = 70.0) -> Optional[SignalIntent]:
    """S3 高置信精选：S1 信号 ∩ confidence ≥ 阈值。"""
    conf = ledger_confidence(record, horizon)
    if conf is None or conf < min_confidence:
        return None
    base = strategy_direction_intent(record, horizon)
    if base is None:
        return None
    return SignalIntent(
        record=base.record, symbol=base.symbol, signal_date=base.signal_date,
        intent=base.intent, weight=base.weight, confidence=conf,
        probability=base.probability, horizon=horizon,
        reason=f"high_confidence={conf:.0f}>={min_confidence:.0f}",
    )


StrategyFn = Callable[[Mapping[str, Any]], Optional[SignalIntent]]

STRATEGIES: Dict[str, StrategyFn] = {
    "direction": lambda r: strategy_direction_intent(r),
    "risk_adjusted": lambda r: strategy_risk_adjusted_intent(r),
    "high_confidence": lambda r: strategy_high_confidence_intent(r),
}


# ---------------------------------------------------------------------------
# 账本（确定性记账）
# ---------------------------------------------------------------------------


@dataclass
class LedgerEntry:
    """单笔成交记录。"""

    trade_date: str          # 实际成交日
    signal_date: str
    symbol: str
    side: str                # BUY / SELL
    shares: int
    price: float             # 原始成交价（开盘买 / 收盘卖）
    effective_price: float   # 含成本价
    gross_amount: float
    fee: float
    cash_after: float
    status: str              # filled
    reason: str = ""


@dataclass
class HoldingState:
    symbol: str
    shares: int
    entry_date: str
    entry_price_eff: float   # 买入有效价
    last_price: Optional[float] = None   # 最近一个有效收盘（用于 mark）


@dataclass
class ShadowLedger:
    """单策略影子账本。初始资金、持仓、现金、逐笔成交、逐日净值。"""

    initial_cash: float
    engine: ExecutionEngine
    trading_calendar: TradingCalendar

    cash: float = field(init=False)
    positions: Dict[str, HoldingState] = field(default_factory=dict, init=False)
    entries: List[LedgerEntry] = field(default_factory=list, init=False)
    # 每信号日同标的上一次开仓日（冷却判定用）
    _last_entry_pos: Dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    # -- 估值 ----------------------------------------------------------------

    def market_value(self, get_bar, date: str) -> float:
        mv = 0.0
        for sym, h in self.positions.items():
            bar = get_bar(sym, date)
            px = None
            if bar is not None and bar.close is not None and bar.close > 0 and \
                    bar.vol is not None and bar.vol > 0:
                px = float(bar.close)
                h.last_price = px
            elif h.last_price is not None:
                px = h.last_price
            if px is not None:
                mv += h.shares * px
        return mv

    def equity(self, get_bar, date: str) -> float:
        return self.cash + self.market_value(get_bar, date)

    # -- 执行委托 -------------------------------------------------------------

    def try_buy(self, si: SignalIntent, exec_cash: float) -> LedgerEntry | None:
        r = self.engine.execute_buy(si.symbol, si.signal_date, cash=exec_cash)
        if r.status != OrderStatus.FILLED:
            return None
        self.cash -= r.effective_price * r.shares  # type: ignore[operator]
        h = HoldingState(symbol=si.symbol, shares=r.shares,
                         entry_date=r.executed_date or si.signal_date,
                         entry_price_eff=r.effective_price or r.price or 0.0)
        self.positions[si.symbol] = h
        e = LedgerEntry(trade_date=r.executed_date or si.signal_date,
                        signal_date=si.signal_date, symbol=si.symbol,
                        side="BUY", shares=r.shares, price=r.price or 0.0,
                        effective_price=r.effective_price or 0.0,
                        gross_amount=r.gross_amount, fee=r.fee,
                        cash_after=self.cash, status=r.status.value,
                        reason=si.reason)
        self.entries.append(e)
        return e

    def try_sell(self, symbol: str, signal_date: str,
                 earliest_date: str, reason: str = "") -> LedgerEntry | None:
        h = self.positions.get(symbol)
        if h is None or h.shares <= 0:
            return None
        r = self.engine.execute_sell(symbol, earliest_date, h.shares)
        if r.status != OrderStatus.FILLED:
            return None
        proceeds = (r.effective_price or 0.0) * r.shares
        self.cash += proceeds
        e = LedgerEntry(trade_date=r.executed_date or earliest_date,
                        signal_date=signal_date, symbol=symbol, side="SELL",
                        shares=r.shares, price=r.price or 0.0,
                        effective_price=r.effective_price or 0.0,
                        gross_amount=r.gross_amount, fee=r.fee,
                        cash_after=self.cash, status=r.status.value,
                        reason=reason)
        self.entries.append(e)
        del self.positions[symbol]
        return e


# ---------------------------------------------------------------------------
# 组合回测主流程
# ---------------------------------------------------------------------------


@dataclass
class PortfolioResult:
    strategy: str
    risk_rules: str
    initial_cash: float
    entries: List[LedgerEntry]
    nav_series: List[Tuple[str, float]]
    n_records: int
    n_signals: int          # 产生意图的记录数
    n_blocked: int          # 风控拦截的意图数
    n_filled_buys: int
    n_filled_sells: int
    summary: Dict[str, Any]


def _sort_key_record(r: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (str(r.get("signal_date") or ""),
            str(r.get("symbol") or ""),
            str(r.get("report_id") or ""))


def run_shadow_portfolio(
        records: Sequence[Mapping[str, Any]],
        strategy_fn: StrategyFn,
        engine: ExecutionEngine,
        rules: RiskRules,
        initial_cash: float = 1_000_000.0,
        horizon: str = "medium",
        strategy_name: str = "",
        get_bar: Optional[Callable[[str, str], Any]] = None,
        mark_start: Optional[str] = None,
        mark_end: Optional[str] = None,
) -> PortfolioResult:
    """在给定封存记录集合上运行一套策略，返回确定性账本结果。

    - ``records`` 输入顺序无关：内部按 (signal_date, symbol, report_id) 排序。
    - ``mark_start`` / ``mark_end`` 为净值区间（默认覆盖全部信号日±顺延窗口）。
    """
    get_bar = get_bar or engine.get_bar
    ledger = ShadowLedger(initial_cash=initial_cash, engine=engine,
                          trading_calendar=engine.trading_calendar)
    cal = list(engine.trading_calendar)

    # 1) 逐条生成意图（确定性顺序）
    intents: List[SignalIntent] = []
    for rec in sorted(records, key=_sort_key_record):
        si = strategy_fn(rec)
        if si is None or not si.symbol or not si.signal_date:
            continue
        intents.append(si)

    # 2) 按信号日分组 → 逐日应用风控 → 执行
    by_day: Dict[str, List[SignalIntent]] = {}
    for si in intents:
        by_day.setdefault(si.signal_date, []).append(si)

    blocked = 0
    for day in sorted(by_day):
        day_list = by_day[day]
        new_buys_today = 0
        for si in day_list:
            rec = si.record
            risk_status = ledger_risk_status(rec, horizon)

            # --- 风控层（版本化规则） ---
            if si.intent == Intent.BUY:
                if rules.respect_blocked and risk_status == "BLOCKED":
                    blocked += 1
                    continue
                if rules.require_risk_clear and risk_status not in (
                        None, "OK", "UNKNOWN"):
                    blocked += 1
                    continue
                if rules.min_confidence is not None and (
                        si.confidence is None
                        or si.confidence < rules.min_confidence):
                    blocked += 1
                    continue
                if rules.max_positions and len(ledger.positions) >= rules.max_positions:
                    blocked += 1
                    continue
                if rules.max_new_per_day and new_buys_today >= rules.max_new_per_day:
                    blocked += 1
                    continue
                if rules.cooldown_per_symbol > 0:
                    last = ledger._last_entry_pos.get(si.symbol)
                    cur_pos = calendar_position(cal, day)
                    if last is not None and cur_pos - last < rules.cooldown_per_symbol:
                        blocked += 1
                        continue
                # 已有持仓 → 先平（新信号覆盖旧持仓）
                if si.symbol in ledger.positions:
                    ledger.try_sell(si.symbol, day, si.signal_date,
                                    reason="preempt_by_new_signal")
                    if si.symbol in ledger.positions:
                        blocked += 1
                        continue
                # 仓位额度：equity * position_weight * si.weight
                eq = ledger.equity(get_bar, day)
                budget = eq * rules.position_weight * max(si.weight, 0.0)
                exec_cash = min(ledger.cash, budget)
                e = ledger.try_buy(si, exec_cash)
                if e is not None:
                    new_buys_today += 1
                    ledger._last_entry_pos[si.symbol] = calendar_position(
                        cal, e.trade_date)
            else:  # SELL 意图：平仓信号，风控不拦截退出
                if si.symbol in ledger.positions:
                    ledger.try_sell(si.symbol, day, si.signal_date,
                                    reason=si.reason or "signal_sell")

    # 3) 逐日净值（mark-to-market）
    if not cal:
        nav: List[Tuple[str, float]] = []
    else:
        start = mark_start or cal[0]
        end = mark_end or cal[-1]
        nav = [(d, ledger.equity(get_bar, d))
               for d in cal if start <= d <= end]

    buys = [e for e in ledger.entries if e.side == "BUY"]
    sells = [e for e in ledger.entries if e.side == "SELL"]
    summary = summarize(ledger, nav)
    return PortfolioResult(
        strategy=strategy_name or getattr(strategy_fn, "__name__", "strategy"),
        risk_rules=rules.name, initial_cash=initial_cash,
        entries=ledger.entries, nav_series=nav,
        n_records=len(records), n_signals=len(intents), n_blocked=blocked,
        n_filled_buys=len(buys), n_filled_sells=len(sells),
        summary=summary,
    )


# ---------------------------------------------------------------------------
# 指标（账本确定性计算）
# ---------------------------------------------------------------------------


def summarize(ledger: ShadowLedger,
              nav: Sequence[Tuple[str, float]]) -> Dict[str, Any]:
    final_nav = nav[-1][1] if nav else ledger.cash
    total_ret = final_nav / ledger.initial_cash - 1.0 if ledger.initial_cash else 0.0

    turnover_gross = sum(e.gross_amount for e in ledger.entries)
    avg_eq = (statistics.fmean(v for _, v in nav) if nav else ledger.initial_cash)
    turnover = turnover_gross / avg_eq if avg_eq else 0.0

    peak = -math.inf
    max_dd = 0.0
    for _, v in nav:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, 1.0 - v / peak)

    rets = [
        nav[i][1] / nav[i - 1][1] - 1.0
        for i in range(1, len(nav)) if nav[i - 1][1] > 0
    ]
    sharpe = 0.0
    if len(rets) >= 2:
        sd = statistics.pstdev(rets)
        if sd > 0:
            sharpe = statistics.fmean(rets) / sd * math.sqrt(252)

    sells = [e for e in ledger.entries if e.side == "SELL"]
    return {
        "initial_cash": ledger.initial_cash,
        "final_nav": final_nav,
        "total_return": total_ret,
        "n_trades": len(ledger.entries),
        "n_buys": sum(1 for e in ledger.entries if e.side == "BUY"),
        "n_sells": len(sells),
        "turnover_gross": turnover_gross,
        "turnover_ratio": turnover,
        "max_drawdown": max_dd,
        "daily_sharpe_ann": sharpe,
        "open_positions": {s: h.shares for s, h in ledger.positions.items()},
        "nav_points": len(nav),
    }


def compare_strategies(results: Sequence[PortfolioResult]) -> Dict[str, Any]:
    """多策略对比汇总表。"""
    return {
        r.strategy: {
            "risk_rules": r.risk_rules,
            "n_signals": r.n_signals,
            "n_blocked": r.n_blocked,
            "n_buys": r.summary["n_buys"],
            "n_sells": r.summary["n_sells"],
            "total_return": r.summary["total_return"],
            "max_drawdown": r.summary["max_drawdown"],
            "turnover_ratio": r.summary["turnover_ratio"],
            "daily_sharpe_ann": r.summary["daily_sharpe_ann"],
            "final_nav": r.summary["final_nav"],
        }
        for r in results
    }
