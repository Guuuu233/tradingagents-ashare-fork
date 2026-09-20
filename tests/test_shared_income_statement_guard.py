"""DAV-1134：财务字段 canonicalization/口径守卫下沉共享 pre-LLM 装配层。

红线：
- 所有 provider 的 income statement 经 route_to_vendor 进入 Fundamentals
  Analyst prompt 前，强制过同一 营业总成本/营业成本 口径守卫；
- 仅有营业总成本、无真实营业成本 → 显式 gap 提示，禁止冒充/反推毛利率；
- 营业总成本 < 营业成本 的行 → 口径告警；
- 模拟新 provider 不经任何 provider 级接线即自动受同一守卫约束；
- provider 已注入的相同提示不重复追加。
"""
import types
import unittest
from unittest.mock import patch

import pandas as pd

import tradingagents.dataflows.interface as interface
import tradingagents.dataflows.providers.tushare_provider as tp
from tradingagents.dataflows.providers.base import DEFAULT_PROVIDER_RESOURCE_POLICY
from tradingagents.dataflows.providers.tushare_provider import TushareProvider
from tradingagents.dataflows.utils import income_statement_text_caliber_notes
from tradingagents.dataflows.vendor_result import VendorOk


TOTAL_ONLY_TABLE = (
    "| 报告期 | 营业总收入 | 营业总成本 |\n"
    "|---|---|---|\n"
    "| 2025-06-30 | 1000.0 | 900.0 |\n"
)

BOTH_CONSISTENT_TABLE = (
    "| 报告期 | 营业总收入 | 营业总成本 | 营业成本 |\n"
    "|---|---|---|---|\n"
    "| 2025-06-30 | 1000.0 | 900.0 | 700.0 |\n"
)

BOTH_INVERTED_TABLE = (
    "| 报告期 | 营业总收入 | 营业总成本 | 营业成本 |\n"
    "|---|---|---|---|\n"
    "| 2025-06-30 | 1000.0 | 650.0 | 700.0 |\n"
)


class TestTextCaliberNotes(unittest.TestCase):
    def test_total_cost_without_cogs_flags_explicit_gap(self):
        notes = income_statement_text_caliber_notes(TOTAL_ONLY_TABLE)
        assert "口径提示" in notes
        assert "营业总成本≠营业成本" in notes
        assert "禁止" in notes

    def test_inverted_rows_flag_caliber_alert(self):
        notes = income_statement_text_caliber_notes(BOTH_INVERTED_TABLE)
        assert "口径告警" in notes

    def test_consistent_table_is_silent(self):
        assert income_statement_text_caliber_notes(BOTH_CONSISTENT_TABLE) == ""

    def test_no_cost_columns_is_silent(self):
        text = "| 报告期 | 净利润 |\n|---|---|\n| 2025-06-30 | 100.0 |\n"
        assert income_statement_text_caliber_notes(text) == ""

    def test_english_headers_also_guarded(self):
        text = (
            "| end_date | total_cogs |\n"
            "|---|---|\n"
            "| 2025-06-30 | 900.0 |\n"
        )
        notes = income_statement_text_caliber_notes(text)
        assert "口径提示" in notes

    def test_all_null_cost_column_counts_as_missing(self):
        text = (
            "| 报告期 | 营业总成本 | 营业成本 |\n"
            "|---|---|---|\n"
            "| 2025-06-30 | 900.0 |  |\n"
        )
        notes = income_statement_text_caliber_notes(text)
        assert "口径提示" in notes

    def test_existing_note_not_duplicated(self):
        first = income_statement_text_caliber_notes(TOTAL_ONLY_TABLE)
        annotated = TOTAL_ONLY_TABLE + "\n" + first
        again = income_statement_text_caliber_notes(annotated)
        assert again == ""


class _FakeRegistry:
    """最小 registry stub：模拟一个未接任何守卫的新 provider。"""

    def __init__(self, providers):
        self._providers = providers

    def get(self, name):
        return self._providers.get(name)

    def list_names(self):
        return list(self._providers)

    def resource_policy(self, name):
        return DEFAULT_PROVIDER_RESOURCE_POLICY


