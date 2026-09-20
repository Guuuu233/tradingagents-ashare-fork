"""DAV-1107: H1b 历史回放入场价契约 —— T+1 Open 统一评价基准与 cohort 隔离。

契约要点：
1. T+5/backfill 评价入口统一为 T+1 Open 真实成交价；
2. manager_verdict.entry / report entry / T 日 close 禁止作为 H1b 评价基准，
   仅作遗留记账口径；
3. 按 T+1 Open 评价的样本标记 price_basis.t1_open_v1 cohort；拿不到 T+1
   Open 的样本标记 price_basis.unspecified，由既有 cohort 同质性机制隔离。
"""

from __future__ import annotations

import pytest

from tradingagents.agents.utils import shadow_credit
from tradingagents.agents.utils.shadow_credit import (
    ENTRY_PRICE_SOURCE_LEGACY,
    ENTRY_PRICE_SOURCE_T1_OPEN,
    PRICE_BASIS_T1_OPEN_V1,
    PRICE_BASIS_UNSPECIFIED,
    backfill_tplus5_shadow_for_report,
    calculate_shadow_credit_metrics,
    extract_sample_cohort,
    is_cohort_homogeneous,
)

from tests.test_tplus5_shadow_backfill import _build_v2_report_fixture


# 2026-08-03 (Mon) → T+1 = 2026-08-04, T+5 = 2026-08-10
TRADE_DATE = "2026-08-03"
T1_DATE = "2026-08-04"
T5_DATE = "2026-08-10"
AS_OF = "2026-08-15"


