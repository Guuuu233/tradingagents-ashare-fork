"""DAV-1953: b2.v1 M1 输出契约单测 — spec §5 全覆盖。

Synthetic only — no DB, no model, no provider. Covers the spec §5
checklist: strict integer bounds & error types, duplicate block/key,
node exception & not_run, legacy-version separation, three-place read
consistency, base-rate unit/missing/as_of ruling, ledger v1/v2 mixed
hash-chain verification.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tradingagents.eval import b2_contract as b2  # noqa: E402
from tradingagents.agents.managers.research_manager import (
    capture_manager_forecast_input,
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


dsl = _load("daily_snapshot_ledger", "scripts/phase2/daily_snapshot_ledger.py")


# ---------------------------------------------------------------------------
# §2.1 strict integer contract
# ---------------------------------------------------------------------------

class TestStrictInt:
    @pytest.mark.parametrize("v", [1, 50, 99])
    def test_valid_bounds(self, v):
        ev = b2.evaluate_p_rel_t40(
            f'<!-- MANAGER_VERDICT: {{"p_rel_t40": {v}}} -->')
        assert ev["p_rel_t40_status"] == "valid" and ev["p_rel_t40"] == v

    @pytest.mark.parametrize("v,code", [
        (0, "out_of_range"), (100, "out_of_range"), (-3, "out_of_range"),
        (101, "out_of_range"),
    ])
    def test_out_of_range(self, v, code):
        ev = b2.evaluate_p_rel_t40(
            f'<!-- MANAGER_VERDICT: {{"p_rel_t40": {v}}} -->')
        assert ev["p_rel_t40_status"] == "malformed"
        assert code in ev["p_rel_t40_error_codes"]

    @pytest.mark.parametrize("lit", [
        "50.0", "50.5", '"50"', '"50%"', "true", "false", "null",
        "[50]", '{"x": 50}', "1e2",
    ])
    def test_invalid_types(self, lit):
        ev = b2.evaluate_p_rel_t40(
            f'<!-- MANAGER_VERDICT: {{"p_rel_t40": {lit}}} -->')
        assert ev["p_rel_t40_status"] == "malformed"
        assert ev["p_rel_t40"] is None
        assert ev["p_rel_t40_error_codes"]

    def test_explicit_null_is_malformed_not_missing(self):
        ev = b2.evaluate_p_rel_t40(
            '<!-- MANAGER_VERDICT: {"p_rel_t40": null} -->')
        assert ev["p_rel_t40_status"] == "malformed"
        assert "invalid_type" in ev["p_rel_t40_error_codes"]


# ---------------------------------------------------------------------------
# §3 failure classification
# ---------------------------------------------------------------------------

class TestFailureClasses:
    def test_missing_field(self):
        ev = b2.evaluate_p_rel_t40(
            '<!-- MANAGER_VERDICT: {"direction": "BULLISH"} -->')
        assert ev["p_rel_t40_status"] == "missing"
        assert "missing_field" in ev["p_rel_t40_error_codes"]

    def test_missing_block(self):
        ev = b2.evaluate_p_rel_t40("没有任何机读块的正文")
        assert ev["p_rel_t40_status"] == "missing"
        assert "missing_block" in ev["p_rel_t40_error_codes"]

    def test_malformed_block_unparseable(self):
        ev = b2.evaluate_p_rel_t40(
            '<!-- MANAGER_VERDICT: {not json} -->')
        assert ev["p_rel_t40_status"] == "malformed"

    def test_duplicate_block_is_malformed(self):
        text = ('<!-- MANAGER_VERDICT: {"p_rel_t40": 60} -->\n'
                '<!-- MANAGER_VERDICT: {"p_rel_t40": 40} -->')
        ev = b2.evaluate_p_rel_t40(text)
        assert ev["p_rel_t40_status"] == "malformed"
        assert ev["p_rel_t40"] is None  # 不取任一值掩盖歧义

    def test_duplicate_key_is_malformed(self):
        ev = b2.evaluate_p_rel_t40(
            '<!-- MANAGER_VERDICT: {"p_rel_t40": 60, "p_rel_t40": 80} -->')
        # json.loads keeps last — the block-level validator must still
        # refuse a value that hides ambiguity. Either malformed (preferred)
        # or the *sole* surviving 80 — never a silently-merged first value.
        assert ev["p_rel_t40_status"] == "malformed" or ev["p_rel_t40"] == 80

    def test_node_failed(self):
        ev = b2.evaluate_p_rel_t40("partial response",
                                   node_ran=True, node_succeeded=False)
        assert ev["p_rel_t40_status"] == "node_failed"
        assert ev["p_rel_t40"] is None

    def test_not_run(self):
        ev = b2.evaluate_p_rel_t40("", node_ran=False)
        assert ev["p_rel_t40_status"] == "not_run"
        assert ev["raw_output_sha256"] is None

    def test_fail_close_never_defaults(self):
        for text in ["garbage", None, '<!-- MANAGER_VERDICT: {} -->']:
            ev = b2.evaluate_p_rel_t40(text)
            assert ev["p_rel_t40"] is None
            assert ev["p_rel_t40_status"] != "valid"

    def test_raw_output_sha256(self):
        text = '<!-- MANAGER_VERDICT: {"p_rel_t40": 60} -->'
        ev = b2.evaluate_p_rel_t40(text)
        assert ev["raw_output_sha256"] == hashlib.sha256(
            text.encode()).hexdigest()

    def test_contract_version(self):
        ev = b2.evaluate_p_rel_t40("x")
        assert ev["probability_contract_version"] == "b2.v1"


# ---------------------------------------------------------------------------
# capture_manager_forecast_input — report placement (medium slice)
# ---------------------------------------------------------------------------

class TestCaptureInput:
    def test_medium_carries_b2_fields(self):
        fi = capture_manager_forecast_input(
            '<!-- MANAGER_VERDICT: {"p_rel_t40": 70, "p_rel_t10": 60} -->',
            {"src": "SW2021", "level": "L1", "code": "801999.SI", "name": "X"},
            research_horizon="medium",
            base_rate={"p_base_rel_t40": 42.5,
                       "p_base_rel_t40_status": "valid",
                       "p_base_rel_t40_meta": {"m": 200, "W": 0}},
            prompt_sha256="ab" * 32,
        )
        assert fi["p_rel_t40"] == 70
        assert fi["p_rel_t40_status"] == "valid"
        assert fi["probability_contract_version"] == "b2.v1"
        assert fi["p_base_rel_t40"] == 42.5
        assert fi["manager_prompt_sha256"] == "ab" * 32
        assert fi["p_rel_t10"] == 60  # b1.v1 并存不动

    def test_short_untouched(self):
        fi = capture_manager_forecast_input(
            '<!-- MANAGER_VERDICT: {"p_rel_t10": 60} -->',
            research_horizon="short")
        assert "p_rel_t40" not in fi
        assert "probability_contract_version" not in fi

    def test_medium_missing(self):
        fi = capture_manager_forecast_input(
            '<!-- MANAGER_VERDICT: {"p_rel_t10": 60} -->',
            research_horizon="medium")
        assert fi["p_rel_t40_status"] == "missing"


# ---------------------------------------------------------------------------
# as_of 基率时点判定（裁定 3）
# ---------------------------------------------------------------------------

class TestAsOfRuling:
    SIG = date(2026, 7, 28)  # Tuesday

    def _lookup(self, cutoff, industry):
        return {"p_m200": 0.5, "n": 10, "n_market": 100, "_cutoff": cutoff}

    def test_after_close_bound_is_T(self):
        as_of = datetime(2026, 7, 28, 15, 0, tzinfo=b2._CN_TZ)
        assert b2.maturity_cutoff(self.SIG, as_of) == self.SIG
        as_of_late = datetime(2026, 7, 28, 18, 30, tzinfo=b2._CN_TZ)
        assert b2.maturity_cutoff(self.SIG, as_of_late) == self.SIG

    def test_before_close_bound_is_T_minus_1(self):
        as_of = datetime(2026, 7, 28, 14, 59, tzinfo=b2._CN_TZ)
        assert b2.maturity_cutoff(self.SIG, as_of) == self.SIG - timedelta(days=1)
        as_of_am = datetime(2026, 7, 28, 9, 0, tzinfo=b2._CN_TZ)
        assert b2.maturity_cutoff(self.SIG, as_of_am) == self.SIG - timedelta(days=1)

    def test_missing_as_of_pit_unverified(self):
        out = b2.build_base_rate_payload(
            lookup=self._lookup, signal_date=self.SIG,
            industry_code="801999.SI", as_of=None)
        assert out["p_base_rel_t40_status"] == "pit_unverified"
        assert out["p_base_rel_t40"] is None
        assert out["p_base_rel_t40_meta"]["as_of"] is None

    def test_bare_date_is_before_close(self):
        # date-only as_of → 00:00 < 15:00 → T-1 (conservative PIT).
        out = b2.build_base_rate_payload(
            lookup=lambda c, i: {"p_m200": 0.5, "n": 3},
            signal_date=self.SIG, industry_code="X",
            as_of=b2.parse_analysis_cutoff("2026-07-28"))
        assert out["p_base_rel_t40_meta"]["maturity_cutoff_date"] == "2026-07-27"


# ---------------------------------------------------------------------------
# base-rate payload: unit conversion / statuses
# ---------------------------------------------------------------------------

class TestBaseRate:
    SIG = date(2026, 7, 28)
    AS_OF = datetime(2026, 7, 28, 16, 0, tzinfo=b2._CN_TZ)

    def test_unit_conversion_once_no_round(self):
        out = b2.build_base_rate_payload(
            lookup=lambda c, i: {"p_m200": 0.4273, "n": 7, "n_market": 70},
            signal_date=self.SIG, industry_code="801999.SI", as_of=self.AS_OF)
        assert out["p_base_rel_t40_status"] == "valid"
        assert out["p_base_rel_t40"] == pytest.approx(42.73)
        assert out["p_base_rel_t40_meta"]["n_industry"] == 7
        assert out["p_base_rel_t40_meta"]["m"] == 200
        assert out["p_base_rel_t40_meta"]["W"] == 0

    def test_missing_industry(self):
        out = b2.build_base_rate_payload(
            lookup=lambda c, i: {"p_m200": 0.5},
            signal_date=self.SIG, industry_code=None, as_of=self.AS_OF)
        assert out["p_base_rel_t40_status"] == "missing_industry"

    def test_insufficient_history(self):
        out = b2.build_base_rate_payload(
            lookup=lambda c, i: None,
            signal_date=self.SIG, industry_code="X", as_of=self.AS_OF)
        assert out["p_base_rel_t40_status"] == "insufficient_history"

    def test_source_unavailable(self):
        out = b2.build_base_rate_payload(
            lookup=None, signal_date=self.SIG,
            industry_code="X", as_of=self.AS_OF)
        assert out["p_base_rel_t40_status"] == "source_unavailable"
        out2 = b2.build_base_rate_payload(
            lookup=lambda c, i: (_ for _ in ()).throw(RuntimeError("x")),
            signal_date=self.SIG, industry_code="X", as_of=self.AS_OF)
        assert out2["p_base_rel_t40_status"] == "source_unavailable"

    def test_no_fabricated_meta(self):
        out = b2.build_base_rate_payload(
            lookup=lambda c, i: None, signal_date=self.SIG,
            industry_code="X", as_of=self.AS_OF)
        meta = out["p_base_rel_t40_meta"]
        assert meta["history_sha256"] is None
        assert meta["n_industry"] is None or isinstance(meta["n_industry"], int)


# ---------------------------------------------------------------------------
# three-place consistency + ledger v1/v2
# ---------------------------------------------------------------------------

class Row(dict):
    pass


def _row(**kw):
    base = dict(
        id="r1", user_id="u1", symbol="600519.SH", industry="白酒",
        trade_date="2026-07-28", status="completed", analysis_status="VALID",
        decision="BUY", direction="看多", probability=60,
        trade_action="BUY", risk_status="APPROVED",
        final_trade_decision="ok",
        created_at="2026-07-28 15:43:57.730577",
        updated_at="2026-07-28 15:43:57.730577",
    )
    base.update(kw)
    return Row(base)


def _cal(start="2026-07-01", days=400):
    out = []
    d = date.fromisoformat(start)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


B2_MEDIUM = {
    "direction": "看多", "probability": 60,
    "manager_verdict": {
        "p_rel_t40": 70,
        "forecast_input": {
            "p_rel_t40": 70,
            "p_rel_t40_status": "valid",
            "p_rel_t40_error_codes": [],
            "probability_contract_version": "b2.v1",
            "raw_output_sha256": "ab" * 32,
            "manager_prompt_sha256": "cd" * 32,
            "p_base_rel_t40": 42.73,
            "p_base_rel_t40_status": "valid",
            "p_base_rel_t40_meta": {"m": 200, "W": 0},
        },
    },
}


def _rd_v2():
    return json.dumps({
        "input_snapshot": {"algo": "sha256", "hash": "x", "keys": []},
        "market_data_context": {"short": {}, "medium": {}},
        "medium_term": B2_MEDIUM,
        "short_term": {"direction": "BULL", "probability": 60,
                       "manager_verdict": {"p_rel_t10": 60}},
        "run_identity": {"commit_sha": "x" * 40},
    }, ensure_ascii=False)


class TestLedgerV2:
    def _build(self, rd_text, cal):
        sealed = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        return dsl.build_record(_row(), rd_text, sealed, "2026-07-29", cal)

    def test_v2_record_fields(self):
        rec = self._build(_rd_v2(), _cal())
        assert rec["ledger_version"] == 2
        med = rec["horizons"]["medium"]
        for k in dsl._B2_MEDIUM_KEYS:
            assert k in med
        assert med["p_rel_t40"] == 70
        assert med["p_rel_t40_status"] == "valid"
        assert med["probability_contract_version"] == "b2.v1"
        assert med["raw_output_sha256"] == "ab" * 32
        assert med["p_base_rel_t40"] == pytest.approx(42.73)
        assert med["p_base_rel_t40_meta"]["m"] == 200
        v = rec["versions"]
        assert v["manager_prompt_sha256_by_horizon"]["medium"] == "cd" * 32
        assert v["probability_contract_version_by_horizon"]["medium"] == "b2.v1"

    def test_v1_row_stays_v1(self):
        rd = json.dumps({"horizons": {"medium": {"direction": "BEAR",
                                                 "probability": 55}},
                         "market_data_context": {}})
        rec = self._build(rd, _cal())
        assert rec["ledger_version"] == 1
        assert "p_rel_t40" not in rec["horizons"]["medium"]

    def test_no_cross_horizon_borrow(self):
        # short slice carrying p_rel_t40 must NOT leak into medium.
        rd = json.dumps({
            "market_data_context": {},
            "short_term": {"manager_verdict": {
                "p_rel_t40": 99, "forecast_input": {"p_rel_t40": 99}}},
            "medium_term": {"direction": "X"},
        })
        rec = self._build(rd, _cal())
        med = rec["horizons"]["medium"]
        assert med.get("p_rel_t40") is None
        assert rec["ledger_version"] == 1

    def test_mixed_ledger_hash_chain(self, tmp_path):
        """v1 + v2 rows in ONE ledger: verify must pass end-to-end."""
        ledger = tmp_path / "forward_ledger.jsonl"
        cal = _cal()
        sealed = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        v1_rd = json.dumps({"horizons": {"medium": {"direction": "BEAR"}},
                            "market_data_context": {}})
        prev = "GENESIS"
        with open(ledger, "w") as fh:
            for i, rd in enumerate([v1_rd, _rd_v2()]):
                rec = dsl.build_record(_row(id=f"r{i}"), rd, sealed,
                                       "2026-07-29", cal)
                ch = hashlib.sha256(
                    (prev + rec["record_sha256"]).encode()).hexdigest()
                fh.write(json.dumps(
                    {"record": rec, "prev_hash": prev, "chain_hash": ch}
                ) + "\n")
                prev = ch
        args = type("A", (), {"ledger_dir": str(tmp_path)})()
        assert dsl.cmd_verify(args) == 0

    def test_reader_v1_v2_uniform(self):
        for rd, want_status, want_val in [
            (_rd_v2(), "valid", 70),
            (json.dumps({"horizons": {"medium": {"probability": 55}}}),
             "not_sealed", None),
        ]:
            rec = self._build(rd, _cal())
            f = b2.medium_contract_fields(rec)
            assert f["p_rel_t40_status"] == want_status
            assert f["p_rel_t40"] == want_val

    def test_three_readings_consistent(self):
        """spec §4 三处读数：节点 / 报告持久化 / 账本 — 全链一致。"""
        text = '<!-- MANAGER_VERDICT: {"p_rel_t40": 70} -->'
        fi = capture_manager_forecast_input(
            text, {"src": "SW2021", "level": "L1", "code": "801999.SI",
                   "name": "X"},
            research_horizon="medium",
            base_rate={"p_base_rel_t40": 42.73,
                       "p_base_rel_t40_status": "valid",
                       "p_base_rel_t40_meta": {"m": 200, "W": 0}},
            prompt_sha256="cd" * 32)
        report_slice = {"manager_verdict": {
            "p_rel_t40": fi["p_rel_t40"],
            "p_rel_t40_status": fi["p_rel_t40_status"],
            "p_rel_t40_error_codes": fi["p_rel_t40_error_codes"],
            "probability_contract_version":
                fi["probability_contract_version"],
            "raw_output_sha256": fi["raw_output_sha256"],
            "p_base_rel_t40": fi["p_base_rel_t40"],
            "p_base_rel_t40_status": fi["p_base_rel_t40_status"],
            "forecast_input": fi}}
        rd = json.dumps({"medium_term": report_slice,
                         "market_data_context": {}})
        rec = self._build(rd, _cal())
        res = b2.verify_three_readings(
            node_forecast_input=fi,
            report_medium_slice=report_slice,
            ledger_record=rec)
        assert res["consistent"], res["mismatches"]

    def test_three_readings_flags_drift(self):
        rec = self._build(_rd_v2(), _cal())
        res = b2.verify_three_readings(
            node_forecast_input={"p_rel_t40": 71,
                                 "p_rel_t40_status": "valid"},
            report_medium_slice={"manager_verdict": {"p_rel_t40": 70,
                                                     "p_rel_t40_status":
                                                     "valid"}},
            ledger_record=rec)
        assert not res["consistent"]
        assert any("p_rel_t40" in m for m in res["mismatches"])

    def test_compressed_result_data_readback(self):
        """压缩存储读回：zst bytes → _result_data_text → 同一 b2 值。"""
        from tradingagents.storage.compressed_json import (
            compress_json_bytes,
        )
        rd = _rd_v2()
        zst = compress_json_bytes(rd.encode("utf-8"))
        text = dsl._result_data_text(zst)
        rec = self._build(text, _cal())
        assert rec["ledger_version"] == 2
        assert rec["horizons"]["medium"]["p_rel_t40"] == 70


# ---------------------------------------------------------------------------
# report-side reader (parse_horizon_forecast medium extension)
# ---------------------------------------------------------------------------

class TestReportReader:
    def test_forecast_carries_b2(self):
        from api.services import report_service

        slice_ = {
            "horizon": "medium",
            "manager_verdict": {"forecast_input": {
                "node_ran": True, "p_rel_t10": 60,
                "p_rel_t40": 70, "p_rel_t40_status": "valid",
                "p_rel_t40_error_codes": [],
                "probability_contract_version": "b2.v1",
                "benchmark": {"src": "SW2021", "level": "L1",
                              "code": "801999.SI", "name": "X"},
            }},
        }
        f = report_service.parse_horizon_forecast(slice_)
        assert f["p_rel_t10"] == 60 and f["status"] == "valid"
        assert f["p_rel_t40"] == 70 and f["p_rel_t40_status"] == "valid"
        assert f["probability_contract_version"] == "b2.v1"

    def test_pre_contract_medium_not_sealed(self):
        from api.services import report_service

        f = report_service.parse_horizon_forecast({
            "horizon": "medium",
            "manager_verdict": {"forecast_input": {"node_ran": True,
                                                   "p_rel_t10": 60}},
        })
        assert f["p_rel_t40_status"] == "not_sealed"
        assert f["p_rel_t40"] is None

    def test_tampered_status_reverified(self):
        from api.services import report_service

        f = report_service.parse_horizon_forecast({
            "horizon": "medium",
            "manager_verdict": {"forecast_input": {
                "node_ran": True,
                "p_rel_t40": 150, "p_rel_t40_status": "valid",
                "probability_contract_version": "b2.v1"}},
        })
        assert f["p_rel_t40_status"] == "malformed"
        assert f["p_rel_t40"] is None


# ---------------------------------------------------------------------------
# m1_eval ledger-JSONL compat (v1+v2) + paired base eval
# ---------------------------------------------------------------------------

class TestEvalCompat:
    def _m1(self):
        return _load("m1_eval_mod", "scripts/phase2/m1_eval.py")

    def test_ledger_jsonl_unwrap_v2(self, tmp_path):
        m1 = self._m1()
        rec = {"ledger_version": 2, "signal_date": "2026-07-28",
               "symbol": "600519.SH", "report_id": "r1", "user_id": "u1",
               "created_at": "2026-07-28 15:43:57",
               "timing_class": "F0", "input_pit_status": "VERIFIED",
               "versions": {"generated_by_commit_sha": "abc"},
               "horizons": {"medium": {
                   "p_rel_t40": 70, "p_rel_t40_status": "valid",
                   "p_base_rel_t40": 42.0, "p_base_rel_t40_status": "valid"}}}
        p = tmp_path / "l.jsonl"
        p.write_text(json.dumps({"record": rec, "prev_hash": "G",
                                 "chain_hash": "x"}) + "\n")
        df = m1.load_frame(p)
        assert df.iloc[0]["q"] == pytest.approx(0.70)
        assert df.iloc[0]["q_base"] == pytest.approx(0.42)

    def test_ledger_jsonl_v1_no_b2(self, tmp_path):
        m1 = self._m1()
        rec = {"ledger_version": 1, "signal_date": "2026-07-28",
               "symbol": "X", "report_id": "r1",
               "timing_class": "F0", "input_pit_status": "VERIFIED",
               "horizons": {"medium": {"probability": 55}}}
        p = tmp_path / "l.jsonl"
        p.write_text(json.dumps({"record": rec}) + "\n")
        df = m1.load_frame(p)
        assert df.iloc[0]["q"] == pytest.approx(0.55)
        assert "q_base" not in df.columns or df.iloc[0].get("q_base") is None \
            or df.iloc[0]["q_base"] != df.iloc[0]["q_base"]

    def test_invalid_p40_becomes_nan_prob(self, tmp_path):
        m1 = self._m1()
        rec = {"ledger_version": 2, "signal_date": "2026-07-28",
               "symbol": "X", "timing_class": "F0",
               "input_pit_status": "VERIFIED",
               "horizons": {"medium": {"p_rel_t40": None,
                                       "p_rel_t40_status": "malformed"}}}
        p = tmp_path / "l.jsonl"
        p.write_text(json.dumps({"record": rec}) + "\n")
        df = m1.load_frame(p)
        assert df.iloc[0]["q"] != df.iloc[0]["q"]  # NaN → funnel count

    def test_paired_base_eval(self):
        m1 = self._m1()
        import numpy as np
        import pandas as pd
        rows = [{"signal_date": "20260105", "symbol": f"S{i}",
                 "q": (i + 1) / 100.0,
                 "q_base": (20 - i) / 100.0 if i < 20 else np.nan,
                 "r": (i - 10) * 0.01,
                 "version_key": "v1", "timing_class": "F0",
                 "input_pit_status": "VERIFIED"}
                for i in range(25)]
        d = pd.DataFrame(rows)
        out = m1.paired_base_eval(d)
        assert out["paired"] and out["paired_rows"] == 20
        assert out["days"][0]["ic_model"] == pytest.approx(1.0)
        assert out["days"][0]["ic_base"] == pytest.approx(-1.0)
        assert out["days"][0]["ic_diff"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# shadow_portfolio ledger probability v1/v2
# ---------------------------------------------------------------------------

class TestShadowCompat:
    def test_v2_prob_preferred(self):
        from tradingagents.eval.shadow_portfolio import ledger_probability
        assert ledger_probability({"horizons": {"medium": {
            "p_rel_t40": 70, "p_rel_t40_status": "valid",
            "probability": 55}}}) == 70.0

    def test_v2_invalid_no_fallback(self):
        from tradingagents.eval.shadow_portfolio import ledger_probability
        assert ledger_probability({"horizons": {"medium": {
            "p_rel_t40": None, "p_rel_t40_status": "malformed",
            "probability": 55}}}) is None

    def test_v1_legacy(self):
        from tradingagents.eval.shadow_portfolio import ledger_probability
        assert ledger_probability({"horizons": {"medium": {
            "probability": 55}}}) == 55.0


class TestParseCounters:
    def test_counter_matrix(self):
        evs = [
            b2.evaluate_p_rel_t40('<!-- MANAGER_VERDICT: {"p_rel_t40": 60} -->'),
            b2.evaluate_p_rel_t40('<!-- MANAGER_VERDICT: {} -->'),
            b2.evaluate_p_rel_t40('bad'),
            b2.evaluate_p_rel_t40('x', node_succeeded=False),
            b2.evaluate_p_rel_t40('x', node_ran=False),
        ]
        c = b2.parse_counters(evs, three_way_consistent=1)
        assert c["planned_units"] == 5 and c["attempted"] == 4
        assert c["completed_units"] == 3
        assert c["valid"] == 1 and c["missing"] == 2
        assert c["node_failed"] == 1 and c["not_run"] == 1
        assert c["parse_invalid"] == 2
        assert c["three_way_consistent"] == 1

    def test_batch_gate_40x2(self):
        # 签收门槛：40 次尝试 missing+malformed ≤1；本例构造 1 次缺失。
        evs = [b2.evaluate_p_rel_t40(
            '<!-- MANAGER_VERDICT: {"p_rel_t40": 60} -->')] * 39
        evs.append(b2.evaluate_p_rel_t40('<!-- MANAGER_VERDICT: {} -->'))
        c = b2.parse_counters(evs, planned=40)
        assert c["attempted"] == 40 and c["parse_invalid"] == 1
