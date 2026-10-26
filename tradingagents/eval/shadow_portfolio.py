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
  - 净值按「成交日 ≤ 当日」重放账本事件（DAV-2323）：历史净值不得用末态
    cash/positions 回算，未来信号不得改写历史净值；T+1 成交的持仓也不得
    回填到信号日估值。
  - 标准持仓出口 = 信号日起第 40 个交易日收盘（``exit_after_trading_days``，
    0/None 关闭）；早于该日的 SELL 仍按 T+1 收盘退出，到期退出排在当日信号
    之后（收盘成交，不用当日早盘的卖出回款）。
  - 委托解析与成交入账分离（DAV-2344）：成交层给出 ``executed_date``（停牌/
    锁板顺延到未来）时只登记待成交委托，到该交易日的对应时点才改动现金与
    持仓——买入走开盘、卖出走收盘，同一日的开盘买入不得使用当日收盘卖出
    的回款。未成交的卖出委托仍计入持仓与估值（不得提前释放持仓额度/净值），
    未成交的买入只占用现金额度、不预扣现金也不计入净值。
  - 输出净值序列、换手（成交额 / 期间平均权益）、最大回撤、胜率等。

风控规则离线重放：
  ``RiskRules`` 为纯数据版本对象；``risk_rules_v1`` / ``risk_rules_v2``
  为已冻结的版本实例，同一份封存信号可用不同版本离线重放对比。

策略与风控分层（DAV-2323，对齐 M2 方案 §3）：
  - 信号层风控（risk_status / require_risk_clear / min_confidence）只作用于
    显式声明不忽略它的策略（S2/S3）；S1 ``direction`` 按模块语义忽略风控
    输出层，不再被默认 ``RiskRules`` 的 BLOCKED 等条件污染。
  - 组合层约束（持仓数、每日新开、单仓权重、冷却）对所有策略一致生效。
  - S2 只认最终动作（trade_action → decision），缺失时记不可用（不产生
    意图），不再回退到原始 direction 变成伪 S1。

本模块纯函数 + 注入式行情/日历，不触网、不写生产库。
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any,
)

from tradingagents.eval.trade_execution import (
    ExecutionEngine,
    OrderStatus,
    TradingCalendar,
    calendar_position,
    ledger_direction,
    next_trading_day,
)
from tradingagents.storage.result_data_compat import (
    result_data_authoritative_view,
)

# ---------------------------------------------------------------------------
# 账本输入
# ---------------------------------------------------------------------------


def load_forward_ledger(path: Path | str) -> list[dict[str, Any]]:
    """读 forward_ledger.jsonl，兼容裸记录行与 {'record': {...}} 包装行。"""
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rec = obj.get("record") if isinstance(obj, dict) else None
            out.append(rec if isinstance(rec, dict) else obj)
    return out


