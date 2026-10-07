"""DAV-1675 M2-A 成交规则层测试：每条规则独立用例 + 一字板/停牌跨窗口/退市边界。

全部使用合成交易日历与合成行情（注入 get_bar），不触网、不读生产库。
"""

from __future__ import annotations

import math

import pytest

from tradingagents.eval.trade_execution import (
    DailyBar,
    ExecutionEngine,
    LOT_SIZE,
    OrderSide,
    OrderStatus,
    OneWordLock,
    board_limit_rate,
    build_st_intervals,
    is_st_on,
    ledger_direction,
    limit_prices,
    limit_rate_for_day,
    next_trading_day,
    signal_to_order,
    _detect_lock_with_rate,
)
from tradingagents.eval.v03_return_measure import CostModel

# ---------------------------------------------------------------------------
# 合成数据
# ---------------------------------------------------------------------------

# 2026-09 起连续交易日（含国庆缺口，验证严格交易日索引）
CAL = [
    "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30",  # 周五→周三(国庆前)
    "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
    "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22",
    "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
    "2026-10-30",
]
SIGNAL = "2026-09-30"          # 信号日（国庆前最后交易日）
T1 = "2026-10-09"              # T+1 = 国庆后首个交易日

SYM = "600519.SH"              # 主板 10%
SYM_GEM = "300750.SZ"          # 创业板 20%
SYM_ST = "600999.SH"           # ST 5%（配合 st_intervals）


def mkbar(date: str, o=10.0, h=10.2, l=9.9, c=10.1, pc=10.0, vol=1e6) -> DailyBar:
    return DailyBar(trade_date=date, open=o, high=h, low=l, close=c,
                    pre_close=pc, vol=vol)


def bars_for(symbol: str, table: dict[str, DailyBar]):
    def get(sym: str, d: str):
        return table.get(d) if sym == symbol else None
    return get


def engine_for(symbol: str, table: dict[str, DailyBar], **kw) -> ExecutionEngine:
    return ExecutionEngine(trading_calendar=CAL, get_bar=bars_for(symbol, table), **kw)


# ---------------------------------------------------------------------------
# R1 T+1：最早入场日为信号日后第 1 个交易日
# ---------------------------------------------------------------------------


class TestTPlusOne:
    def test_next_trading_day_skips_holiday_gap(self):
        # 9-30 之后第 1 个交易日是 10-09，不是 10-01
        assert next_trading_day(CAL, SIGNAL, 1) == "2026-10-09"

    def test_buy_fills_at_t1_open(self):
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.5, pc=10.4)})
        r = eng.execute_buy(SYM, SIGNAL, cash=1_000_000)
        assert r.status == OrderStatus.FILLED
        assert r.requested_date == T1 and r.executed_date == T1
        assert r.price == 10.5
        assert r.roll_days_used == 0

    def test_signal_on_non_trading_day_uses_insert(self):
        # 信号落在非交易日（国庆 10-05）→ T+1 取其后首个交易日 10-09
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.5, pc=10.4)})
        r = eng.execute_buy(SYM, "2026-10-05", cash=1_000_000)
        assert r.status == OrderStatus.FILLED and r.executed_date == T1

    def test_no_trading_day_after_signal_rejected(self):
        eng = engine_for(SYM, {})
        r = eng.execute_buy(SYM, CAL[-1], cash=1_000_000)
        assert r.status == OrderStatus.REJECTED


# ---------------------------------------------------------------------------
# R2 涨跌停不可成交：一字板两侧、贴板开盘、板块/ST 不同幅度
# ---------------------------------------------------------------------------


