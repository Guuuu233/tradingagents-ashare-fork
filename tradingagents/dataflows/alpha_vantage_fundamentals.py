from .alpha_vantage_common import _make_api_request
from .trade_calendar import is_historical_analysis_date
from .vendor_result import VendorRefuse


_REPORT_FREQUENCIES = frozenset(("annual", "quarterly", "annually"))


def _historical_refusal(label: str, curr_date: str | None) -> VendorRefuse | None:
    if not is_historical_analysis_date(curr_date):
        return None
    return VendorRefuse(
        f"【数据获取失败】Alpha Vantage {label}无法提供可验证的历史日期（{curr_date}）数据，"
        "本项不可用。"
    )


def _normalize_statement_args(
    freq: str | None, curr_date: str | None
) -> tuple[str | None, str | None]:
    normalized_freq = str(freq).strip().lower() if freq is not None else freq
    if (
        curr_date is None
        and freq is not None
        and normalized_freq not in _REPORT_FREQUENCIES
    ):
        return "quarterly", freq
    return normalized_freq, curr_date


def get_fundamentals(ticker: str, curr_date: str = None) -> str | VendorRefuse:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Analysis date; historical dates are rejected as not point-in-time

    Returns:
        str | VendorRefuse: Company overview data or typed historical refusal
    """
    refusal = _historical_refusal("基本面概况", curr_date)
    if refusal is not None:
        return refusal
    params = {
        "symbol": ticker,
    }

    return _make_api_request("OVERVIEW", params)


def get_balance_sheet(
    ticker: str, freq: str = "quarterly", curr_date: str = None
) -> str | VendorRefuse:
    """
    Retrieve balance sheet data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly) - not used for Alpha Vantage
        curr_date (str): Analysis date; historical dates are rejected as not point-in-time

    Returns:
        str | VendorRefuse: Balance sheet data or typed historical refusal
    """
    freq, curr_date = _normalize_statement_args(freq, curr_date)
    refusal = _historical_refusal("资产负债表", curr_date)
    if refusal is not None:
        return refusal
    params = {
        "symbol": ticker,
    }

    return _make_api_request("BALANCE_SHEET", params)


def get_cashflow(
    ticker: str, freq: str = "quarterly", curr_date: str = None
) -> str | VendorRefuse:
    """
    Retrieve cash flow statement data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly) - not used for Alpha Vantage
        curr_date (str): Analysis date; historical dates are rejected as not point-in-time

    Returns:
        str | VendorRefuse: Cash flow data or typed historical refusal
    """
    freq, curr_date = _normalize_statement_args(freq, curr_date)
    refusal = _historical_refusal("现金流量表", curr_date)
    if refusal is not None:
        return refusal
    params = {
        "symbol": ticker,
    }

    return _make_api_request("CASH_FLOW", params)


def get_income_statement(
    ticker: str, freq: str = "quarterly", curr_date: str = None
) -> str | VendorRefuse:
    """
    Retrieve income statement data for a given ticker symbol using Alpha Vantage.

    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly) - not used for Alpha Vantage
        curr_date (str): Analysis date; historical dates are rejected as not point-in-time

    Returns:
        str | VendorRefuse: Income statement data or typed historical refusal
    """
    freq, curr_date = _normalize_statement_args(freq, curr_date)
    refusal = _historical_refusal("利润表", curr_date)
    if refusal is not None:
        return refusal
    params = {
        "symbol": ticker,
    }

    return _make_api_request("INCOME_STATEMENT", params)