def _norm_action(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("BUY", "SELL", "HOLD", "WAIT", "NO_TRADE") else None


def _norm_decision(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s if s in ("BUY", "SELL", "HOLD", "WAIT", "NO_TRADE") else None


def _record_hz(record: Mapping[str, Any], horizon: str) -> Mapping[str, Any]:
    """``horizons.<hz>`` on a sealed ledger record, read via the
    authoritative view (DAV-2129): when the record carries a ``*_term``
    twin its values win over a stale alias mirror; on plain ledger
    records (no ``*_term``) this is a pass-through.
    """
    return (
        result_data_authoritative_view(record).get("horizons") or {}
    ).get(horizon) or {}


def ledger_confidence(record: Mapping[str, Any], horizon: str = "medium") -> float | None:
    hz = _record_hz(record, horizon)
    v = hz.get("confidence")
    if v is None:
        v = record.get("confidence_top")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ledger_probability(record: Mapping[str, Any], horizon: str = "medium") -> float | None:
    hz = _record_hz(record, horizon)
    v = hz.get("probability")
    if v is None:
        v = record.get("probability_top")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def ledger_risk_status(record: Mapping[str, Any], horizon: str = "medium") -> str | None:
    hz = _record_hz(record, horizon)
    v = hz.get("risk_status")
    if v is None:
        v = record.get("risk_status_top")
    s = str(v).strip().upper() if v is not None else ""
    return s or None


def ledger_final_action(record: Mapping[str, Any], horizon: str = "medium") -> str | None:
    """风控后的最终动作：trade_action → decision；缺失返回 None。

    DAV-2323（源自 DAV-1988 审查）：不再回退到原始 ``direction``。S2 的语义是
    「按风控后动作下单」，缺最终动作时应记不可用，而不是静默退化成 S1，否则
    两套策略的对比失去意义。
    """
    hz = _record_hz(record, horizon)
    act = _norm_action(hz.get("trade_action")) or _norm_action(record.get("trade_action_top"))
    if act is not None:
        return act
    return _norm_decision(hz.get("decision")) or _norm_decision(record.get("decision_top"))


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
    - ``respect_blocked``      : True 时 risk_status==BLOCKED 抑制新开仓
                                 （仅拦 BUY；平仓信号不受影响——退出永远放行）
    - ``cooldown_per_symbol``  : 同标的相邻两次开仓的最小间隔交易日数（0=不限）
    """

    name: str
    max_positions: int = 0
    max_new_per_day: int = 0
    position_weight: float = 0.10
    min_confidence: float | None = None
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


RISK_RULES_VERSIONS: dict[str, RiskRules] = {
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
    confidence: float | None = None
    probability: float | None = None
    horizon: str = "medium"
    reason: str = ""
    # True = 该策略按定义忽略风控输出层（S1）；组合层约束仍统一生效。
    # DAV-2323：默认 RiskRules 的 BLOCKED/置信门槛不得污染 S1。
    ignore_signal_risk: bool = False


def _weight_from_probability(prob: float | None) -> float:
    """概率 → 仓位权重：线性映射到 [0.05, 1.0]，缺概率给中性 0.5。"""
    if prob is None:
        return 0.5
    p = min(max(prob, 0.0), 1.0)
    return 0.05 + 0.95 * p


def strategy_direction_intent(record: Mapping[str, Any],
                              horizon: str = "medium") -> SignalIntent | None:
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
        # S1 语义：只按经理方向/概率下单，忽略风控输出层（DAV-2323/DAV-1988）。
        ignore_signal_risk=True,
    )


def strategy_risk_adjusted_intent(record: Mapping[str, Any],
                                  horizon: str = "medium") -> SignalIntent | None:
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
        min_confidence: float = 70.0) -> SignalIntent | None:
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


StrategyFn = Callable[[Mapping[str, Any]], SignalIntent | None]

STRATEGIES: dict[str, StrategyFn] = {
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
    last_price: float | None = None   # 最近一个有效收盘（用于 mark）
    signal_date: str = ""             # 开仓信号日（到期退出的起算基准）
    expiry_date: str | None = None    # 标准持仓出口日（信号日 + N 交易日收盘）
    # False = 委托已解析但尚未成交（成交日未到）：不预扣现金、不参与估值。
    filled: bool = True
    # True = 已登记卖出委托、等待成交日收盘入账；成交前仍计入持仓与估值。
    exit_pending: bool = False


@dataclass
class PendingFill:
    """已解析、待成交日入账的委托（DAV-2344）。

    成交层解析（``executed_date`` 可能因停牌/锁板顺延到未来某日）与账本入账
    分离：只有到 ``executed_date`` 的对应时点才改动现金与持仓，禁止提前释放
    未来回款、提前释放持仓额度，或用未发生的成交给当日买单供资。
    """

    side: str                       # BUY / SELL
    symbol: str
    executed_date: str              # 实际成交日（买=开盘，卖=收盘）
    shares: int
    price: float                    # 原始成交价
    effective_price: float          # 含成本价
    gross_amount: float
    fee: float
    signal_date: str
    reason: str = ""
    lot: HoldingState | None = None                      # BUY：待开仓的持仓
    lots: list[HoldingState] = field(default_factory=list)  # SELL：待平仓的持仓
    reserved_cash: float = 0.0      # BUY：已占用额度、尚未从现金中扣除

    @property
    def cost(self) -> float:
        return self.effective_price * self.shares


@dataclass
class ShadowLedger:
    """单策略影子账本。初始资金、持仓、现金、逐笔成交、逐日净值。

    ``lots`` 为持仓明细（允许同一标的短暂重叠：先平后开时新仓在开盘成交、
    旧仓在同一交易日收盘成交）；``positions`` 为按标的聚合的只读快照。
    未成交委托记在 ``pending``，到成交日的开盘（买）/收盘（卖）才入账。
    """

    initial_cash: float
    engine: ExecutionEngine
    trading_calendar: TradingCalendar

    cash: float = field(init=False)
    lots: list[HoldingState] = field(default_factory=list, init=False)
    entries: list[LedgerEntry] = field(default_factory=list, init=False)
    pending: list[PendingFill] = field(default_factory=list, init=False)
    # 每信号日同标的上一次开仓日（冷却判定用）
    _last_entry_pos: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    # -- 持仓视图 ------------------------------------------------------------

    @property
    def positions(self) -> dict[str, HoldingState]:
        """按标的聚合的持仓快照（只读；同标的多笔合并股数）。"""
        out: dict[str, HoldingState] = {}
        for h in self.lots:
            cur = out.get(h.symbol)
            if cur is None:
                out[h.symbol] = HoldingState(
                    symbol=h.symbol, shares=h.shares, entry_date=h.entry_date,
                    entry_price_eff=h.entry_price_eff,
                    last_price=h.last_price, signal_date=h.signal_date,
                    expiry_date=h.expiry_date, filled=h.filled,
                    exit_pending=h.exit_pending)
            else:
                cur.shares += h.shares
        return out

    def reserved_cash(self) -> float:
        """已占用、尚未扣除的买入额度（未到成交日的买入委托）。"""
        return sum(f.reserved_cash for f in self.pending if f.side == "BUY")

    def available_cash(self) -> float:
        """可用于新委托的现金 = 已入账现金 − 未成交买入占用额度。"""
        return self.cash - self.reserved_cash()

    # -- 估值 ----------------------------------------------------------------

    def market_value(self, get_bar, date: str) -> float:
        mv = 0.0
        for h in self.lots:
            if not h.filled:
                # DAV-2344：成交日未到的买入既未扣现金，也不得计入权益。
                continue
            if h.entry_date and h.entry_date > date:
                # DAV-2323：T+1 才成交的持仓不得回填到更早日期估值（信号日
                # 收盘读到的只能是当日真实持仓）。
                continue
            bar = get_bar(h.symbol, date)
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

    def submit_buy(self, si: SignalIntent, exec_cash: float,
                   expiry_date: str | None = None) -> PendingFill | None:
        """解析 T+1 开盘买入委托；现金与持仓到成交日开盘才入账（DAV-2344）。

        返回已登记的待成交委托；成交层拒绝（现金不足一手/无后续交易日）
        返回 None，此时不占用任何额度。
        """
        r = self.engine.execute_buy(si.symbol, si.signal_date, cash=exec_cash)
        if r.status != OrderStatus.FILLED:
            return None
        eff = r.effective_price or 0.0
        executed = r.executed_date or si.signal_date
        h = HoldingState(symbol=si.symbol, shares=r.shares,
                         entry_date=executed,
                         entry_price_eff=eff,
                         signal_date=si.signal_date,
                         expiry_date=expiry_date,
                         # 成交日之前不得计入估值，也不从现金里预扣。
                         filled=False)
        pf = PendingFill(side="BUY", symbol=si.symbol,
                         executed_date=executed, shares=r.shares,
                         price=r.price or 0.0, effective_price=eff,
                         gross_amount=r.gross_amount, fee=r.fee,
                         signal_date=si.signal_date, reason=si.reason,
                         lot=h, reserved_cash=eff * r.shares)
        self.lots.append(h)
        self.pending.append(pf)
        return pf

    def submit_sell(self, symbol: str, signal_date: str,
                    earliest_date: str, reason: str = "") -> PendingFill | None:
        """解析卖出委托；回款与持仓释放到成交日收盘才入账（DAV-2344）。

        成交日未到之前持仓仍在 ``lots`` 里：不得提前释放持仓额度、不得用
        尚未发生的回款供资。已登记退出（``exit_pending``）的持仓不再重复
        下单；无可卖持仓时返回 None。
        """
        sellable = [h for h in self.lots
                    if h.symbol == symbol and not h.exit_pending]
        if not sellable:
            return None
        shares = sum(h.shares for h in sellable)
        # 未成交的买入（T+1 或停牌顺延）不得在其成交日之前卖出。
        first = max([earliest_date] + [h.entry_date for h in sellable
                                       if h.entry_date])
        r = self.engine.execute_sell(symbol, first, shares)
        if r.status != OrderStatus.FILLED:
            return None
        for h in sellable:
            h.exit_pending = True
        pf = PendingFill(side="SELL", symbol=symbol,
                         executed_date=r.executed_date or first,
                         shares=r.shares, price=r.price or 0.0,
                         effective_price=r.effective_price or 0.0,
                         gross_amount=r.gross_amount, fee=r.fee,
                         signal_date=signal_date, reason=reason, lots=sellable)
        self.pending.append(pf)
        return pf

    def apply_due_fills(self, day: str, phase: str) -> list[LedgerEntry]:
        """把 ``executed_date <= day`` 的到期委托入账。

        ``phase="open"`` 只入账买入（开盘价成交），``phase="close"`` 只入账
        卖出（收盘价成交）：同一交易日的开盘买入早于收盘卖出，不可互为资金
        来源。账本按真实成交时序追加，故 ``cash_after`` 是时序真实的余额。
        """
        want = "BUY" if phase == "open" else "SELL"
        due = [f for f in self.pending
               if f.side == want and f.executed_date <= day]
        if not due:
            return []
        self.pending = [f for f in self.pending
                        if not any(f is done for done in due)]
        out: list[LedgerEntry] = []
        for f in due:
            if f.side == "BUY":
                self.cash -= f.cost
                if f.lot is not None:
                    f.lot.filled = True
            else:
                self.cash += f.cost
                self.lots = [h for h in self.lots
                             if not any(h is closed for closed in f.lots)]
            e = LedgerEntry(trade_date=f.executed_date,
                            signal_date=f.signal_date, symbol=f.symbol,
                            side=f.side, shares=f.shares, price=f.price,
                            effective_price=f.effective_price,
                            gross_amount=f.gross_amount, fee=f.fee,
                            cash_after=self.cash, status=OrderStatus.FILLED.value,
                            reason=f.reason)
            self.entries.append(e)
            out.append(e)
        return out


# ---------------------------------------------------------------------------
# 组合回测主流程
# ---------------------------------------------------------------------------


@dataclass
class PortfolioResult:
    strategy: str
    risk_rules: str
    initial_cash: float
    entries: list[LedgerEntry]
    nav_series: list[tuple[str, float]]
    n_records: int
    n_signals: int          # 产生意图的记录数
    n_blocked: int          # 风控拦截的意图数（仅计 BUY 意图；SELL 意图不放行入此计数）
    n_filled_buys: int
    n_filled_sells: int
    summary: dict[str, Any]


def _sort_key_record(r: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """同 record 集合排序键：按封存到达序执行，避免同日同标的方向仲裁为哈希序。

    (signal_date, symbol, sealed_at, report_id)：sealed_at 为封存写入时间戳，
    业务语义上等价于"信号到达账本先后"；report_id 仅作同刻稳定序的决胜键。
    """
    return (str(r.get("signal_date") or ""),
            str(r.get("symbol") or ""),
            str(r.get("sealed_at") or ""),
            str(r.get("report_id") or ""))


def _nav_series_from_entries(
        initial_cash: float,
        entries: Sequence[LedgerEntry],
        get_bar: Callable[[str, str], Any],
        calendar: Sequence[str],
        start: str,
        end: str,
) -> list[tuple[str, float]]:
    """按成交日重放账本事件，逐日收盘计算净值（DAV-2323）。

    历史净值只由「成交日 ≤ 当日」的成交流水决定：不得用末态 cash/positions
    回算（否则未来成交会把前视偏差带进历史），后加入的记录也不能改写已有
    日期之前的净值（前缀稳定）；T+1 成交的持仓不计入信号日估值。

    成交日相同的记录按账本写入顺序重放；估值优先用当日有效收盘，无 bar
    （停牌/缺数据）沿用该标的最近一次有效收盘，与 ``market_value`` 口径一致。
    """
    ordered = sorted(entries, key=lambda e: e.trade_date)   # 稳定排序
    cash = float(initial_cash)
    shares: dict[str, int] = {}
    last_price: dict[str, float] = {}
    nav: list[tuple[str, float]] = []
    p = 0
    for day in calendar:
        while p < len(ordered) and ordered[p].trade_date <= day:
            e = ordered[p]
            p += 1
            if e.side == "BUY":
                cash -= e.effective_price * e.shares
                shares[e.symbol] = shares.get(e.symbol, 0) + e.shares
            else:
                cash += e.effective_price * e.shares
                left = shares.get(e.symbol, 0) - e.shares
                if left > 0:
                    shares[e.symbol] = left
                else:
                    shares.pop(e.symbol, None)
        if not (start <= day <= end):
            continue
        mv = 0.0
        for sym, n_shares in shares.items():
            bar = get_bar(sym, day)
            if (bar is not None and bar.close is not None and bar.close > 0
                    and bar.vol is not None and bar.vol > 0):
                px = float(bar.close)
                last_price[sym] = px
            else:
                px = last_price.get(sym)
            if px is not None:
                mv += n_shares * px
        nav.append((day, cash + mv))
    return nav


def run_shadow_portfolio(
        records: Sequence[Mapping[str, Any]],
        strategy_fn: StrategyFn,
        engine: ExecutionEngine,
        rules: RiskRules,
        initial_cash: float = 1_000_000.0,
        horizon: str = "medium",
        strategy_name: str = "",
        get_bar: Callable[[str, str], Any] | None = None,
        mark_start: str | None = None,
        mark_end: str | None = None,
        exit_after_trading_days: int | None = 40,
) -> PortfolioResult:
    """在给定封存记录集合上运行一套策略，返回确定性账本结果。

    - ``records`` 输入顺序无关：内部按 (signal_date, symbol, sealed_at,
      report_id) 排序。
    - ``mark_start`` / ``mark_end`` 为净值区间（默认覆盖整个日历）。
    - ``exit_after_trading_days``：标准持仓出口 = 信号日起第 N 个交易日收盘，
      默认 40（M2 方案 §4 的标准口径）；0/None 关闭。到期退出排在当日信号
      之后（收盘成交，不用当日早盘卖出回款），早于该日的 SELL 仍走 T+1 收盘。
    """
    get_bar = get_bar or engine.get_bar
    ledger = ShadowLedger(initial_cash=initial_cash, engine=engine,
                          trading_calendar=engine.trading_calendar)
    cal = list(engine.trading_calendar)
    cal_days = set(cal)

    # 1) 逐条生成意图（确定性顺序）
    intents: list[SignalIntent] = []
    for rec in sorted(records, key=_sort_key_record):
        si = strategy_fn(rec)
        if si is None or not si.symbol or not si.signal_date:
            continue
        intents.append(si)

    # 2) 按信号日分组 → 逐日应用风控 → 执行
    by_day: dict[str, list[SignalIntent]] = {}
    for si in intents:
        by_day.setdefault(si.signal_date, []).append(si)

    def _expiry_date(signal_date: str) -> str | None:
        n = exit_after_trading_days
        if not n or n <= 0 or not cal:
            return None
        pos = calendar_position(cal, signal_date) + n
        if pos >= len(cal):
            return None
        return cal[pos]

    blocked = 0
    # 信号日 ∪ 日历日：到期退出必须在真实日期触发，不能等到下一个信号日。
    for day in sorted(cal_days | set(by_day)):
        # 开盘：成交日已到的买入先入账（扣现金、开仓）。同一交易日的开盘
        # 买入早于收盘卖出，不能用当日收盘卖出的回款（DAV-2344）。
        ledger.apply_due_fills(day, "open")
        day_list = by_day.get(day, ())
        new_buys_today = 0
        for si in day_list:
            rec = si.record
            risk_status = ledger_risk_status(rec, horizon)

            # --- 信号层风控（版本化规则；仅对不忽略它的策略生效） ---
            if si.intent == Intent.BUY:
                if not si.ignore_signal_risk:
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
                # --- 组合层约束（所有策略一致，DAV-2323） ---
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
                # 已有持仓 → 先平（新信号覆盖旧持仓）。卖出委托只登记，回款
                # 与持仓释放到其成交日收盘才入账；已安排退出的持仓不再重复
                # 下单（停牌顺延期间新仓可开，但不得用未到账的回款供资）。
                live = [h for h in ledger.lots if h.symbol == si.symbol]
                if any(not h.exit_pending for h in live):
                    t1 = next_trading_day(cal, si.signal_date, 1)
                    if t1 is None:
                        blocked += 1
                        continue
                    ledger.submit_sell(si.symbol, si.signal_date, t1,
                                       reason="preempt_by_new_signal")
                    if any(not h.exit_pending for h in ledger.lots
                           if h.symbol == si.symbol):
                        blocked += 1
                        continue
                # 仓位额度：同一时点的真实权益（已入账成交）× 权重；可动用
                # 现金需先扣除未到成交日的买入占用额度（DAV-2344）。
                eq = ledger.equity(get_bar, day)
                budget = eq * rules.position_weight * max(si.weight, 0.0)
                exec_cash = min(ledger.available_cash(), budget)
                pf = ledger.submit_buy(si, exec_cash,
                                       expiry_date=_expiry_date(si.signal_date))
                if pf is not None:
                    new_buys_today += 1
                    # 冷却基准：实际成交日（买入发生日），而非信号日——同标的
                    # 二次开仓间隔按「上一次成交日」起算更贴近业务语义。
                    ledger._last_entry_pos[si.symbol] = calendar_position(
                        cal, pf.executed_date)
            else:  # SELL 意图：平仓信号，风控不拦截退出
                if any(h.symbol == si.symbol and not h.exit_pending
                       for h in ledger.lots):
                    # T+1 收盘卖出（对齐 R1 T+1 与 M2-A execute_sell 收盘价语义，
                    # 避免同日收盘回填的前视偏差）
                    t1 = next_trading_day(cal, si.signal_date, 1)
                    if t1 is None:
                        continue
                    ledger.submit_sell(si.symbol, si.signal_date, t1,
                                       reason=si.reason or "signal_sell")

        # 到期退出：目标日只尝试一次（成交层内部按 max_roll_days 顺延），排在
        # 当日信号之后，避免用当日收盘回款给当日早盘买入供资。顺延期间持仓
        # 仍在账上（不提前释放持仓额度与净值）。
        if exit_after_trading_days and day in cal_days:
            for h in list(ledger.lots):
                if h.exit_pending or h.expiry_date != day:
                    continue
                ledger.submit_sell(h.symbol, h.signal_date or day, day,
                                   reason="holding_period_expiry")

        # 收盘：成交日已到的卖出入账（加现金、退持仓）。
        ledger.apply_due_fills(day, "close")

    # 3) 逐日净值（按成交日重放；不得用末态 cash/positions 回算）
    if not cal:
        nav: list[tuple[str, float]] = []
    else:
        start = mark_start or cal[0]
        end = mark_end or cal[-1]
        nav = _nav_series_from_entries(initial_cash, ledger.entries, get_bar,
                                       cal, start, end)

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
              nav: Sequence[tuple[str, float]]) -> dict[str, Any]:
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


def compare_strategies(results: Sequence[PortfolioResult]) -> dict[str, Any]:
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
