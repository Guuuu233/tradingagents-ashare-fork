import io
import logging
import os
import re
import socket
import threading
from contextlib import contextmanager
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from stockstats import wrap

from .base import BaseMarketDataProvider
from ..trade_calendar import (
    cn_no_data_reason,
    dedupe_daily_bars,
    drop_incomplete_today_bar,
)

logger = logging.getLogger(__name__)

DEFAULT_BAOSTOCK_SOCKET_TIMEOUT = float(os.getenv("BAOSTOCK_SOCKET_TIMEOUT", "45.0"))
_INSTALL_LOCK = threading.Lock()
_HARDENING_INSTALLED = False
_HARDENING_ERROR: Optional[Exception] = None


def ensure_baostock_socket_hardening(timeout: float = DEFAULT_BAOSTOCK_SOCKET_TIMEOUT) -> None:
    """Harden baostock client socketutil: enforce per-socket timeout, fail-fast connect, and break EOF liveloop.

    Fail-closed invariant:
    If hardening installation fails, subsequent attempts to access baostock are actively
    blocked before bs.login() can be invoked.
    """
    global _HARDENING_INSTALLED, _HARDENING_ERROR

    if _HARDENING_ERROR is not None:
        raise RuntimeError(
            f"baostock hardening installation failed previously (fail-closed): {_HARDENING_ERROR}"
        ) from _HARDENING_ERROR

    if _HARDENING_INSTALLED:
        return

    with _INSTALL_LOCK:
        if _HARDENING_ERROR is not None:
            raise RuntimeError(
                f"baostock hardening installation failed previously (fail-closed): {_HARDENING_ERROR}"
            ) from _HARDENING_ERROR
        if _HARDENING_INSTALLED:
            return

        try:
            import zlib
            import baostock.util.socketutil as bssock

            if not hasattr(bssock, "SocketUtil") or not hasattr(bssock, "send_msg"):
                raise AttributeError("baostock.util.socketutil is missing SocketUtil or send_msg")

            cons = bssock.cons
            context = bssock.context

            def safe_connect(self):
                my_socket = None
                try:
                    my_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    my_socket.settimeout(timeout)
                    my_socket.connect((cons.BAOSTOCK_SERVER_IP, cons.BAOSTOCK_SERVER_PORT))
                    setattr(context, "default_socket", my_socket)
                except Exception as exc:
                    if my_socket is not None:
                        try:
                            my_socket.close()
                        except OSError as close_err:
                            logger.debug("Error closing baostock socket after connect failure: %s", close_err)
                    setattr(context, "default_socket", None)
                    logger.debug("Baostock socket connect failed, cleaned up context.default_socket: %s", exc)
                    raise

            def safe_send_msg(msg: str):
                default_socket = getattr(context, "default_socket", None)
                if default_socket is None:
                    raise ConnectionError("baostock default_socket is None or not connected")
                try:
                    default_socket.settimeout(timeout)
                    msg_bytes = (msg + "\n").encode("utf-8")
                    default_socket.sendall(msg_bytes)
                    receive = b""
                    while True:
                        recv = default_socket.recv(8192)
                        if not recv:
                            try:
                                default_socket.close()
                            except OSError as close_err:
                                logger.debug("Error closing baostock socket on EOF: %s", close_err)
                            setattr(context, "default_socket", None)
                            raise ConnectionResetError("baostock socket connection closed by peer (EOF)")
                        receive += recv
                        if receive.endswith(b"<![CDATA[]]>\n"):
                            break

                    head_bytes = receive[0:cons.MESSAGE_HEADER_LENGTH]
                    head_str = head_bytes.decode("utf-8")
                    head_arr = head_str.split(cons.MESSAGE_SPLIT)
                    if head_arr[1] in cons.COMPRESSED_MESSAGE_TYPE_TUPLE:
                        head_inner_length = int(head_arr[2])
                        body_bytes = receive[
                            cons.MESSAGE_HEADER_LENGTH : cons.MESSAGE_HEADER_LENGTH + head_inner_length
                        ]
                        body_str = zlib.decompress(body_bytes).decode("utf-8")
                        return head_str + body_str
                    else:
                        return receive.decode("utf-8")
                except Exception as exc:
                    try:
                        default_socket.close()
                    except OSError as close_err:
                        logger.debug("Error closing baostock socket on send/recv error: %s", close_err)
                    setattr(context, "default_socket", None)
                    logger.debug("Baostock send_msg error: %s", exc)
                    raise

            bssock.SocketUtil.connect = safe_connect
            bssock.send_msg = safe_send_msg
            _HARDENING_INSTALLED = True
        except Exception as exc:
            _HARDENING_ERROR = exc
            logger.error("Failed to install baostock socket hardening: %s", exc, exc_info=True)
            raise RuntimeError(
                f"baostock hardening installation failed (fail-closed): {exc}"
            ) from exc


