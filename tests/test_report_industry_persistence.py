"""Tests for Track A7: Report industry persistence in write path (without DB schema changes).

Validates:
1. infer_instrument_context correctly includes mapped industry when known, and leaves None when unmapped (no fake '未知行业').
2. ensure_report_industry_persisted correctly populates result_data['instrument_context']['industry'].
3. create_report and update_report_partial persist verified industry into result_data JSON column.
4. extract_report_industry correctly finds industry from persisted ReportDB records.
5. verify_h1b_gates / evaluate_h1b_system_gates on multi-industry fixtures counts unique industries dynamically.
6. scripts/backfill_report_industry.py executes idempotently with dry-run support.
"""

from __future__ import annotations

import copy
import json
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.database import Base, ReportDB
from api.services.report_service import (
    create_report,
    ensure_report_industry_persisted,
    get_report,
    init_report,
    update_report_partial,
)
from tradingagents.agents.utils.agent_states import PROTOCOL_VERSION_V2_STRUCTURED
from tradingagents.agents.utils.context_utils import (
    infer_instrument_context,
    summarize_instrument_context,
)
from tradingagents.agents.utils.shadow_credit import (
    evaluate_h1b_system_gates,
    extract_report_industry,
    filter_v2_completed_reports,
)
from scripts.backfill_report_industry import (
    backfill_report_industry_in_sample,
    load_raw_reports,
    run_industry_backfill,
)


