"""Dialect-aware report schema DDL helpers (D-009 P0-1 review)."""

from __future__ import annotations

import api.database as database


def test_column_ddl_boolean_differs_by_dialect():
    src = open(database.__file__, encoding="utf-8").read()
    assert "BOOLEAN DEFAULT FALSE" in src
    # ordered_columns lists critical status fields before not_applicable
    start = src.find("ordered_columns = [")
    assert start > 0
    chunk = src[start : start + 500]
    assert chunk.find('"analysis_status"') < chunk.find('"not_applicable"')
    assert chunk.find('"trade_action"') < chunk.find('"not_applicable"')
    assert chunk.find('"risk_status"') < chunk.find('"not_applicable"')

def test_ensure_report_schema_on_sqlite_memory_adds_critical_columns(tmp_path):
    from sqlalchemy import create_engine, text, inspect

    db_path = tmp_path / "schema.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE reports ("
                "id VARCHAR(36) PRIMARY KEY, "
                "symbol VARCHAR(20), "
                "trade_date VARCHAR(10)"
                ")"
            )
        )

    old_engine = database.engine
    database.engine = engine
    try:
        database._ensure_report_schema()
        cols = {c["name"] for c in inspect(engine).get_columns("reports")}
        assert {"analysis_status", "trade_action", "risk_status", "industry"}.issubset(cols)
        assert "not_applicable" in cols

        # Check index on industry is created
        indexes = {idx["name"] for idx in inspect(engine).get_indexes("reports") if idx.get("name")}
        assert "ix_reports_industry" in indexes

        # Re-running migration must be idempotent and repeatable without errors
        database._ensure_report_schema()
        cols_after = {c["name"] for c in inspect(engine).get_columns("reports")}
        assert cols == cols_after
    finally:
        database.engine = old_engine


def test_ensure_report_schema_custom_engine_and_idempotency(tmp_path):
    from sqlalchemy import create_engine, text, inspect

    db_path = tmp_path / "custom_schema.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE reports ("
                "id VARCHAR(36) PRIMARY KEY, "
                "symbol VARCHAR(20), "
                "trade_date VARCHAR(10)"
                ")"
            )
        )

    # Pass engine explicitly as target_engine
    database._ensure_report_schema(target_engine=engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("reports")}
    assert "industry" in cols
    indexes = {idx["name"] for idx in insp.get_indexes("reports") if idx.get("name")}
    assert "ix_reports_industry" in indexes

    # Second pass
    database._ensure_report_schema(target_engine=engine)
    assert "industry" in {c["name"] for c in inspect(engine).get_columns("reports")}


def test_ensure_report_schema_raises_on_inspection_failure(tmp_path):
    import pytest
    from sqlalchemy import create_engine

    # Disposed/invalid engine that raises on connection/inspection
    engine = create_engine("sqlite:////dev/null/cannot_create_db.sqlite")
    with pytest.raises(RuntimeError) as exc_info:
        database._ensure_report_schema(target_engine=engine)
    assert "reports schema inspection failed" in str(exc_info.value)