def _cleanup_context_socket() -> None:
    try:
        import baostock.common.context as bs_ctx
        sock = getattr(bs_ctx, "default_socket", None)
        if sock is not None:
            try:
                sock.close()
            except OSError as close_err:
                logger.debug("Error closing leftover baostock socket: %s", close_err)
            setattr(bs_ctx, "default_socket", None)
    except Exception as ctx_err:
        logger.debug("Error accessing baostock context for cleanup: %s", ctx_err)


@contextmanager
def baostock_session(timeout: float = DEFAULT_BAOSTOCK_SOCKET_TIMEOUT):
    """Unified production entry point and session context for baostock operations."""
    ensure_baostock_socket_hardening(timeout=timeout)
    try:
        import baostock as bs  # type: ignore
    except ImportError as exc:
        raise NotImplementedError(
            "cn_baostock requires 'baostock'. Install it with: pip install baostock"
        ) from exc

    with redirect_stdout(io.StringIO()):
        lg = bs.login()
    if getattr(lg, "error_code", "1") != "0":
        _cleanup_context_socket()
        err_msg = getattr(lg, "error_msg", "unknown login error")
        raise ConnectionError(f"baostock login failed: {err_msg}")
    try:
        yield bs
    finally:
        with redirect_stdout(io.StringIO()):
            try:
                bs.logout()
            except Exception as logout_err:
                logger.debug("Error during baostock logout: %s", logout_err)
        _cleanup_context_socket()