@pytest.fixture
def sqlite_db_session():
    """In-memory SQLite session for report_service tests."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


class TestInferInstrumentContextIndustry:
    """Test infer_instrument_context and summarize_instrument_context industry behavior."""

    def test_infer_instrument_context_maps_known_symbols(self):
        ctx_moutai = infer_instrument_context("600519.SH")
        assert ctx_moutai["industry"] == "白酒与精制茶酒"
        assert ctx_moutai["symbol"] == "600519.SH"
        assert ctx_moutai["market_country"] == "CN"

        ctx_boe = infer_instrument_context("000725.SZ")
        assert ctx_boe["industry"] == "消费电子"

        ctx_catl = infer_instrument_context("300750.SZ")
        assert ctx_catl["industry"] == "新能源车"

        ctx_smic = infer_instrument_context("688981.SH")
        assert ctx_smic["industry"] == "半导体"

        ctx_petro = infer_instrument_context("601857.SH")
        assert ctx_petro["industry"] == "石油化工"

        ctx_cmb = infer_instrument_context("600036.SH")
        assert ctx_cmb["industry"] == "金融地产"

    def test_infer_instrument_context_unknown_symbol_has_no_fake_industry(self):
        ctx_unknown = infer_instrument_context("UNKNOWN_999999")
        assert "industry" not in ctx_unknown or ctx_unknown["industry"] is None
        assert ctx_unknown.get("industry") != "未知行业"

        ctx_none = infer_instrument_context(None)
        assert "industry" not in ctx_none or ctx_none["industry"] is None

        ctx_empty = infer_instrument_context("")
        assert "industry" not in ctx_empty or ctx_empty["industry"] is None

    def test_summarize_instrument_context_includes_industry_when_present(self):
        ctx_with_ind = {
            "symbol": "688981.SH",
            "security_name": "中芯国际",
            "market_country": "CN",
            "exchange": "SH",
            "currency": "CNY",
            "asset_type": "equity",
            "industry": "半导体",
        }
        summary = summarize_instrument_context(ctx_with_ind)
        assert "所属行业：半导体" in summary

        ctx_without_ind = {
            "symbol": "UNKNOWN",
            "security_name": "UNKNOWN",
            "market_country": "CN",
            "exchange": "SH",
            "currency": "CNY",
            "asset_type": "equity",
        }
        summary_no = summarize_instrument_context(ctx_without_ind)
        assert "所属行业" not in summary_no


class TestEnsureReportIndustryPersisted:
    """Test ensure_report_industry_persisted helper."""

    def test_persists_mapped_industry_from_symbol(self):
        result_data = {
            "symbol": "600519.SH",
            "instrument_context": {"symbol": "600519.SH"},
        }
        res = ensure_report_industry_persisted(result_data, symbol="600519.SH")
        assert res["instrument_context"]["industry"] == "白酒与精制茶酒"

    def test_persists_existing_industry_from_market_data_context(self):
        result_data = {
            "symbol": "CUSTOM_TICKER",
            "market_data_context": {
                "industry_linkage": {
                    "industry_name": "生物医药",
                }
            },
        }
        res = ensure_report_industry_persisted(result_data)
        assert res["instrument_context"]["industry"] == "生物医药"

    def test_cleans_legacy_unknown_industry_when_unmapped(self):
        result_data = {
            "symbol": "UNKNOWN_TICKER",
            "instrument_context": {
                "symbol": "UNKNOWN_TICKER",
                "industry": "未知行业",
            },
        }
        res = ensure_report_industry_persisted(result_data, symbol="UNKNOWN_TICKER")
        assert res["instrument_context"]["industry"] is None
        assert res["instrument_context"]["industry"] != "未知行业"

    def test_replaces_legacy_unknown_industry_when_symbol_is_known(self):
        result_data = {
            "symbol": "000725.SZ",
            "instrument_context": {
                "symbol": "000725.SZ",
                "industry": "未知行业",
            },
        }
        res = ensure_report_industry_persisted(result_data, symbol="000725.SZ")
        assert res["instrument_context"]["industry"] == "消费电子"

    def test_handles_dual_horizon_nested_horizons(self):
        result_data = {
            "symbol": "300750.SZ",
            "mode": "dual_horizon",
            "short_term": {
                "symbol": "300750.SZ",
                "instrument_context": {"symbol": "300750.SZ"},
            },
            "medium_term": {
                "symbol": "300750.SZ",
                "instrument_context": {"symbol": "300750.SZ"},
            },
        }
        res = ensure_report_industry_persisted(result_data, symbol="300750.SZ")
        assert res["instrument_context"]["industry"] == "新能源车"
        assert res["short_term"]["instrument_context"]["industry"] == "新能源车"
        assert res["medium_term"]["instrument_context"]["industry"] == "新能源车"


class TestReportServiceIndustryWritePath:
    """Test create_report and update_report_partial persistence in SQLite."""

    def test_create_report_persists_industry_in_result_data(self, sqlite_db_session):
        report_id = f"test-rep-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "688981.SH",
            "market_report": "半导体龙头分析",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bull", "direction": "看多"},
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="688981.SH",
            trade_date="2026-08-20",
            decision="BUY",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )
        assert rep.id == report_id
        assert rep.symbol == "688981.SH"
        assert isinstance(rep.result_data, dict)
        assert rep.result_data.get("instrument_context", {}).get("industry") == "半导体"

        # Verify extract_report_industry directly extracts from ReportDB dict
        rep_dict = rep.to_dict()
        extracted = extract_report_industry(rep_dict)
        assert extracted == "半导体"

    def test_create_report_unknown_symbol_keeps_industry_none(self, sqlite_db_session):
        report_id = f"test-rep-unk-{uuid4().hex[:8]}"
        res_data = {
            "symbol": "999999.SH",
            "market_report": "未知代码分析",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
        }
        rep = create_report(
            db=sqlite_db_session,
            symbol="999999.SH",
            trade_date="2026-08-20",
            decision="HOLD",
            result_data=res_data,
            report_id=report_id,
            status="completed",
        )
        rep_dict = rep.to_dict()
        extracted = extract_report_industry(rep_dict)
        assert extracted is None

    def test_update_report_partial_completed_status_persists_industry(self, sqlite_db_session):
        report_id = f"test-init-{uuid4().hex[:8]}"
        init_report(
            db=sqlite_db_session,
            report_id=report_id,
            symbol="601857.SH",
            trade_date="2026-08-20",
        )

        res_data = {
            "symbol": "601857.SH",
            "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
            "manager_verdict": {"winner": "bear", "direction": "看空"},
        }
        updated = update_report_partial(
            db=sqlite_db_session,
            report_id=report_id,
            status="completed",
            result_data=res_data,
        )
        assert updated is not None
        assert updated.result_data["instrument_context"]["industry"] == "石油化工"
        assert extract_report_industry(updated.to_dict()) == "石油化工"


class TestH1bGateMultiIndustryVerification:
    """Test evaluate_h1b_system_gates accurately counts industries across multi-symbol fixtures."""

    def test_gate_evaluates_unique_industries_dynamically(self, sqlite_db_session):
        # Create reports across 6 distinct industries
        stock_industries = [
            ("600519.SH", "白酒与精制茶酒", "bull"),
            ("688981.SH", "半导体", "bear"),
            ("000725.SZ", "消费电子", "bull"),
            ("300750.SZ", "新能源车", "bear"),
            ("601857.SH", "石油化工", "bull"),
            ("600036.SH", "金融地产", "bear"),
        ]

        reports = []
        for sym, expected_ind, winner in stock_industries:
            rep = create_report(
                db=sqlite_db_session,
                symbol=sym,
                trade_date="2026-08-20",
                decision="BUY" if winner == "bull" else "SELL",
                result_data={
                    "symbol": sym,
                    "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                    "manager_verdict": {"winner": winner, "direction": "看多" if winner == "bull" else "看空"},
                },
                report_id=f"rep-{sym}-{uuid4().hex[:4]}",
                status="completed",
            )
            reports.append(rep.to_dict())

        # Filter v2 completed reports
        v2_reps = filter_v2_completed_reports(reports)
        assert len(v2_reps) == 6

        gate_res = evaluate_h1b_system_gates(v2_reps)
        details = gate_res["matrix"]["dimension_n"]["details"]
        assert details["sample_count"] == 6
        assert details["unique_symbols"] == 6
        assert details["unique_industries"] == 6
        assert details["min_industries"] == 5
        # Industry requirement (>=5) is passed
        assert details["unique_industries"] >= 5


class TestBackfillReportIndustryScript:
    """Test offline backfill_report_industry script logic."""

    def test_backfill_sample_is_idempotent(self):
        sample = {
            "symbol": "600036.SH",
            "result_data": {
                "symbol": "600036.SH",
                "instrument_context": {"symbol": "600036.SH"},
            },
        }
        # First pass: updates industry
        up1, mod1, ind1 = backfill_report_industry_in_sample(sample)
        assert mod1 is True
        assert ind1 == "金融地产"
        assert up1["result_data"]["instrument_context"]["industry"] == "金融地产"

        # Second pass: idempotent, mod2 is False
        up2, mod2, ind2 = backfill_report_industry_in_sample(up1)
        assert mod2 is False
        assert ind2 == "金融地产"
        assert up2 == up1

    def test_run_industry_backfill_dry_run_and_output_file(self, tmp_path):
        input_data = [
            {"symbol": "600519.SH", "status": "completed", "result_data": {"symbol": "600519.SH"}},
            {"symbol": "688981.SH", "status": "completed", "result_data": {"symbol": "688981.SH"}},
            {"symbol": "000725.SZ", "status": "completed", "result_data": {"symbol": "000725.SZ"}},
        ]
        in_file = str(tmp_path / "input_samples.json")
        out_file = str(tmp_path / "output_backfill.json")
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump(input_data, f)

        # 1. Dry run
        dry_stats = run_industry_backfill(
            input_file=in_file,
            dry_run=True,
        )
        assert dry_stats["total_scanned"] == 3
        assert dry_stats["backfilled_count"] == 3
        assert dry_stats["unique_industries_after"] == 3

        # 2. Real output run
        real_stats = run_industry_backfill(
            input_file=in_file,
            output_file=out_file,
            dry_run=False,
            verify_gates=True,
        )
        assert real_stats["total_scanned"] == 3
        assert real_stats["backfilled_count"] == 3
        assert os.path.exists(out_file)

        with open(out_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        assert len(saved["samples"]) == 3
        for s in saved["samples"]:
            assert s["result_data"]["instrument_context"]["industry"] is not None


class TestBackfillReportIndustryDbPath:
    """Test suite for Track A10: backfill_report_industry --db-path behavior."""

    @pytest.fixture
    def custom_sqlite_db(self, tmp_path):
        """Create a temporary SQLite DB populated with known ReportDB records without industry."""
        db_path = str(tmp_path / "custom_industry.db")
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        symbols = [
            ("rep-1", "600519.SH", "completed"),
            ("rep-2", "688981.SH", "completed"),
            ("rep-3", "000725.SZ", "completed"),
            ("rep-4", "300750.SZ", "completed"),
            ("rep-5", "601857.SH", "completed"),
            ("rep-6", "600036.SH", "completed"),
            ("rep-7", "000001.SZ", "failed"),  # non-completed report
        ]

        reports = []
        for rep_id, sym, status in symbols:
            reports.append(
                ReportDB(
                    id=rep_id,
                    symbol=sym,
                    trade_date="2026-08-01",
                    status=status,
                    result_data={
                        "symbol": sym,
                        "protocol_version": PROTOCOL_VERSION_V2_STRUCTURED,
                        "instrument_context": {"symbol": sym},
                    },
                )
            )

        session.add_all(reports)
        session.commit()
        session.close()
        engine.dispose()
        return db_path

    def test_load_raw_reports_with_db_path(self, custom_sqlite_db):
        """load_raw_reports strictly reads completed reports from the custom SQLite DB."""
        reports, db_ctx = load_raw_reports(db_path=custom_sqlite_db)
        assert len(reports) == 6
        symbols = [r["symbol"] for r in reports]
        assert "000001.SZ" not in symbols
        assert set(symbols) == {"600519.SH", "688981.SH", "000725.SZ", "300750.SZ", "601857.SH", "600036.SH"}
        assert db_ctx is not None
        ctx, db, ReportDBCls = db_ctx
        ctx.__exit__(None, None, None)

    def test_run_industry_backfill_active_updates_sqlite_db(self, custom_sqlite_db):
        """Active run_industry_backfill updates instrument_context.industry in SQLite DB."""
        stats = run_industry_backfill(db_path=custom_sqlite_db, dry_run=False)
        assert stats["total_scanned"] == 6
        assert stats["backfilled_count"] == 6
        assert stats["unique_industries_after"] == 6

        # Verify DB directly
        engine = create_engine(f"sqlite:///{custom_sqlite_db}")
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            db_rows = session.query(ReportDB).filter(ReportDB.status == "completed").all()
            assert len(db_rows) == 6
            industry_map = {row.symbol: row.result_data["instrument_context"].get("industry") for row in db_rows}
            assert industry_map["600519.SH"] == "白酒与精制茶酒"
            assert industry_map["688981.SH"] == "半导体"
            assert industry_map["000725.SZ"] == "消费电子"
            assert industry_map["300750.SZ"] == "新能源车"
            assert industry_map["601857.SH"] == "石油化工"
            assert industry_map["600036.SH"] == "金融地产"
        finally:
            session.close()
            engine.dispose()

    def test_run_industry_backfill_dry_run_does_not_modify_sqlite_db(self, custom_sqlite_db):
        """--dry-run mode computes statistics but leaves SQLite DB completely unchanged."""
        stats = run_industry_backfill(db_path=custom_sqlite_db, dry_run=True)
        assert stats["total_scanned"] == 6
        assert stats["backfilled_count"] == 6
        assert stats["dry_run"] is True

        # Verify DB is not modified
        engine = create_engine(f"sqlite:///{custom_sqlite_db}")
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            db_rows = session.query(ReportDB).filter(ReportDB.status == "completed").all()
            assert len(db_rows) == 6
            for row in db_rows:
                assert "industry" not in row.result_data["instrument_context"]
        finally:
            session.close()
            engine.dispose()

    def test_non_existent_db_path_raises_file_not_found(self):
        """Non-existent db_path must raise FileNotFoundError and NEVER fall back to golden samples."""
        fake_path = "/non/existent/path/custom_industry_fake.db"
        with pytest.raises(FileNotFoundError):
            load_raw_reports(db_path=fake_path)

        with pytest.raises(FileNotFoundError):
            run_industry_backfill(db_path=fake_path)

    def test_corrupt_db_path_raises_runtime_error(self, tmp_path):
        """Corrupt non-SQLite file must raise RuntimeError and NEVER fall back to golden samples."""
        corrupt_file = tmp_path / "corrupt.db"
        corrupt_file.write_text("Not a SQLite database")

        with pytest.raises(RuntimeError):
            load_raw_reports(db_path=str(corrupt_file))

        with pytest.raises(RuntimeError):
            run_industry_backfill(db_path=str(corrupt_file))

    def test_empty_sqlite_db_returns_zero_samples_without_golden_fallback(self, tmp_path):
        """Empty SQLite DB returns 0 samples without falling back to golden audit."""
        empty_db = str(tmp_path / "empty.db")
        engine = create_engine(f"sqlite:///{empty_db}")
        Base.metadata.create_all(bind=engine)
        engine.dispose()

        reports, db_ctx = load_raw_reports(db_path=empty_db)
        assert len(reports) == 0
        if db_ctx:
            db_ctx[0].__exit__(None, None, None)

        stats = run_industry_backfill(db_path=empty_db, dry_run=True)
        assert stats["total_scanned"] == 0
        assert stats["backfilled_count"] == 0

    def test_cli_subprocess_execution_with_db_path(self, custom_sqlite_db):
        """CLI invocation with --db-path exits with 0."""
        import subprocess
        import sys

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "backfill_report_industry.py",
        )
        proc = subprocess.run(
            [sys.executable, script_path, "--db-path", custom_sqlite_db, "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        output = json.loads(proc.stdout)
        assert output["total_scanned"] == 6

    def test_cli_subprocess_execution_with_bad_db_path_exits_nonzero(self):
        """CLI invocation with non-existent --db-path exits with non-zero exit code."""
        import subprocess
        import sys

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts",
            "backfill_report_industry.py",
        )
        fake_db = "/non/existent/path/fake.db"
        proc = subprocess.run(
            [sys.executable, script_path, "--db-path", fake_db],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0