class TestT1OpenEntryBasis:
    """T+1 Open 作为唯一 H1b 评价基准。"""

    def test_t1_open_used_as_entry_and_stamped(self):
        """open_price_series 提供 T+1 Open 时，评价基准取 T+1 Open 而非 manager entry。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        # manager entry=10.0 → 若按遗留口径 t5=10.5 为 hit；
        # T+1 Open=11.0 → 按契约口径应为 miss，可区分基准来源。
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of=AS_OF,
            price_series={T5_DATE: 10.5},
            open_price_series={T1_DATE: 11.0},
        )

        assert res["_backfill_status"] == "miss"
        assert res["t_plus_5_direction_hit"] is False
        assert res["entry_date"] == T1_DATE
        assert res["entry_price"] == 11.0
        assert res["t_plus_1_open"] == 11.0
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_T1_OPEN
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1
        rd = res["result_data"]
        assert rd["entry_price"] == 11.0
        assert rd["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1

    def test_get_open_price_fn_supplies_t1_open(self):
        """get_open_price_fn 回调同样可作为 T+1 Open 来源。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of=AS_OF,
            price_series={T5_DATE: 12.0},
            get_open_price_fn=lambda sym, d: 11.0 if d == T1_DATE else None,
        )
        assert res["_backfill_status"] == "hit"
        assert res["entry_price"] == 11.0
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1

    def test_legacy_fallback_marked_unspecified(self):
        """拿不到 T+1 Open 时回退遗留口径记账，样本归入 unspecified cohort。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of=AS_OF,
            price_series={T5_DATE: 10.5},
        )
        # 遗留口径仍记账 hit，但 cohort 标记为 unspecified
        assert res["_backfill_status"] == "hit"
        assert res["t_plus_5_direction_hit"] is True
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_LEGACY
        assert res["price_basis_version"] == PRICE_BASIS_UNSPECIFIED

    def test_vendor_bars_supply_t1_open(self, monkeypatch):
        """vendor daily bars 路径：T+1 bar 的 open 作为入场基准。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )

        def fake_bars(symbol, start, end):
            return {
                TRADE_DATE: {"open": 10.2, "close": 10.0},
                T1_DATE: {"open": 11.0, "close": 11.2},
                T5_DATE: {"open": 10.4, "close": 10.5},
            }

        monkeypatch.setattr(shadow_credit, "fetch_daily_bars_safe", fake_bars)
        res = backfill_tplus5_shadow_for_report(report, as_of=AS_OF)

        assert res["_backfill_status"] == "miss"
        assert res["t_plus_5_price"] == 10.5
        assert res["entry_price"] == 11.0
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_T1_OPEN
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1

    def test_existing_t5_price_short_circuit_still_fetches_t1_open(self, monkeypatch):
        """返修回归 (DAV-1116)：报告已带 t_plus_5_price 且未传 open 入参时，
        vendor bar 路径仍须解析出 T+1 Open 并盖章 t1_open_v1。

        存量已回填样本在 T+5 elif 链中被既有 t_plus_5_price 短路；若 T+1 Open
        解析耦合在 else 分支内将永不执行，本用例守护该结构性回归。
        """
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE,
            existing_t5_price=10.5,
        )

        def fake_bars(symbol, start, end):
            return {
                TRADE_DATE: {"open": 10.2, "close": 10.0},
                T1_DATE: {"open": 11.0, "close": 11.2},
            }

        monkeypatch.setattr(shadow_credit, "fetch_daily_bars_safe", fake_bars)
        # 不传 price_series / open_price_series / get_open_price_fn ——
        # 模拟生产 run_backfill 对存量样本的真实调用形态
        res = backfill_tplus5_shadow_for_report(report, as_of=AS_OF)

        assert res["t_plus_5_price"] == 10.5
        assert res["entry_price"] == 11.0
        assert res["entry_date"] == T1_DATE
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_T1_OPEN
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1
        assert res["t_plus_5_direction_hit"] is False  # 11.0 -> 10.5 bull miss

    def test_price_series_short_circuit_still_fetches_t1_open(self, monkeypatch):
        """price_series 命中 T+5 短路时，vendor T+1 Open 解析同样不被跳过。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        monkeypatch.setattr(
            shadow_credit, "fetch_daily_bars_safe",
            lambda s, a, b: {T1_DATE: {"open": 11.0, "close": 11.2}},
        )
        res = backfill_tplus5_shadow_for_report(
            report, as_of=AS_OF, price_series={T5_DATE: 10.5}
        )
        assert res["entry_price"] == 11.0
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1

    def test_legacy_entry_date_is_none(self):
        """legacy 回退口径下 entry_date 置空，避免 T+1 日期 + T 日信号价错配。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res = backfill_tplus5_shadow_for_report(
            report, as_of=AS_OF, price_series={T5_DATE: 10.5}
        )
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_LEGACY
        assert res["entry_date"] is None

    def test_idempotent_rerun_reuses_stamped_t1_open(self):
        """重跑幂等：已盖章 t_plus_1_open 在无 open 输入时仍被复用。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res1 = backfill_tplus5_shadow_for_report(
            report,
            as_of=AS_OF,
            price_series={T5_DATE: 10.5},
            open_price_series={T1_DATE: 11.0},
        )
        res2 = backfill_tplus5_shadow_for_report(
            res1,
            as_of=AS_OF,
            price_series={T5_DATE: 10.5},
        )
        assert res2["entry_price"] == 11.0
        assert res2["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1
        assert res2["t_plus_5_direction_hit"] is False

    def test_pending_due_still_stamps_basis(self):
        """未到期样本也完成口径盖章（用于先导批隔离标记）。"""
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of="2026-08-05",
            open_price_series={T1_DATE: 11.0},
        )
        assert res["t_plus_5_status"] == "pending_due"
        assert res["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1
        assert res["entry_price_source"] == ENTRY_PRICE_SOURCE_T1_OPEN


class TestMetricsEntryPreference:
    """calculate_shadow_credit_metrics 优先采用已盖章 T+1 Open。"""

    def test_t_plus_1_open_preferred_over_manager_entry(self):
        data = {
            "t_plus_1_open": 11.0,
            "manager_verdict": {"winner": "bull", "entry": "10.00元"},
        }
        sm = calculate_shadow_credit_metrics(data, t_plus_5_price=10.5)
        # 以 11.0 为基准 → -0.5 → bull miss
        assert sm["t_plus_5_direction_hit"] is False

    def test_manager_entry_fallback_when_no_t1_open(self):
        data = {
            "manager_verdict": {"winner": "bull", "entry": "10.00元"},
        }
        sm = calculate_shadow_credit_metrics(data, t_plus_5_price=10.5)
        assert sm["t_plus_5_direction_hit"] is True


class TestCohortSegregation:
    """不同口径样本在 cohort 同质性检查下自动隔离。"""

    def test_extract_cohort_reads_stamped_basis(self):
        report = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        res = backfill_tplus5_shadow_for_report(
            report,
            as_of=AS_OF,
            price_series={T5_DATE: 10.5},
            open_price_series={T1_DATE: 11.0},
        )
        cohort = extract_sample_cohort(res)
        assert cohort["price_basis_version"] == PRICE_BASIS_T1_OPEN_V1

    def test_mixed_basis_cohorts_not_homogeneous(self):
        r1 = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        stamped = backfill_tplus5_shadow_for_report(
            r1, as_of=AS_OF,
            price_series={T5_DATE: 10.5},
            open_price_series={T1_DATE: 11.0},
        )
        stamped["decision_model_version"] = "decision_model.v1"
        stamped["evidence_contract_version"] = "evidence_contract.v0"

        r2 = _build_v2_report_fixture(
            winner="bull", entry_price=10.0, trade_date=TRADE_DATE
        )
        legacy = backfill_tplus5_shadow_for_report(
            r2, as_of=AS_OF, price_series={T5_DATE: 10.5}
        )
        legacy["decision_model_version"] = "decision_model.v1"
        legacy["evidence_contract_version"] = "evidence_contract.v0"

        is_homo, key = is_cohort_homogeneous([stamped, legacy])
        assert is_homo is False