class CnBaoStockProvider(BaseMarketDataProvider):
    """A-share provider backed by BaoStock."""

    INDICATOR_DESCRIPTIONS = {
        "close_50_sma": "50 日均线（SMA）：中期趋势指标。",
        "close_200_sma": "200 日均线（SMA）：长期趋势基准。",
        "close_10_ema": "10 日指数均线（EMA）：短期响应更快。",
        "macd": "MACD：趋势与动量综合指标。",
        "macds": "MACD 信号线（Signal）。",
        "macdh": "MACD 柱状图（Histogram）。",
        "rsi": "RSI：衡量超买/超卖的动量指标。",
        "boll": "布林中轨（20 日均线）。",
        "boll_ub": "布林上轨。",
        "boll_lb": "布林下轨。",
        "atr": "ATR：真实波动幅度均值，用于波动与风控。",
        "vwma": "VWMA：成交量加权均线。",
        "mfi": "MFI：资金流量指标。",
    }

    @property
    def name(self) -> str:
        return "cn_baostock"

    def _bs(self):
        ensure_baostock_socket_hardening()
        try:
            import baostock as bs  # type: ignore
        except ImportError as exc:
            raise NotImplementedError(
                "cn_baostock requires 'baostock'. Install it with: pip install baostock"
            ) from exc
        return bs

    def _normalize_symbol(self, symbol: str) -> str:
        s = symbol.strip().lower()
        m = re.search(r"(\d{6})", s)
        if not m:
            raise NotImplementedError(
                f"cn_baostock only supports A-share 6-digit symbols, got: {symbol}"
            )
        code = m.group(1)
        if code.startswith(("5", "6", "9")):
            return f"sh.{code}"
        return f"sz.{code}"

    @contextmanager
    def _session(self, timeout: float = DEFAULT_BAOSTOCK_SOCKET_TIMEOUT):
        with baostock_session(timeout=timeout) as bs:
            yield bs

    def _fetch_hist_df(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        start_boundary = pd.to_datetime(start_date, errors="coerce")
        end_boundary = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(start_boundary) or pd.isna(end_boundary):
            raise ValueError(
                f"Invalid date parameter for cn_baostock: start_date={start_date!r}, end_date={end_date!r}"
            )
        if start_boundary > end_boundary:
            raise ValueError(
                f"Invalid date range for cn_baostock: start_date ({start_date!r}) cannot be after end_date ({end_date!r})"
            )
        code = self._normalize_symbol(symbol)
        with self._session() as bs:
            rs = bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != "0":
                raise NotImplementedError(
                    f"baostock query failed: {rs.error_code} {rs.error_msg}"
                )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=rs.fields)
        rename_map = {
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
        df = df.rename(columns=rename_map)
        for c in ("Open", "High", "Low", "Close", "Volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Open", "High", "Low", "Close", "Volume"])
        df = df[(df["Date"] >= start_boundary) & (df["Date"] <= end_boundary)]
        return dedupe_daily_bars(
            df, "Date", ["Open", "High", "Low", "Close", "Volume"]
        )

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        df = self._fetch_hist_df(symbol, start_date, end_date)
        df = drop_incomplete_today_bar(df, "Date", end_date)
        if df.empty:
            return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        out = df.copy()
        out["Dividends"] = 0.0
        out["Stock Splits"] = 0.0
        out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")
        header = f"# Stock data for {symbol} from {start_date} to {end_date}\n"
        header += f"# Total records: {len(out)}\n\n"
        return header + out.to_csv(index=False)

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        if indicator not in self.INDICATOR_DESCRIPTIONS:
            raise ValueError(
                f"Indicator {indicator} is not supported. "
                f"Please choose from: {list(self.INDICATOR_DESCRIPTIONS.keys())}"
            )

        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - timedelta(days=max(look_back_days, 260))
        df = self._fetch_hist_df(symbol, start_dt.strftime("%Y-%m-%d"), curr_date)
        df = drop_incomplete_today_bar(df, "Date", curr_date)
        if df.empty:
            return f"No data found for {symbol} for indicator {indicator}"

        ind_df = df.rename(
            columns={
                "Date": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )[["date", "open", "high", "low", "close", "volume"]].copy()

        ss = wrap(ind_df)
        indicator_series = ss[indicator]
        values_by_date = {}
        for idx, dt_val in enumerate(ind_df["date"]):
            date_str = pd.to_datetime(dt_val).strftime("%Y-%m-%d")
            val = indicator_series.iloc[idx]
            values_by_date[date_str] = "N/A" if pd.isna(val) else str(val)

        begin = curr_dt - timedelta(days=look_back_days)
        lines = []
        d = curr_dt
        while d >= begin:
            key = d.strftime("%Y-%m-%d")
            value = values_by_date.get(key)
            if value is None:
                value = cn_no_data_reason(key)
            elif value == "N/A":
                value = cn_no_data_reason(key)
            lines.append(
                f"{key}: {value}"
            )
            d -= timedelta(days=1)

        result = (
            f"## {indicator} 指标值（{begin.strftime('%Y-%m-%d')} 至 {curr_date}）：\n\n"
            + "\n".join(lines)
            + "\n\n"
            + self.INDICATOR_DESCRIPTIONS[indicator]
        )
        return result

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        raise NotImplementedError("cn_baostock does not provide fundamentals yet.")

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("cn_baostock does not provide balance sheet yet.")

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("cn_baostock does not provide cashflow yet.")

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("cn_baostock does not provide income statement yet.")

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError("cn_baostock does not provide news yet.")

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        raise NotImplementedError("cn_baostock does not provide global news yet.")

    def get_insider_transactions(self, symbol: str, curr_date: str = None) -> str:
        raise NotImplementedError(
            "cn_baostock does not provide insider transactions yet."
        )
