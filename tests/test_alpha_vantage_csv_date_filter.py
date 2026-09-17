"""DAV-943: Alpha Vantage CSV date-range filter must stay fail-closed.

`_filter_csv_by_date_range` must never hand the caller rows outside
[start_date, end_date] or silently re-emit the raw response when parsing
fails. Rows with unparseable dates are dropped explicitly; malformed
inputs yield an explicitly unavailable (empty / header-only) result.
Pure offline tests -- no network, model, or database access.
"""

import io

import pandas as pd
import pytest

from tradingagents.dataflows.alpha_vantage_common import _filter_csv_by_date_range


GOOD_CSV = (
    "timestamp,open,high,low,close,adjusted_close,volume,dividend_amount,split_coefficient\n"
    "2024-01-02,100.0,101.0,99.0,100.5,100.5,1000,0.0,1.0\n"
    "2024-01-03,0.0,0.0,0.0,0.0,0.0,0,0.0,1.0\n"  # legitimate zero values
    "2024-01-04,102.0,103.0,101.0,102.5,102.5,1200,0.0,1.0\n"
    "2024-02-01,110.0,111.0,109.0,110.5,110.5,1500,0.0,1.0\n"  # out of range
)

START, END = "2024-01-01", "2024-01-31"


def _parse(csv_text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(csv_text))


def test_normal_csv_returns_only_in_range_rows():
    out = _filter_csv_by_date_range(GOOD_CSV, START, END)
    df = _parse(out)
    assert list(df["timestamp"]) == ["2024-01-02", "2024-01-03", "2024-01-04"]
    # legitimate zero row is not dropped
    zero_row = df[df["timestamp"] == "2024-01-03"].iloc[0]
    assert zero_row["open"] == 0.0 and zero_row["volume"] == 0


def test_unparseable_date_row_is_dropped_not_returned_raw():
    bad_csv = GOOD_CSV.replace(
        "2024-02-01,", "not-a-date,"
    )
    out = _filter_csv_by_date_range(bad_csv, START, END)
    # must NOT be the raw original CSV
    assert out != bad_csv
    df = _parse(out)
    # out-of-range row must not leak back through the unparseable row
    assert "2024-02-01" not in df["timestamp"].astype(str).tolist()
    assert "not-a-date" not in df["timestamp"].astype(str).tolist()
    assert len(df) == 3


def test_invalid_start_or_end_yields_unavailable_not_raw():
    for s, e in [("bogus", END), (START, "bogus"), ("", "")]:
        out = _filter_csv_by_date_range(GOOD_CSV, s, e)
        assert out != GOOD_CSV  # never the raw CSV
        # explicitly unavailable: empty string or header-only CSV
        if out.strip():
            assert len(_parse(out)) == 0


def test_error_response_not_returned_as_data():
    json_error = '{"Error Message": "the parameter apikey is invalid"}'
    out = _filter_csv_by_date_range(json_error, START, END)
    assert "Error Message" not in out


def test_missing_date_column_does_not_fabricate_dates():
    csv_no_dates = "a,b\n1,2\n3,4\n"
    out = _filter_csv_by_date_range(csv_no_dates, START, END)
    assert out != csv_no_dates
    if out.strip():
        df = _parse(out)
        assert len(df) == 0
        # no synthetic date column created
        assert list(df.columns) == ["a", "b"]


def test_empty_input_passthrough():
    assert _filter_csv_by_date_range("", START, END) == ""
    assert _filter_csv_by_date_range("   ", START, END).strip() == ""