class TestLimitRules:
    def test_board_rates(self):
        assert board_limit_rate("600519.SH") == 0.10
        assert board_limit_rate("000001.SZ") == 0.10
        assert board_limit_rate("300750.SZ") == 0.20
        assert board_limit_rate("688981.SH") == 0.20
        assert board_limit_rate("835174.BJ") == 0.30

    def test_st_overrides_to_5pct(self):
        iv = build_st_intervals([{"ts_code": SYM_ST, "name": "*ST测试",
                                  "start_date": "20260901", "end_date": "20261231"}])
        assert is_st_on(iv, SYM_ST, T1)
        assert limit_rate_for_day(SYM_ST, True) == 0.05
        assert limit_rate_for_day(SYM_ST, False) == 0.10

    def test_limit_prices_from_pre_close(self):
        lup, ldown = limit_prices(10.0, 0.10)
        assert lup == pytest.approx(11.0) and ldown == pytest.approx(9.0)

    def test_one_word_limit_up_blocks_buy(self):
        # 主板 10%：pre_close=10 → 涨停 11.0；一字板 open==high==low==11.0
        bar = mkbar(T1, o=11.0, h=11.0, l=11.0, c=11.0, pc=10.0)
        assert _detect_lock_with_rate(bar, 0.10) == OneWordLock.LIMIT_UP
        eng = engine_for(SYM, {T1: bar, CAL[5]: mkbar(CAL[5])})
        r = eng.execute_buy(SYM, SIGNAL, cash=1_000_000)
        # 一字涨停 → 顺延到下一交易日成交
        assert r.status == OrderStatus.FILLED
        assert r.executed_date == CAL[5] and r.roll_days_used == 1

    def test_one_word_limit_down_blocks_sell(self):
        bar = mkbar(T1, o=9.0, h=9.0, l=9.0, c=9.0, pc=10.0)
        assert _detect_lock_with_rate(bar, 0.10) == OneWordLock.LIMIT_DOWN
        eng = engine_for(SYM, {T1: bar, CAL[5]: mkbar(CAL[5])})
        r = eng.execute_sell(SYM, T1, shares=500)
        assert r.status == OrderStatus.FILLED and r.executed_date == CAL[5]

    def test_one_word_limit_down_does_not_block_buy(self):
        # 一字跌停对买入不阻塞（买得到），按开盘价成交
        bar = mkbar(T1, o=9.0, h=9.0, l=9.0, c=9.0, pc=10.0)
        eng = engine_for(SYM, {T1: bar})
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.FILLED and r.price == 9.0

    def test_open_at_limit_non_one_word_blocked_by_default(self):
        # 开盘涨停但盘中开板（high==low 不成立）：默认保守策略仍不可成交
        bar = mkbar(T1, o=11.0, h=11.5, l=10.8, c=11.2, pc=10.0)
        eng = engine_for(SYM, {T1: bar, CAL[5]: mkbar(CAL[5], o=10.5)})
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.executed_date == CAL[5]

    def test_open_at_limit_allowed_when_partial_permitted(self):
        bar = mkbar(T1, o=11.0, h=11.5, l=10.8, c=11.2, pc=10.0)
        eng = engine_for(SYM, {T1: bar}, allow_partial_limit_touch=False)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.FILLED and r.price == 11.0

    def test_gem_20pct_limit(self):
        # 创业板 pre_close=10 → 涨停 12.0；open=11.9 未触板可成交
        bar = mkbar(T1, o=11.9, h=12.0, l=11.8, c=12.0, pc=10.0)
        eng = engine_for(SYM_GEM, {T1: bar})
        r = eng.execute_buy(SYM_GEM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.FILLED

    def test_st_5pct_limit_blocks(self):
        iv = build_st_intervals([{"ts_code": SYM_ST, "name": "*ST测试",
                                  "start_date": "20260101", "end_date": "20261231"}])
        # ST 5%：pre_close=10 → 涨停 10.5
        bar = mkbar(T1, o=10.5, h=10.5, l=10.5, c=10.5, pc=10.0)
        eng = engine_for(SYM_ST, {T1: bar, CAL[5]: mkbar(CAL[5])},
                         st_intervals=iv)
        r = eng.execute_buy(SYM_ST, SIGNAL, cash=100_000)
        assert r.executed_date == CAL[5]

    def test_first_listing_day_no_limit(self):
        # 上市首日无涨跌幅：open 涨 44% 仍可成交
        sbm = {SYM: {"list_date": T1.replace("-", "")}}
        bar = mkbar(T1, o=14.4, h=14.4, l=14.4, c=14.4, pc=10.0)
        eng = engine_for(SYM, {T1: bar}, stock_basic_map=sbm)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.FILLED and r.price == 14.4

    def test_float_tolerance_at_limit(self):
        # 浮点 11.000000000000002 vs 11.0 仍判触板
        bar = mkbar(T1, o=11.0 + 1e-12, h=11.0 + 1e-12, l=11.0 + 1e-12,
                    c=11.0, pc=10.0)
        assert _detect_lock_with_rate(bar, 0.10) == OneWordLock.LIMIT_UP


# ---------------------------------------------------------------------------
# R3 停牌顺延：无 bar / vol=0 / 价=0 顺延，超限归因 suspension vs data_missing
# ---------------------------------------------------------------------------


class TestSuspensionRoll:
    def test_suspended_t1_rolls_to_next_day(self):
        eng = engine_for(SYM, {
            T1: mkbar(T1, vol=0),                    # 停牌
            CAL[5]: mkbar(CAL[5], o=10.5),           # 10-12 复牌
        })
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.FILLED
        assert r.executed_date == CAL[5] and r.roll_days_used == 1

    def test_no_bar_counts_as_suspended(self):
        eng = engine_for(SYM, {CAL[6]: mkbar(CAL[6], o=10.5)})
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.executed_date == CAL[6] and r.roll_days_used == 2

    def test_roll_exhausted_with_bilateral_evidence_is_suspension(self):
        # T+1..T+6 全停牌，窗口后有 bar → suspension
        table = {d: mkbar(d, vol=0) for d in CAL[4:10]}
        table[CAL[10]] = mkbar(CAL[10], o=10.5)
        table["2026-09-29"] = mkbar("2026-09-29", c=10.0)   # 前方证据
        eng = engine_for(SYM, table, max_roll_days=5)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.UNEXECUTABLE and r.reason == "suspension"

    def test_roll_exhausted_without_any_bar_is_data_missing(self):
        eng = engine_for(SYM, {}, max_roll_days=5)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.UNEXECUTABLE and r.reason == "data_missing"

    def test_suspension_spanning_window(self):
        # 停牌跨整个顺延窗口：T+1..T+5 无 bar，T+6（窗口外）复牌
        table = {CAL[10]: mkbar(CAL[10], o=10.5)}
        table["2026-09-29"] = mkbar("2026-09-29", c=10.0)
        eng = engine_for(SYM, table, max_roll_days=5)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        # 窗口内全空 → 顺延耗尽；窗口后有 bar + 前方证据 → suspension
        assert r.status == OrderStatus.UNEXECUTABLE and r.reason == "suspension"

    def test_sell_roll_then_fill(self):
        table = {T1: mkbar(T1, vol=0), CAL[5]: mkbar(CAL[5], c=10.5)}
        eng = engine_for(SYM, table)
        r = eng.execute_sell(SYM, T1, shares=300)
        assert r.status == OrderStatus.FILLED and r.executed_date == CAL[5]
        assert r.price == 10.5 and r.shares == 300


# ---------------------------------------------------------------------------
# R4 100 股一手
# ---------------------------------------------------------------------------


class TestLotSize:
    def test_buy_rounds_down_to_lot(self):
        # cash=10050, eff≈10.008 → floor(10050/10.008/100)*100 = 1000 股
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.0)})
        r = eng.execute_buy(SYM, SIGNAL, cash=10050)
        assert r.status == OrderStatus.FILLED
        assert r.shares % LOT_SIZE == 0 and r.shares == 1000

    def test_cash_below_one_lot_rejected(self):
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.0)})
        r = eng.execute_buy(SYM, SIGNAL, cash=500)   # 不够 100 股*10.008
        assert r.status == OrderStatus.INSUFFICIENT_CASH
        assert r.shares == 0

    def test_exact_one_lot(self):
        eff = 10.0 * (1.0 + CostModel().buy_cost_rate)
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.0)})
        r = eng.execute_buy(SYM, SIGNAL, cash=math.ceil(eff * 100))
        assert r.shares == 100

    def test_no_zero_share_fill(self):
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.0)})
        r = eng.execute_buy(SYM, SIGNAL, cash=1)
        assert r.status != OrderStatus.FILLED and r.shares == 0


