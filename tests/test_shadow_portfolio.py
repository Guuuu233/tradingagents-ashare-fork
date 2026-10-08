"""DAV-1676 M2-B 影子组合·策略与记账层测试。

覆盖：
- 三套策略（方向/概率、风控后动作、高置信精选）在同一封存信号上的行为差异；
- 风控规则离线重放（risk_v1 vs risk_v2 不同版本）；
- 账本确定性：净值、换手、回撤由账本字段可重算；
- 边界：Blocked 抑制开仓、max_positions/max_new_per_day、置信门槛、
  同标的抢占（先平后开）、同日多信号确定性顺序。

全部使用合成交易日历与合成行情（注入 get_bar），不触网、不读生产库。
"""

from __future__ import annotations

import pytest
from tradingagents.eval.shadow_portfolio import (
    Intent,
    RiskRules,
    compare_strategies,
    ledger_confidence,
    ledger_final_action,
    ledger_probability,
    ledger_risk_status,
    load_forward_ledger,
    risk_rules_v1,
    risk_rules_v2,
    run_shadow_portfolio,
    strategy_direction_intent,
    strategy_high_confidence_intent,
    strategy_risk_adjusted_intent,
)
from tradingagents.eval.trade_execution import (
    DailyBar,
    ExecutionEngine,
)
from tradingagents.eval.v03_return_measure import CostModel

# ---------------------------------------------------------------------------
# 合成数据（与 test_trade_execution_rules 同套日历）
# ---------------------------------------------------------------------------

CAL = [
    "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30",
    "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
    "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22",
    "2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
    "2026-10-30",
]
SIGNAL = "2026-09-30"
T1 = "2026-10-09"
SYM = "600519.SH"
SYM2 = "000001.SZ"


def mkbar(date, o=10.0, h=10.2, l=9.9, c=10.1, pc=10.0, vol=1e6) -> DailyBar:
    return DailyBar(trade_date=date, open=o, high=h, low=l, close=c,
                    pre_close=pc, vol=vol)


def rec(symbol=SYM, signal_date=SIGNAL, direction="看多", action="BUY",
        risk="OK", conf=80, prob=0.7, decision="BUY", rid="r1",
        sealed_at="2026-10-04 19:51:38"):
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


def flat_engine(bars: dict | None = None, price: float = 10.0) -> ExecutionEngine:
    """所有交易日都返回同一价的可成交 bar。"""
    table = {d: mkbar(d, o=price, h=price * 1.01, l=price * 0.99, c=price)
             for d in CAL}
    if bars:
        table.update(bars)

    def get_bar(sym, d):
        return table.get(d)

    return ExecutionEngine(trading_calendar=CAL, get_bar=get_bar)


def rules_unlimited() -> RiskRules:
    return RiskRules(name="test_unlim", max_positions=0, max_new_per_day=0,
                     position_weight=0.5, min_confidence=None,
                     require_risk_clear=False, respect_blocked=False,
                     cooldown_per_symbol=0)


# ---------------------------------------------------------------------------
# 字段提取
# ---------------------------------------------------------------------------


class TestLedgerFields:
    def test_confidence_from_horizon(self):
        assert ledger_confidence(rec(conf=75)) == 75.0

    def test_confidence_fallback_top(self):
        r = rec(conf=80)
        r["horizons"]["medium"]["confidence"] = None
        r["confidence_top"] = 66
        assert ledger_confidence(r) == 66.0

    def test_probability_passthrough(self):
        assert ledger_probability(rec(prob=0.62)) == pytest.approx(0.62)

    def test_risk_status_norm(self):
        assert ledger_risk_status(rec(risk="BLOCKED")) == "BLOCKED"
        assert ledger_risk_status(rec(risk="ok")) == "OK"
        r = rec(risk=None)
        r["horizons"]["medium"]["risk_status"] = None
        r["risk_status_top"] = None
        assert ledger_risk_status(r) is None

    def test_final_action_trade_action_first(self):
        assert ledger_final_action(rec(action="SELL")) == "SELL"

    def test_final_action_falls_back_to_decision(self):
        r = rec(action=None, decision="BUY")
        r["horizons"]["medium"]["trade_action"] = None
        assert ledger_final_action(r) == "BUY"

    def test_final_action_falls_back_to_direction(self):
        r = rec(action=None, decision=None, direction="看空")
        r["horizons"]["medium"]["trade_action"] = None
        r["horizons"]["medium"]["decision"] = None
        assert ledger_final_action(r) == "SELL"


