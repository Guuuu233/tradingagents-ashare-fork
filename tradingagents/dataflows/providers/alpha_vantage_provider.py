from .base import BaseMarketDataProvider
from ..trade_calendar import is_historical_analysis_date
from ..vendor_result import VendorRefuse
from ..alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)


_REPORT_FREQUENCIES = frozenset(("annual", "quarterly", "annually"))


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


class AlphaVantageProvider(BaseMarketDataProvider):
    @property
    def name(self) -> str:
        return "alpha_vantage"

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        return get_alpha_vantage_stock(symbol, start_date, end_date)

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        return get_alpha_vantage_indicator(symbol, indicator, curr_date, look_back_days)

    def get_fundamentals(
        self, ticker: str, curr_date: str = None
    ) -> str | VendorRefuse:
        if is_historical_analysis_date(curr_date):
            return VendorRefuse(
                f"【数据获取失败】Alpha Vantage 基本面概况仅提供当前快照，无法用于历史日期（{curr_date}）分析，本项不可用。"
            )
        return get_alpha_vantage_fundamentals(ticker, curr_date)

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str | VendorRefuse:
        freq, curr_date = _normalize_statement_args(freq, curr_date)
        if is_historical_analysis_date(curr_date):
            return VendorRefuse(
                f"【数据获取失败】Alpha Vantage 资产负债表无法提供历史日期（{curr_date}）时点数据，本项不可用。"
            )
        return get_alpha_vantage_balance_sheet(ticker, freq, curr_date)

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str | VendorRefuse:
        freq, curr_date = _normalize_statement_args(freq, curr_date)
        if is_historical_analysis_date(curr_date):
            return VendorRefuse(
                f"【数据获取失败】Alpha Vantage 现金流量表无法提供历史日期（{curr_date}）时点数据，本项不可用。"
            )
        return get_alpha_vantage_cashflow(ticker, freq, curr_date)

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str | VendorRefuse:
        freq, curr_date = _normalize_statement_args(freq, curr_date)
        if is_historical_analysis_date(curr_date):
            return VendorRefuse(
                f"【数据获取失败】Alpha Vantage 利润表无法提供历史日期（{curr_date}）时点数据，本项不可用。"
            )
        return get_alpha_vantage_income_statement(ticker, freq, curr_date)

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        return get_alpha_vantage_news(ticker, start_date, end_date)

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        return get_alpha_vantage_global_news(curr_date, look_back_days, limit)

    def get_insider_transactions(
        self, symbol: str, curr_date: str = None
    ) -> str | VendorRefuse:
        if is_historical_analysis_date(curr_date):
            return VendorRefuse(
                f"【数据获取失败】Alpha Vantage 内部人交易仅提供最新记录，无法用于历史日期（{curr_date}）分析，本项不可用。"
            )
        return get_alpha_vantage_insider_transactions(symbol, curr_date=curr_date)
