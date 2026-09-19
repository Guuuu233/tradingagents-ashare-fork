import logging
from typing import Any, Dict, Optional, Union
import pandas as pd

from .base import BaseMarketDataProvider
from .industry_linkage_provider import _get_tushare_token, _query_tushare_api
from ..macro_market_utils import calculate_series_metrics, build_global_indices_markdown
from ..vendor_result import VendorFail

logger = logging.getLogger(__name__)

# 全球核心指数标的映射契约：(标准名称, Tushare代码, 显示代码)
# 修复契约：
# 1. 恒生科技指数映射为 HKTECH（非 HSTECH，HSTECH 在 Tushare 返回空）
# 2. 道琼斯映射为 DJI（非 DJIA）
# 3. 纳斯达克综合映射为 IXIC
TUSHARE_GLOBAL_TARGETS: list[tuple[str, str, str]] = [
    ("标普500", "SPX", ".INX"),
    ("纳斯达克综合", "IXIC", ".IXIC"),
    ("道琼斯", "DJI", ".DJI"),
    ("恒生指数", "HSI", "HSI"),
    ("恒生科技指数", "HKTECH", "HKTECH"),
    ("日经225", "N225", "N225"),
    ("韩国KOSPI", "KS11", "KS11"),
    ("德国DAX", "GDAXI", "GDAXI"),
    ("法国CAC40", "FCHI", "FCHI"),
    ("英国富时100", "FTSE", "FTSE"),
]


class TushareProvider(BaseMarketDataProvider):
    """正式接入的 Tushare 数据源 Provider。

    实现全球指数正式采集，遵循：
    1. 真实字段直取，不做任何缩放猜测（禁止 /100）；
    2. 严格按 instrument 合约与标的值域防线校验；
    3. 新鲜度闸校验；
    4. 逐指数保留 source='tushare' 与 actual_as_of；
    5. 未实现的方法（如 get_major_assets 等）不定义或抛 NotImplementedError，
       避免裸 VendorRefuse 打死链路；
    6. 基础设施性/数据源不可用（缺 Token、全源调用失败、数据全解析失败）显式返回 VendorFail，
       确保链路正常回退到 cn_akshare 兜底。
    """

    TARGET_SYMBOLS = TUSHARE_GLOBAL_TARGETS

    @property
    def name(self) -> str:
        return "tushare"

    def get_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_indicators(
        self, symbol: str, indicator: str, curr_date: str, look_back_days: int
    ) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_fundamentals(self, ticker: str, curr_date: str = None) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_balance_sheet(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_news(self, ticker: str, start_date: str, end_date: str) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_global_news(
        self, curr_date: str, look_back_days: int = 7, limit: int = 50
    ) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_insider_transactions(self, symbol: str, curr_date: str = None) -> str:
        raise NotImplementedError("TushareProvider currently only implements get_global_indices")

    def get_global_indices(
        self, curr_date: str = None, look_back_days: int = 30
    ) -> Union[str, VendorFail]:
        """获取全球核心市场指数时序与行情。

        如遇 Token 未配置、非法日期或全部接口调用/解析失败，返回 VendorFail 以便
        route_to_vendor 继续尝试后续 vendor (cn_akshare)。
        """
        if curr_date is None:
            return VendorFail("缺少分析基准日期 (tushare)")

        token = _get_tushare_token()
        if not token:
            return VendorFail("Tushare Token 未配置 (TUSHARE_TOKEN missing)")

        try:
            pd.to_datetime(curr_date)
        except Exception:
            return VendorFail(f"非法日期格式 {curr_date} (tushare)")

        results: Dict[str, Optional[Dict[str, Any]]] = {}
        error_reasons: list[str] = []

        for name, ts_code, display_code in self.TARGET_SYMBOLS:
            try:
                df, err_cat, err_note = _query_tushare_api(
                    api_name="index_global",
                    ts_code=ts_code,
                    as_of=curr_date,
                )
                if err_cat:
                    error_reasons.append(f"{ts_code}: {err_note or err_cat}")
            except Exception as e:
                logger.warning("Tushare query failed for %s (%s): %s", name, ts_code, e)
                error_reasons.append(f"{ts_code}: {e}")
                df = None

            if df is not None and not df.empty:
                metrics = calculate_series_metrics(
                    df,
                    curr_date,
                    instrument=ts_code,
                    max_stale_business_days=3,
                )
                if metrics:
                    metrics["code"] = display_code
                    metrics["source"] = "tushare"
                    results[name] = metrics
                else:
                    results[name] = None
            else:
                results[name] = None

        valid_count = sum(
            1 for v in results.values() if v is not None and v.get("latest_close") is not None
        )
        if valid_count == 0:
            err_summary = "; ".join(error_reasons[:3]) if error_reasons else "无有效数据"
            return VendorFail(f"所有全球指数 Tushare 接口调用失败或无有效数据: {err_summary}")

        return build_global_indices_markdown(results, curr_date, source="tushare")