# ---------------------------------------------------------------------------
# 三套策略差异（同一封存信号）
# ---------------------------------------------------------------------------


class TestThreeStrategies:
    def test_direction_ignores_risk_output(self):
        # direction=BULL 但 trade_action=WAIT → S1 下单，S2 不下单
        r = rec(direction="看多", action="WAIT", conf=80)
        assert strategy_direction_intent(r) is not None
        assert strategy_risk_adjusted_intent(r) is None

    def test_risk_adjusted_uses_final_action(self):
        r = rec(direction="看多", action="BUY", conf=80)
        si = strategy_risk_adjusted_intent(r)
        assert si and si.intent == Intent.BUY and "final_action" in si.reason

    def test_blocked_risk_status_still_yields_s2_intent(self):
        # S2 不判 risk_status（组合层风控管）；只取 final action
        r = rec(action="BUY", risk="BLOCKED", conf=80)
        si = strategy_risk_adjusted_intent(r)
        assert si is not None and si.intent == Intent.BUY

    def test_high_confidence_filters_low_conf(self):
        assert strategy_high_confidence_intent(rec(conf=80)) is not None
        assert strategy_high_confidence_intent(rec(conf=50)) is None
        assert strategy_high_confidence_intent(rec(conf=None)) is None

    def test_high_confidence_still_respects_direction(self):
        r = rec(conf=90, direction=None)
        r["horizons"]["medium"]["direction"] = None
        r["direction_top"] = None
        assert strategy_high_confidence_intent(r) is None

    def test_neutral_no_intent_all_strategies(self):
        r = rec(direction="中性", action="HOLD", conf=90)
        assert strategy_direction_intent(r) is None
        assert strategy_risk_adjusted_intent(r) is None
        assert strategy_high_confidence_intent(r) is None


# ---------------------------------------------------------------------------
# 账本确定性：净值 / 换手 / 回撤 / 逐笔记账
# ---------------------------------------------------------------------------