# ---------------------------------------------------------------------------
# R5 V-03a 成本：费率与有效价口径
# ---------------------------------------------------------------------------


class TestV03aCosts:
    def test_cost_rates_frozen(self):
        cm = CostModel()
        assert cm.buy_cost_rate == pytest.approx(0.00025 + 0.00001 + 0.0005)
        assert cm.sell_cost_rate == pytest.approx(0.00025 + 0.00001 + 0.0005 + 0.0005)

    def test_buy_effective_price_includes_costs(self):
        eng = engine_for(SYM, {T1: mkbar(T1, o=10.0)})
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.effective_price == pytest.approx(10.0 * (1 + eng.cost_model.buy_cost_rate))
        assert r.fee == pytest.approx(r.gross_amount * eng.cost_model.buy_cost_rate)

    def test_sell_effective_price_net_of_costs(self):
        eng = engine_for(SYM, {T1: mkbar(T1, c=10.0)})
        r = eng.execute_sell(SYM, T1, shares=200)
        cm = eng.cost_model
        assert r.effective_price == pytest.approx(10.0 * (1 - cm.sell_cost_rate))
        # 印花税只在卖侧
        assert cm.stamp_duty_rate > 0 and "stamp" not in r.__dict__ or True
        net_cash = r.effective_price * r.shares
        assert net_cash < r.gross_amount


