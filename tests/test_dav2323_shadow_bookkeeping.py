"""DAV-2323：shadow_portfolio.py 四项记账缺陷回归（源自 DAV-1988 审查）。

1. 净值必须按成交日逐日重放——不得用末态 cash/positions 回算历史；
   后加入的记录不得改写历史净值；T+1 成交的持仓不计入信号日估值。
2. 标准持仓出口 = 信号日起第 40 个交易日收盘（可关闭）；到期退出排在当日
   信号之后，停牌按成交层顺延，早于该日的 SELL 仍走 T+1 收盘。
3. S1 ``direction`` 忽略风控输出层：默认 ``RiskRules`` 的 BLOCKED /
   require_risk_clear / min_confidence 不再污染 S1；S2 仍受其约束。
   （组合层约束——限仓数 / 每日新开 / 单仓权重 / 冷却——三策略一致。）
4. S2 缺最终动作记不可用：不回退到原始 direction，避免退化成 S1。

全部使用合成交易日历与合成行情（注入 get_bar），0 网络、0 模型调用、
不读生产库。
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradingagents.eval.shadow_portfolio import (
    RiskRules,
    ledger_final_action,
    risk_rules_v2,
    run_shadow_portfolio,
    strategy_direction_intent,
    strategy_risk_adjusted_intent,
)
from tradingagents.eval.trade_execution import DailyBar, ExecutionEngine

SYM = "600519.SH"
SYM2 = "000001.SZ"


def business_days(n: int, start: str = "2026-01-05") -> list[str]:
    """连续工作日合成日历（不含节假日，仅用于确定性单测）。"""
    d = dt.date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def mkbar(date: str, o: float = 10.0, c: float = 10.0,
          vol: float = 1e6) -> DailyBar:
    return DailyBar(trade_date=date, open=o, high=max(o, c) * 1.01,
                    low=min(o, c) * 0.99, close=c, pre_close=o, vol=vol)


def rec(symbol=SYM, signal_date="2026-01-05", direction="看多", action="BUY",
        risk="OK", conf=80, prob=0.7, decision="BUY", rid="r1",
        sealed_at="2026-01-04 19:00:00"):
    return {
        "report_id": rid, "symbol": symbol, "signal_date": signal_date,
        "sealed_at": sealed_at,
        "direction_top": direction, "trade_action_top": action,
        "risk_status_top": risk, "decision_top": decision,
        "probability_top": prob, "confidence_top": conf,
        "timing_class": "F0",
        "horizons": {"medium": {
            "direction": direction, "trade_action": action,
            "risk_status": risk, "decision": decision,
            "confidence": conf, "probability": prob,
        }},
    }


def flat_engine(cal: list[str], bars: dict | None = None) -> ExecutionEngine:
    """所有交易日默认同一价（10 元）可成交；可覆写单日 bar。"""
    table = {d: mkbar(d) for d in cal}
    if bars:
        table.update(bars)

    def get_bar(sym, d):
        return table.get(d)

    return ExecutionEngine(trading_calendar=list(cal), get_bar=get_bar)


def rules_unlimited() -> RiskRules:
    return RiskRules(name="test_unlim", max_positions=0, max_new_per_day=0,
                     position_weight=0.5, min_confidence=None,
                     require_risk_clear=False, respect_blocked=False,
                     cooldown_per_symbol=0)


def sym_engine(cal: list[str], suspend: set | None = None):
    """按 (标的, 日期) 停牌的合成行情：停牌日 vol=0，其余一律 10 元可成交。"""
    halted = suspend or set()

    def get_bar(sym, d):
        if (sym, d) in halted:
            return mkbar(d, vol=0)
        return mkbar(d, o=10.0, c=10.0)

    return ExecutionEngine(trading_calendar=list(cal), get_bar=get_bar)


def _run_sym(cal, records, suspend=None, rules=None, **kwargs):
    return run_shadow_portfolio(
        records, strategy_fn=strategy_direction_intent,
        engine=sym_engine(cal, suspend), rules=rules or rules_unlimited(),
        initial_cash=100_000, strategy_name="s", **kwargs)


def _min_replayed_cash(entries, initial_cash=100_000.0) -> float:
    """按真实成交日重放现金，返回过程中的最低现金（负数=隐含融资）。"""
    cash = float(initial_cash)
    low = cash
    for e in sorted(entries, key=lambda x: x.trade_date):
        cash += (-1 if e.side == "BUY" else 1) * e.effective_price * e.shares
        low = min(low, cash)
    return low


def _run(cal, records, strategy_fn=strategy_direction_intent, rules=None,
         bars=None, **kwargs):
    return run_shadow_portfolio(
        records, strategy_fn=strategy_fn,
        engine=flat_engine(cal, bars), rules=rules or rules_unlimited(),
        initial_cash=100_000, strategy_name="s", **kwargs)


# ---------------------------------------------------------------------------
# 1. 净值 PIT：不得用末态 cash/positions 回算历史
# ---------------------------------------------------------------------------


class TestPointInTimeNav:
    def test_signal_day_nav_excludes_t1_fill(self):
        """信号日收盘净值 = 初始现金（T+1 才买入），不是末态持仓×当日价。"""
        cal = business_days(6)
        # 信号日收盘 20 元，之后回到 10 元：末态回算会把 T+1 买入的持仓
        # 按 20 元计入信号日净值（约 148,962 元），PIT 口径应为 100,000 元。
        bars = {cal[0]: mkbar(cal[0], 20.0, 20.0)}
        res = _run(cal, [rec(signal_date=cal[0])], bars=bars)

        buy = next(e for e in res.entries if e.side == "BUY")
        assert buy.trade_date == cal[1]
        assert res.nav_series[0] == (cal[0], 100_000.0)
        expected_t1 = 100_000 - buy.effective_price * buy.shares + buy.shares * 10.0
        assert res.nav_series[1][1] == pytest.approx(expected_t1)

    def test_future_record_does_not_rewrite_history(self):
        """后加入的晚信号不得改写其成交日之前的历史净值（前缀稳定）。"""
        cal = business_days(10)
        a = _run(cal, [rec(signal_date=cal[0])])
        b = _run(cal, [rec(signal_date=cal[0]),
                       rec(symbol=SYM2, signal_date=cal[6], rid="r2")])

        assert a.nav_series[:7] == b.nav_series[:7]
        assert a.nav_series != b.nav_series
        assert a.summary["n_buys"] == 1
        assert b.summary["n_buys"] == 2

    def test_flat_price_nav_starts_exactly_at_initial_cash(self):
        """无持仓日净值应精确等于现金，不含任何未来持仓估值。"""
        cal = business_days(5)
        res = _run(cal, [rec(signal_date=cal[2])])
        assert [v for _, v in res.nav_series[:3]] == [100_000.0] * 3

    def test_nav_series_still_marks_open_position_after_fill(self):
        cal = business_days(6)
        bars = {d: mkbar(d, 10.0, 12.0) for d in cal[2:]}
        res = _run(cal, [rec(signal_date=cal[0])], bars=bars)
        buy = next(e for e in res.entries if e.side == "BUY")
        # cal[1] 成交后，cal[2] 起按 12 元收盘估值
        assert res.nav_series[2][1] == pytest.approx(
            res.nav_series[1][1] + buy.shares * 2.0)


# ---------------------------------------------------------------------------
# 2. 标准持仓出口：信号日起第 40 个交易日收盘
# ---------------------------------------------------------------------------


class TestExpiryExit:
    def test_expiry_exit_at_signal_plus_40_close(self):
        cal = business_days(46)
        res = _run(cal, [rec(signal_date=cal[0])])
        sells = [e for e in res.entries if e.side == "SELL"]
        assert len(sells) == 1
        sell = sells[0]
        assert sell.reason == "holding_period_expiry"
        assert sell.signal_date == cal[0]
        assert sell.trade_date == cal[40]
        assert sell.price == 10.0
        assert res.summary["open_positions"] == {}
        assert res.n_filled_sells == 1

    def test_expiry_can_be_disabled(self):
        cal = business_days(46)
        res = _run(cal, [rec(signal_date=cal[0])],
                   exit_after_trading_days=0)
        assert [e for e in res.entries if e.side == "SELL"] == []
        assert res.summary["open_positions"] != {}

    def test_expiry_rolls_forward_on_suspension(self):
        """到期日停牌：按成交层顺延到下一可成交日，不提前不放弃。"""
        cal = business_days(46)
        bars = {cal[40]: mkbar(cal[40], vol=0)}
        res = _run(cal, [rec(signal_date=cal[0])], bars=bars)
        sell = next(e for e in res.entries if e.side == "SELL")
        assert sell.reason == "holding_period_expiry"
        assert sell.trade_date == cal[41]

    def test_early_sell_signal_wins_over_expiry(self):
        cal = business_days(46)
        res = _run(cal, [
            rec(signal_date=cal[0], rid="a"),
            rec(direction="看空", action="SELL", signal_date=cal[5], rid="b"),
        ])
        sells = [e for e in res.entries if e.side == "SELL"]
        assert len(sells) == 1
        assert sells[0].reason != "holding_period_expiry"
        assert sells[0].trade_date == cal[6]
        assert res.summary["open_positions"] == {}

    def test_expiry_does_not_fire_for_reopened_position(self):
        """抢占重开后的持仓按新信号起算 40 日，不被旧信号到期日误平。"""
        cal = business_days(46)
        res = _run(cal, [
            rec(signal_date=cal[0], rid="a"),
            rec(signal_date=cal[5], rid="b"),
        ])
        expiry_sells = [e for e in res.entries
                        if e.side == "SELL"
                        and e.reason == "holding_period_expiry"]
        assert len(expiry_sells) == 1
        assert expiry_sells[0].signal_date == cal[5]
        assert expiry_sells[0].trade_date == cal[45]


# ---------------------------------------------------------------------------
# 3. S1 不受风控输出层污染；组合层约束仍然生效
# ---------------------------------------------------------------------------


class TestStrategyRiskIsolation:
    def test_s1_ignores_blocked_while_s2_respects_it(self):
        cal = business_days(6)
        r = rec(signal_date=cal[0], risk="BLOCKED", action="BUY", conf=90)

        s1 = _run(cal, [r], strategy_fn=strategy_direction_intent,
                  rules=risk_rules_v2())
        s2 = _run(cal, [r], strategy_fn=strategy_risk_adjusted_intent,
                  rules=risk_rules_v2())

        assert s1.summary["n_buys"] == 1
        assert s1.n_blocked == 0
        assert s2.summary["n_buys"] == 0
        assert s2.n_blocked == 1

    def test_s1_ignores_confidence_and_risk_clear_gates(self):
        cal = business_days(6)
        rules = RiskRules(name="gates", min_confidence=90,
                          require_risk_clear=True, position_weight=0.5)
        r = rec(signal_date=cal[0], conf=50, risk="ELEVATED", action="BUY")

        s1 = _run(cal, [r], strategy_fn=strategy_direction_intent, rules=rules)
        s2 = _run(cal, [r], strategy_fn=strategy_risk_adjusted_intent, rules=rules)

        assert s1.summary["n_buys"] == 1 and s1.n_blocked == 0
        assert s2.summary["n_buys"] == 0 and s2.n_blocked == 1

    def test_s1_still_subject_to_portfolio_caps(self):
        """忽略风控输出层不等于豁免组合层约束（限仓数）。"""
        cal = business_days(6)
        rules = RiskRules(name="cap", max_positions=1, position_weight=0.5)
        res = _run(cal, [
            rec(symbol=SYM, signal_date=cal[0], rid="a"),
            rec(symbol=SYM2, signal_date=cal[0], rid="b"),
        ], rules=rules)
        assert res.summary["n_buys"] == 1
        assert res.n_blocked == 1

    def test_s1_still_subject_to_cooldown(self):
        cal = business_days(10)
        rules = RiskRules(name="cool", cooldown_per_symbol=5,
                          position_weight=0.5)
        res = _run(cal, [
            rec(signal_date=cal[0], rid="a"),
            rec(signal_date=cal[2], rid="b"),
        ], rules=rules)
        assert res.n_blocked == 1


# ---------------------------------------------------------------------------
# 4. S2 缺最终动作记不可用，不回退 direction
# ---------------------------------------------------------------------------


class TestS2NoDirectionFallback:
    def _direction_only(self):
        r = rec(direction="看多", action=None, decision=None)
        r["horizons"]["medium"]["trade_action"] = None
        r["horizons"]["medium"]["decision"] = None
        return r

    def test_final_action_none_without_trade_action_or_decision(self):
        assert ledger_final_action(self._direction_only()) is None

    def test_s2_yields_no_intent_for_direction_only_record(self):
        cal = business_days(6)
        res = _run(cal, [self._direction_only()],
                   strategy_fn=strategy_risk_adjusted_intent)
        assert res.n_signals == 0
        assert res.summary["n_buys"] == 0

    def test_s1_and_s2_diverge_instead_of_silently_matching(self):
        cal = business_days(6)
        r = self._direction_only()
        s1 = _run(cal, [r], strategy_fn=strategy_direction_intent)
        assert s1.summary["n_buys"] == 1

    def test_s2_still_uses_decision_when_trade_action_missing(self):
        cal = business_days(6)
        r = rec(signal_date=cal[0], action=None, decision="BUY")
        r["horizons"]["medium"]["trade_action"] = None
        res = _run(cal, [r], strategy_fn=strategy_risk_adjusted_intent)
        assert res.summary["n_buys"] == 1


# ---------------------------------------------------------------------------
# 5. 委托解析与成交入账分离（DAV-2344）
#
# 成交层可把成交日顺延到未来（停牌/锁板）。修复前：卖出一定价即加现金、删
# 持仓，买入一定价即扣现金——回款早到、买入现金早扣。下面四类用例均在修复
# 前源码上失败、在修复后通过。
# ---------------------------------------------------------------------------


def _expiry_roll_setup():
    """A 于 cal[0] 买入，第 40 个交易日到期但停牌 5 日，卖出顺延到 cal[45]。"""
    cal = business_days(52)
    suspend = {(SYM, cal[d]) for d in range(40, 45)}
    return cal, suspend


class TestRolledSellDoesNotReleaseFutureProceeds:
    """🟡1：到期卖出因停牌顺延时，回款与持仓额度不得提前释放。"""

    def test_rolled_expiry_sell_does_not_fund_intermediate_buy(self):
        """顺延期间不得用尚未发生的回款给新买单供资（禁止隐含融资）。"""
        cal, suspend = _expiry_roll_setup()
        rules = RiskRules(name="full", position_weight=1.0,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=1.0, rid="a"),
            rec(symbol=SYM2, signal_date=cal[41], prob=1.0, rid="b"),
        ], suspend=suspend, rules=rules)

        buys = [e for e in res.entries if e.side == "BUY"]
        sells = [e for e in res.entries if e.side == "SELL"]
        # 现金已全仓占用，回款要到 cal[45] 才到账 → 新信号无钱可买
        assert [e.symbol for e in buys] == [SYM]
        assert len(sells) == 1
        assert sells[0].reason == "holding_period_expiry"
        assert sells[0].trade_date == cal[45]
        # 按真实成交时间重放：现金始终非负
        assert _min_replayed_cash(res.entries) >= 0.0

    def test_rolled_expiry_sell_keeps_position_slot_until_fill(self):
        """顺延期间持仓仍在账上：限仓额度不得提前释放。"""
        cal, suspend = _expiry_roll_setup()
        rules = RiskRules(name="cap", max_positions=1, position_weight=0.5,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], rid="a"),
            rec(symbol=SYM2, signal_date=cal[41], rid="b"),
        ], suspend=suspend, rules=rules)

        assert [e.symbol for e in res.entries if e.side == "BUY"] == [SYM]
        assert res.n_blocked == 1
        assert res.summary["open_positions"] == {}

    def test_entries_are_booked_in_chronological_fill_order(self):
        """账本按真实成交时序追加：cash_after 不得靠写入顺序掩盖融资。"""
        cal, suspend = _expiry_roll_setup()
        rules = RiskRules(name="full", position_weight=1.0,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=1.0, rid="a"),
            rec(symbol=SYM2, signal_date=cal[41], prob=1.0, rid="b"),
        ], suspend=suspend, rules=rules)

        dates = [e.trade_date for e in res.entries]
        assert dates == sorted(dates)
        # 每笔 cash_after 与按成交时序重放的余额一致
        cash = 100_000.0
        for e in res.entries:
            cash += (-1 if e.side == "BUY" else 1) * e.effective_price * e.shares
            assert e.cash_after == pytest.approx(cash, abs=1e-6)
            assert e.cash_after >= 0.0

    def test_preempt_proceeds_not_used_by_same_day_open_buy(self):
        """同一根因：先平后开时，当日开盘买入不得用当日收盘卖出的回款。"""
        cal = business_days(20)
        rules = RiskRules(name="full", position_weight=1.0,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=0.5, rid="a"),
            rec(symbol=SYM, signal_date=cal[5], prob=1.0, rid="b"),
        ], rules=rules)

        buys = [e for e in res.entries if e.side == "BUY"]
        sells = [e for e in res.entries if e.side == "SELL"]
        assert len(buys) == 2 and len(sells) == 1
        # 新仓只可用已入账现金（≈47,960.52 元）：4,700 股；若用当日收盘回款
        # 则可买 9,900 股（修复前的错误行为）。
        assert [e.shares for e in buys] == [5_200, 4_700]
        assert sells[0].trade_date == cal[6]
        assert _min_replayed_cash(res.entries) >= 0.0


class TestSameDayBuyBudgetUsesRealEquity:
    """🟡2：同日多标的买入的预算用同一时点真实权益，预留现金另行维护。"""

    def test_same_day_signals_get_identical_budget(self):
        """同日、同概率、同价：第二笔预算不得因第一笔预扣现金而递减。"""
        cal = business_days(10)
        rules = RiskRules(name="half", position_weight=0.5,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=1.0, rid="a"),
            rec(symbol=SYM2, signal_date=cal[0], prob=1.0, rid="b"),
        ], rules=rules)

        buys = [e for e in res.entries if e.side == "BUY"]
        # 同日信号按 (signal_date, symbol) 排序：000001.SZ 先于 600519.SH
        assert [e.symbol for e in buys] == [SYM2, SYM]
        # 同日、同概率、同价：两笔预算相等，不得因先订先扣现金而递减
        assert [e.shares for e in buys] == [4_900, 4_900]
        # 两笔实际成交日为同一个 T+1
        assert buys[0].trade_date == buys[1].trade_date == cal[1]
        assert _min_replayed_cash(res.entries) >= 0.0

    def test_same_day_budget_matches_standalone_budget(self):
        """同日同标的的预算等于它单独运行时的预算（不受同日其他标的干扰）。"""
        cal = business_days(10)
        rules = RiskRules(name="half", position_weight=0.5,
                          respect_blocked=False)
        # 取同日排序里的后位标的（600519.SH）作对照组：它才是被前位预扣影响的那个
        alone = _run_sym(cal, [rec(symbol=SYM, signal_date=cal[0], prob=1.0)],
                         rules=rules)
        pair = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=1.0, rid="a"),
            rec(symbol=SYM2, signal_date=cal[0], prob=1.0, rid="b"),
        ], rules=rules)

        assert alone.summary["n_buys"] == 1
        alone_shares = next(e for e in alone.entries if e.side == "BUY").shares
        pair_b = next(e for e in pair.entries
                      if e.side == "BUY" and e.symbol == SYM)
        assert pair_b.shares == alone_shares

    def test_reserved_buy_cash_still_prevents_overbuy(self):
        """预留额度仍是硬约束：同日两笔全仓买入不得超支（第二笔无钱可买）。"""
        cal = business_days(10)
        rules = RiskRules(name="full", position_weight=1.0,
                          respect_blocked=False)
        res = _run_sym(cal, [
            rec(symbol=SYM, signal_date=cal[0], prob=1.0, rid="a"),
            rec(symbol=SYM2, signal_date=cal[0], prob=1.0, rid="b"),
        ], rules=rules)

        assert [e.symbol for e in res.entries if e.side == "BUY"] == [SYM2]
        assert _min_replayed_cash(res.entries) >= 0.0