class TestLedgerBookkeeping:
    def test_buy_then_sell_round_trip(self):
        eng = flat_engine(price=10.0)
        res = run_shadow_portfolio(
            [rec(direction="看多"), rec(symbol=SYM, signal_date="2026-10-15",
                                       direction="看空", action="SELL",
                                       rid="r2")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="direction",
        )
        assert res.summary["n_buys"] == 1
        assert res.summary["n_sells"] == 1
        # 平进平出同价：持仓部分收益 ≈ -双边成本率；总资产收益 = 持仓收益×仓位占比
        cm = CostModel()
        buy = next(e for e in res.entries if e.side == "BUY")
        sell = next(e for e in res.entries if e.side == "SELL")
        round_trip_asset = (
            sell.effective_price / buy.effective_price - 1.0
        ) * (buy.effective_price * buy.shares / 100_000)
        assert res.summary["total_return"] == pytest.approx(
            round_trip_asset, abs=1e-6)
        # 卖出有效价 < 买入有效价，差额约双边费率
        assert buy.effective_price > sell.effective_price
        assert 1 - sell.effective_price / buy.effective_price == pytest.approx(
            cm.round_trip_cost_rate, abs=1e-4)

    def test_nav_series_spans_calendar(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看多")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="direction",
        )
        assert res.nav_series[0][0] == CAL[0]
        assert res.nav_series[-1][0] == CAL[-1]
        assert res.summary["nav_points"] == len(CAL)

    def test_max_drawdown_calculation(self):
        # 先涨到 12（峰值），再跌回 10 → MDD = 1 - 10/12
        bars = {d: mkbar(d, o=10.0, c=10.0) for d in CAL[:10]}
        for d in CAL[10:15]:
            bars[d] = mkbar(d, o=12.0, c=12.0)
        for d in CAL[15:]:
            bars[d] = mkbar(d, o=10.0, c=10.0)
        eng = flat_engine(bars)
        res = run_shadow_portfolio(
            [rec(direction="看多", signal_date=CAL[0])],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="direction",
        )
        # 持仓部分回撤约 1-10/12≈16.7%，总资产回撤 ×仓位(0.5) ≈ 8%±
        # 实测须 > 0.05 且 < 0.10（不会低于持仓回撤×仓位，不超过裸持仓回撤）
        assert 0.05 < res.summary["max_drawdown"] < 0.10

    def test_determinism_identical_runs(self):
        eng = flat_engine()
        records = [rec(direction="看多"), rec(symbol=SYM2, direction="看多",
                                            rid="r2")]
        kw = dict(strategy_fn=strategy_direction_intent, engine=eng,
                  rules=rules_unlimited(), initial_cash=100_000)
        a = run_shadow_portfolio(records, strategy_name="s", **kw)
        b = run_shadow_portfolio(list(reversed(records)), strategy_name="s", **kw)
        assert a.summary == b.summary
        assert [(e.trade_date, e.symbol, e.shares) for e in a.entries] == \
               [(e.trade_date, e.symbol, e.shares) for e in b.entries]

    def test_same_day_same_symbol_arbitration_by_sealed_at(self):
        """同日同标的多条信号：按 sealed_at 先后执行（先封存的先成交），
        不再以 report_id 字典序仲裁。为 DAV-1690 复审问题回归用例。"""
        eng = flat_engine()
        # 同 signal_date + symbol，先封存看空 (sealed_at=...01)，后封存看多 (...02)
        # 按 sealed_at 执行：先 SELL (无持仓 noop) 再 BUY → 最终持仓非空
        r_sell_first = rec(direction="看空", action="SELL", rid="zz",
                           sealed_at="2026-10-04 19:51:01")
        r_buy_second = rec(direction="看多", action="BUY", rid="aa",
                           sealed_at="2026-10-04 19:51:02")
        res = run_shadow_portfolio(
            [r_buy_second, r_sell_first],   # 输入顺序颠倒以验证排序
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        # sealed_at 先者先执行：先 SELL（noop，无持仓），再 BUY（开仓）
        assert res.summary["n_buys"] == 1
        # 反向验证：若 report_id 序先生效，会先 BUY 再 SELL → 持仓为空
        assert res.summary["open_positions"] != {}

    def test_same_day_same_symbol_sealed_at_direction_conflict(self):
        """同 signal_date + symbol + 不同 sealed_at：
        看多先封存 → 看空后封存，结果应先 BUY 后 SELL（实际后封存的覆盖）。"""
        eng = flat_engine()
        r_buy_first = rec(direction="看多", action="BUY", rid="zz",
                          sealed_at="2026-10-04 19:51:01")
        r_sell_second = rec(direction="看空", action="SELL", rid="aa",
                            sealed_at="2026-10-04 19:51:02")
        res = run_shadow_portfolio(
            [r_sell_second, r_buy_first],   # 输入顺序颠倒
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        # sealed_at 仲裁：BUY(01) 先执行，SELL(02) 后执行 → 最终无持仓
        assert res.summary["n_buys"] == 1
        assert res.summary["n_sells"] == 1
        assert res.summary["open_positions"] == {}

    def test_same_sealed_at_falls_back_to_report_id(self):
        """同 signal_date + symbol + 同 sealed_at：退化为 report_id 字典序（稳定序）。"""
        eng = flat_engine()
        # report_id "aa" < "zz"：字典序先 BUY 后 SELL → 最终无持仓
        r_buy = rec(direction="看多", rid="aa", sealed_at="2026-10-04 19:51:38")
        r_sell = rec(direction="看空", rid="zz", sealed_at="2026-10-04 19:51:38")
        res = run_shadow_portfolio(
            [r_sell, r_buy],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["n_buys"] == 1
        assert res.summary["n_sells"] == 1
        assert res.summary["open_positions"] == {}

    def test_turnover_ratio_positive_on_trade(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看多")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["turnover_gross"] > 0
        assert res.summary["turnover_ratio"] > 0

    def test_no_signal_no_trades(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="中性", action="HOLD")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["n_trades"] == 0
        assert res.summary["total_return"] == 0.0


# ---------------------------------------------------------------------------
# 风控版本离线重放（同一封存信号跑不同版本）
# ---------------------------------------------------------------------------


class TestRiskRulesReplay:
    def _records(self):
        return [
            rec(symbol=SYM, direction="看多", action="BUY", conf=80, rid="a"),
            rec(symbol=SYM2, direction="看多", action="BUY", conf=80, rid="b"),
            rec(symbol="000002.SZ", direction="看多", action="BUY", conf=80,
                rid="c"),
            rec(symbol="000063.SZ", direction="看多", action="BUY", conf=80,
                rid="d"),
            rec(symbol="000100.SZ", direction="看多", action="BUY", conf=80,
                rid="e"),
            rec(symbol="000157.SZ", direction="看多", action="BUY", conf=80,
                rid="f"),
        ]

    def test_v1_vs_v2_different_outcomes(self):
        # 6 个信号分布在 3 个信号日（避开 max_new_per_day=2 干扰）
        recs = []
        for i, sym in enumerate(
                ("600519.SH", "000001.SZ", "000002.SZ",
                 "000063.SZ", "000100.SZ", "000157.SZ")):
            day = CAL[i // 2]          # 每天 2 个信号
            recs.append(rec(symbol=sym, direction="看多", action="BUY",
                            conf=80, signal_date=day, rid=f"r{i}"))
        eng = flat_engine()
        r1 = run_shadow_portfolio(recs,
                                  strategy_fn=strategy_direction_intent,
                                  engine=eng, rules=risk_rules_v1(),
                                  initial_cash=100_000, strategy_name="s")
        eng2 = flat_engine()
        r2 = run_shadow_portfolio(recs,
                                  strategy_fn=strategy_direction_intent,
                                  engine=eng2, rules=risk_rules_v2(),
                                  initial_cash=100_000, strategy_name="s")
        # v1 无限仓 → 6 仓；v2 max_positions=5 → 第 6 个买入被拦
        assert r1.summary["n_buys"] == 6
        assert r2.summary["n_buys"] == 5
        assert r2.n_blocked >= 1

    def test_blocked_risk_status_suppresses_buy(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看多", action="BUY", risk="BLOCKED", conf=90)],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=risk_rules_v2(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["n_buys"] == 0
        assert res.n_blocked == 1

    def test_blocked_does_not_block_sell(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看多", conf=90, rid="a"),
             rec(direction="看空", action="SELL", risk="BLOCKED", conf=90,
                 rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=risk_rules_v2(), initial_cash=100_000,
            strategy_name="s",
        )
        # BLOCKED 不抑制平仓：BUY 成交，SELL 也成交
        assert res.summary["n_buys"] == 1
        assert res.summary["n_sells"] == 1

    def test_require_risk_clear_filters_unknown_but_allows_ok(self):
        eng = flat_engine()
        rules = RiskRules(name="t", require_risk_clear=True,
                          position_weight=0.5)
        res = run_shadow_portfolio(
            [rec(direction="看多", risk="ELEVATED", conf=90, rid="a"),
             rec(symbol=SYM2, direction="看多", risk="OK", conf=90, rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules, initial_cash=100_000, strategy_name="s",
        )
        buys = [e.symbol for e in res.entries if e.side == "BUY"]
        assert buys == [SYM2]

    def test_max_new_per_day(self):
        eng = flat_engine()
        rules = RiskRules(name="t", max_new_per_day=1, position_weight=0.5)
        res = run_shadow_portfolio(
            [rec(symbol=SYM, direction="看多", rid="a"),
             rec(symbol=SYM2, direction="看多", rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules, initial_cash=100_000, strategy_name="s",
        )
        assert res.summary["n_buys"] == 1
        assert res.n_blocked == 1

    def test_min_confidence_threshold(self):
        eng = flat_engine()
        rules = RiskRules(name="t", min_confidence=70, position_weight=0.5)
        res = run_shadow_portfolio(
            [rec(direction="看多", conf=60, rid="a"),
             rec(symbol=SYM2, direction="看多", conf=90, rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules, initial_cash=100_000, strategy_name="s",
        )
        buys = [e.symbol for e in res.entries if e.side == "BUY"]
        assert buys == [SYM2]

    def test_cooldown_per_symbol(self):
        eng = flat_engine()
        rules = RiskRules(name="t", cooldown_per_symbol=5, position_weight=0.5)
        res = run_shadow_portfolio(
            [rec(direction="看多", signal_date=CAL[0], rid="a"),
             rec(direction="看多", signal_date=CAL[2], rid="b"),   # 距上次 2 日 < 5
             rec(direction="看多", signal_date=CAL[10], rid="c")], # 距上次 9 日 ≥ 5
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules, initial_cash=100_000, strategy_name="s",
        )
        # 第 2 个被冷却拦；第 3 个正常 → 共 2 次买（第 2 次冷却 + 第 3 次新买需先平）
        assert res.n_blocked == 1

    def test_same_rules_replay_bit_identical(self):
        eng = flat_engine()
        kw = dict(strategy_fn=strategy_direction_intent, engine=eng,
                  rules=risk_rules_v2(), initial_cash=200_000,
                  strategy_name="s")
        recs = [
            rec(symbol="600519.SH", direction="看多", conf=80, rid="a"),
            rec(symbol="000001.SZ", direction="看多", conf=80, rid="b"),
        ]
        a = run_shadow_portfolio(recs, **kw)
        b = run_shadow_portfolio(recs, **kw)
        assert a.summary == b.summary


# ---------------------------------------------------------------------------
# 策略对比 / 抢占 / 持仓
# ---------------------------------------------------------------------------


class TestPortfolioBehaviour:
    def test_preempt_existing_position_on_new_signal(self):
        eng = flat_engine()
        # 信号 1: 买 SYM；信号 2（更晚）: 又买 SYM → 先平旧仓再开新仓
        res = run_shadow_portfolio(
            [rec(symbol=SYM, direction="看多", signal_date=CAL[0], rid="a"),
             rec(symbol=SYM, direction="看多", signal_date=CAL[5], rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        # 1 卖（平仓） + 2 买（重开）
        assert res.summary["n_sells"] >= 1
        assert res.summary["n_buys"] == 2

    def test_sell_signal_closes_position(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看多", signal_date=CAL[0], rid="a"),
             rec(direction="看空", signal_date=CAL[8], rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["n_sells"] == 1
        assert res.summary["open_positions"] == {}

    def test_sell_executes_at_t1_close_not_signal_close(self):
        """T+1 收盘卖出：信号日 CAL[8] 的看空信号应在 CAL[9] 收盘成交，
        而非信号日当天。本用例为 DAV-1690 复审发现的卖出前视偏差回归用例。"""
        # 信号日 CAL[8] 价格 10，T+1 CAL[9] 收盘 12
        bars = {d: mkbar(d, o=10.0, c=10.0) for d in CAL}
        bars[CAL[9]] = mkbar(CAL[9], o=11.0, c=12.0)
        eng = flat_engine(bars)
        res = run_shadow_portfolio(
            [rec(direction="看多", signal_date=CAL[0], rid="a"),
             rec(direction="看空", signal_date=CAL[8], rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        sell = next(e for e in res.entries if e.side == "SELL")
        assert sell.trade_date == CAL[9]              # 信号日+1 交易日
        assert sell.signal_date == CAL[8]             # 保留原信号日
        assert sell.price == 12.0                     # 成交价为 T+1 收盘

    def test_preempt_sell_also_at_t1_close(self):
        """抢占平仓同样按 T+1 收盘，而非信号日收盘。"""
        bars = {d: mkbar(d, o=10.0, c=10.0) for d in CAL}
        bars[CAL[6]] = mkbar(CAL[6], o=15.0, c=15.0)
        eng = flat_engine(bars)
        res = run_shadow_portfolio(
            [rec(direction="看多", signal_date=CAL[0], rid="a"),
             rec(direction="看多", signal_date=CAL[5], rid="b")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        preempt_sell = next(e for e in res.entries if e.side == "SELL")
        assert preempt_sell.trade_date == CAL[6]
        assert preempt_sell.reason == "preempt_by_new_signal"

    def test_bear_signal_without_position_noop(self):
        eng = flat_engine()
        res = run_shadow_portfolio(
            [rec(direction="看空", rid="a")],
            strategy_fn=strategy_direction_intent,
            engine=eng, rules=rules_unlimited(), initial_cash=100_000,
            strategy_name="s",
        )
        assert res.summary["n_trades"] == 0

    def test_compare_strategies_table(self):
        eng = flat_engine()
        results = []
        for name in ("direction", "risk_adjusted", "high_confidence"):
            fn = {"direction": strategy_direction_intent,
                  "risk_adjusted": strategy_risk_adjusted_intent,
                  "high_confidence": strategy_high_confidence_intent}[name]
            results.append(run_shadow_portfolio(
                [rec(direction="看多", conf=80)],
                strategy_fn=fn, engine=eng, rules=risk_rules_v1(),
                initial_cash=100_000, strategy_name=name))
        table = compare_strategies(results)
        assert set(table) == {"direction", "risk_adjusted", "high_confidence"}
        assert table["direction"]["n_buys"] == 1


# ---------------------------------------------------------------------------
# 账本加载
# ---------------------------------------------------------------------------


class TestLoadLedger:
    def test_bare_and_wrapped_lines(self, tmp_path):
        p = tmp_path / "ledger.jsonl"
        lines = [
            '{"record": {"symbol": "600519.SH", "signal_date": "2026-09-30"}}',
            '{"symbol": "000001.SZ", "signal_date": "2026-09-29"}',
            '',
        ]
        p.write_text("\n".join(lines), encoding="utf-8")
        recs = load_forward_ledger(p)
        assert len(recs) == 2
        assert recs[0]["symbol"] == "600519.SH"
        assert recs[1]["symbol"] == "000001.SZ"