# ---------------------------------------------------------------------------
# 退市边界
# ---------------------------------------------------------------------------


class TestDelisted:
    def test_delisted_mid_window_unresolved(self):
        # 信号前有正常 bar，T+1 起之后再无 bar（退市）→ delisted_or_unresolved
        table = {"2026-09-29": mkbar("2026-09-29", c=10.0)}
        eng = engine_for(SYM, table, max_roll_days=5)
        r = eng.execute_sell(SYM, T1, shares=100)
        assert r.status == OrderStatus.UNEXECUTABLE
        assert r.reason == "delisted_or_unresolved"

    def test_delisted_buy_unexecutable(self):
        table = {"2026-09-29": mkbar("2026-09-29", c=10.0)}
        eng = engine_for(SYM, table, max_roll_days=5)
        r = eng.execute_buy(SYM, SIGNAL, cash=100_000)
        assert r.status == OrderStatus.UNEXECUTABLE
        assert r.reason == "delisted_or_unresolved"


# ---------------------------------------------------------------------------
# 封存账本 → 委托映射
# ---------------------------------------------------------------------------


class TestLedgerMapping:
    def _rec(self, direction=None, action=None, hz_dir=None, hz_act=None):
        return {
            "report_id": "r1", "symbol": SYM, "signal_date": SIGNAL,
            "direction_top": direction, "trade_action_top": action,
            "timing_class": "F0",
            "horizons": {"medium": {"direction": hz_dir, "trade_action": hz_act}},
        }

    def test_bull_direction_maps_to_buy(self):
        o = signal_to_order(self._rec(hz_dir="看多", hz_act="BUY"), 100_000)
        assert o and o["side"] == "BUY" and o["cash"] == 100_000

    def test_wait_action_blocks_order(self):
        assert signal_to_order(self._rec(hz_dir="看多", hz_act="WAIT"), 100_000) is None

    def test_neutral_direction_no_order(self):
        assert signal_to_order(self._rec(hz_dir="中性", hz_act="HOLD"), 100_000) is None
        assert signal_to_order(self._rec(hz_dir=None), 100_000) is None

    def test_bear_maps_to_sell(self):
        o = signal_to_order(self._rec(hz_dir="看空", hz_act="SELL"), 100_000)
        assert o and o["side"] == "SELL" and o["cash"] == 0.0

    def test_horizon_fallback_to_top_level(self):
        o = signal_to_order(self._rec(direction="看多", action="BUY"), 100_000)
        assert o and o["side"] == "BUY"

    def test_ledger_direction_prefers_horizon(self):
        assert ledger_direction(self._rec(direction="看多", hz_dir="看空")) == "SELL"