class TestSharedLayerGuard(unittest.TestCase):
    def _route(self, provider_name: str, provider) -> str:
        fake_registry = _FakeRegistry({provider_name: provider})
        with patch.object(interface, "_registry", fake_registry), patch.object(
            interface, "get_vendor", return_value=provider_name
        ):
            return interface.route_to_vendor(
                "get_income_statement", "600036", "quarterly", "2025-09-01"
            )

    def test_new_provider_total_cost_only_gets_gap_note(self):
        """模拟新 provider：返回仅有营业总成本的表，共享层自动补口径提示。"""
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_income_statement=lambda *a, **k: TOTAL_ONLY_TABLE,
        )
        out = self._route("cn_newvendor", provider)
        assert "口径提示" in out
        assert "禁止将营业总成本当作营业成本" in out

    def test_new_provider_inverted_rows_get_alert(self):
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_income_statement=lambda *a, **k: BOTH_INVERTED_TABLE,
        )
        out = self._route("cn_newvendor", provider)
        assert "口径告警" in out

    def test_new_provider_consistent_table_untouched(self):
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_income_statement=lambda *a, **k: BOTH_CONSISTENT_TABLE,
        )
        out = self._route("cn_newvendor", provider)
        assert out == BOTH_CONSISTENT_TABLE

    def test_vendor_ok_payload_also_guarded(self):
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_income_statement=lambda *a, **k: VendorOk(TOTAL_ONLY_TABLE),
        )
        out = self._route("cn_newvendor", provider)
        assert "口径提示" in out

    def test_provider_injected_note_not_duplicated(self):
        note = income_statement_text_caliber_notes(TOTAL_ONLY_TABLE)
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_income_statement=lambda *a, **k: TOTAL_ONLY_TABLE + "\n" + note,
        )
        out = self._route("cn_newvendor", provider)
        assert out.count("营业总成本≠营业成本") == 1

    def test_guard_applies_to_get_fundamentals(self):
        """同一守卫覆盖 fundamental_data 全部工具（含 get_fundamentals）。"""
        provider = types.SimpleNamespace(
            name="cn_newvendor",
            get_fundamentals=lambda *a, **k: TOTAL_ONLY_TABLE,
        )
        fake_registry = _FakeRegistry({"cn_newvendor": provider})
        with patch.object(interface, "_registry", fake_registry), patch.object(
            interface, "get_vendor", return_value="cn_newvendor"
        ):
            out = interface.route_to_vendor("get_fundamentals", "600036", "2025-09-01")
        assert "口径提示" in out


def _income_df(include_cost: bool, total: float = 900.0, cost: float = 700.0):
    row = {
        "ts_code": "600036.SH",
        "ann_date": "20250801",
        "f_ann_date": "20250801",
        "end_date": "20250630",
        "report_type": "1",
        "update_flag": "1",
        "basic_eps": 1.5,
        "total_revenue": 1000.0,
        "revenue": 1000.0,
        "total_cogs": total,
        "operate_profit": 300.0,
        "total_profit": 300.0,
        "n_income": 250.0,
        "n_income_attr_p": 250.0,
    }
    if include_cost:
        row["oper_cost"] = cost
    return pd.DataFrame([row])


class TestTushareCostFieldSeparation(unittest.TestCase):
    """Tushare income 渲染必须明确分列 营业总成本/营业成本 canonical 字段。"""

    def setUp(self):
        self.provider = TushareProvider()
        self.patches = [
            patch.object(tp, "_get_tushare_token", return_value="fake-token"),
            patch.object(tp, "_FINANCIAL_RETRY_BACKOFF_S", 0),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_api_requests_cost_fields(self):
        captured = {}

        def fake(api_name, ts_code=None, fields=None, **kwargs):
            captured["fields"] = fields
            return _income_df(include_cost=True), None, None

        with patch.object(tp, "_query_tushare_api", side_effect=fake):
            self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert "total_cogs" in captured["fields"]
        assert "oper_cost" in captured["fields"]

    def test_render_separates_total_cost_and_cogs(self):
        df = _income_df(include_cost=True)
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)):
            out = self.provider.get_income_statement("600036", curr_date="2025-09-01")
        assert "营业总成本" in out
        assert "营业成本" in out

    def test_routed_tushare_total_only_gets_gap_note(self):
        """端到端：Tushare 仅有营业总成本列时，共享层自动补口径提示。"""
        df = _income_df(include_cost=False)
        registry = _FakeRegistry({"tushare": TushareProvider()})
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)), \
             patch.object(interface, "_registry", registry), patch.object(
                interface, "get_vendor", return_value="tushare"
        ):
            out = interface.route_to_vendor(
                "get_income_statement", "600036", "quarterly", "2025-09-01"
            )
        assert "营业总成本" in out
        assert "口径提示" in out

    def test_routed_tushare_consistent_no_note(self):
        df = _income_df(include_cost=True)
        registry = _FakeRegistry({"tushare": TushareProvider()})
        with patch.object(tp, "_query_tushare_api", return_value=(df, None, None)), \
             patch.object(interface, "_registry", registry), patch.object(
                interface, "get_vendor", return_value="tushare"
        ):
            out = interface.route_to_vendor(
                "get_income_statement", "600036", "quarterly", "2025-09-01"
            )
        assert "营业总成本" in out and "营业成本" in out
        assert "口径提示" not in out
        assert "口径告警" not in out


if __name__ == "__main__":
    unittest.main()
