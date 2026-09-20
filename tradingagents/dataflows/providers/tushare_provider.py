import logging
import time
from typing import Any, Dict, Optional, Tuple, Union
import pandas as pd

from .base import BaseMarketDataProvider
from .industry_linkage_provider import _get_tushare_token, _query_tushare_api
from ..macro_market_utils import calculate_series_metrics, build_global_indices_markdown
from ..vendor_result import VendorFail

logger = logging.getLogger(__name__)

# ── 财报三表护栏（卡 2 采样结论 + §4.5 契约）──
# 网关 P95 达 7.7s、最大实测 11.8s，全局 10s 硬超时过窄：
# 拆分 (connect=3s, read=12s)，等效上限 15s，避免误杀正常传输。
_FINANCIAL_TIMEOUT: Tuple[float, float] = (3.0, 12.0)
# 严禁裸调：瞬时错误至少 1 次退避重试，吸收偶发网络毛刺。
_FINANCIAL_MAX_ATTEMPTS = 2
_FINANCIAL_RETRY_BACKOFF_S = 1.5
# 可重试的错误分类（瞬时）；token/403/parse/api/empty 不重试，立即 fail-closed。
_FINANCIAL_RETRYABLE_ERRORS = frozenset(
    {"timeout", "network_error", "rate_limited", "http_error"}
)

# 财报接口请求字段契约（Tushare 需显式声明，缺省字段是行情列）
_FINANCIAL_API_FIELDS: Dict[str, str] = {
    "income": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
        "basic_eps,total_revenue,revenue,total_cogs,oper_cost,operate_profit,"
        "total_profit,n_income,n_income_attr_p"
    ),
    "balancesheet": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
        "total_assets,total_liab,total_hldr_eqy_exc_min_int,total_hldr_eqy_inc_min_int,"
        "money_cap,acct_rcv,inventories,total_cur_assets,total_nca,"
        "total_cur_liab,total_ncl"
    ),
    "cashflow": (
        "ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,"
        "n_cashflow_act,n_cashflow_inv_act,n_cash_flows_fnc_act,"
        "c_cash_equ_end_period,net_profit"
    ),
}

# 渲染给 LLM 的列（存在才显示，缺失自动跳过）
_FINANCIAL_DISPLAY_COLUMNS: Dict[str, list[str]] = {
    "income": [
        "end_date", "ann_date", "total_revenue", "revenue", "total_cogs",
        "oper_cost", "operate_profit", "total_profit", "n_income",
        "n_income_attr_p", "basic_eps",
    ],
    "balancesheet": [
        "end_date", "ann_date", "total_assets", "total_liab",
        "total_hldr_eqy_exc_min_int", "money_cap", "acct_rcv", "inventories",
        "total_cur_assets", "total_cur_liab",
    ],
    "cashflow": [
        "end_date", "ann_date", "n_cashflow_act", "n_cashflow_inv_act",
        "n_cash_flows_fnc_act", "c_cash_equ_end_period", "net_profit",
    ],
}

_FINANCIAL_TITLES = {
    "income": "Income Statement",
    "balancesheet": "Balance Sheet",
    "cashflow": "Cashflow",
}

# 渲染给 LLM 的中文 canonical 表头（与 akshare/fuyao 输出口径一致）。
# 营业总成本(total_cogs) ⊃ 营业成本(oper_cost)，必须分列展示、禁止混用（DAV-1134）。
_FINANCIAL_HEADER_RENAMES: Dict[str, Dict[str, str]] = {
    "income": {
        "total_revenue": "营业总收入",
        "revenue": "营业收入",
        "total_cogs": "营业总成本",
        "oper_cost": "营业成本",
        "operate_profit": "营业利润",
        "total_profit": "利润总额",
        "n_income": "净利润",
        "n_income_attr_p": "归属于母公司所有者的净利润",
        "basic_eps": "基本每股收益",
    },
}

# 报告期重复判定时忽略的公告元数据列（更正公告日/更新标记变化不构成「值冲突」）
_FINANCIAL_CONFLICT_IGNORE_COLS = frozenset(
    {"ann_date", "f_ann_date", "update_flag", "ts_code", "end_date"}
)

_FINANCIAL_QUARTERLY_PERIODS = 6
_FINANCIAL_ANNUAL_PERIODS = 4


def _normalize_financial_ts_code(ticker: str) -> Optional[str]:
    """将各种写法归一化为 Tushare ts_code（如 600036 → 600036.SH）。

    非 A 股形态（含字母但无市场后缀）返回 None，由上层 VendorFail 走备用源。
    """
    if not ticker:
        return None
    code = str(ticker).strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if code.startswith(prefix) and code[len(prefix):].isdigit():
            code = f"{code[len(prefix):]}.{prefix}"
            break
    if "." in code:
        num, _, suffix = code.partition(".")
        if num.isdigit() and suffix in ("SH", "SZ", "BJ"):
            return f"{num}.{suffix}"
        return None
    if not code.isdigit() or len(code) != 6:
        return None
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return None


def _query_financial_api(api_name: str, ts_code: str):
    """带重试的财报接口调用：瞬时错误退避重试一轮，其余立即返回错误分类。

    Returns:
        (df, err_cat, err_note) 与 _query_tushare_api 同构。
    """
    last: Tuple[Optional[pd.DataFrame], Optional[str], Optional[str]] = (
        None, None, None,
    )
    for attempt in range(_FINANCIAL_MAX_ATTEMPTS):
        df, err_cat, err_note = _query_tushare_api(
            api_name=api_name,
            ts_code=ts_code,
            fields=_FINANCIAL_API_FIELDS[api_name],
            timeout=_FINANCIAL_TIMEOUT,
        )
        last = (df, err_cat, err_note)
        if err_cat is None:
            return last
        if err_cat not in _FINANCIAL_RETRYABLE_ERRORS:
            return last
        if attempt + 1 < _FINANCIAL_MAX_ATTEMPTS:
            logger.warning(
                "Tushare %s(%s) 瞬时失败(%s)，%.1fs 后重试",
                api_name, ts_code, err_cat, _FINANCIAL_RETRY_BACKOFF_S,
            )
            time.sleep(_FINANCIAL_RETRY_BACKOFF_S)
    return last


def _parse_financial_date(value: Any) -> Optional[str]:
    """YYYYMMDD / YYYY-MM-DD 归一化为 YYYY-MM-DD；无法解析返回 None。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    try:
        return pd.to_datetime(s, format="mixed").strftime("%Y-%m-%d")
    except Exception:
        return None


def _financial_report(
    api_name: str, ticker: str, freq: str, curr_date: Optional[str]
) -> Union[str, VendorFail]:
    """财报三表公共链路：拉取 → PIT 过滤 → 重复期折叠/冲突 fail-closed → 渲染。

    任一环节不可验证均返回 VendorFail（链路到 cn_akshare 兜底），严禁抛出未捕获异常。
    """
    if not _get_tushare_token():
        return VendorFail("Tushare Token 未配置 (TUSHARE_TOKEN missing)")

    as_of = _parse_financial_date(curr_date)
    if not as_of:
        return VendorFail(f"缺少或非法分析基准日 curr_date={curr_date!r} (tushare)")

    ts_code = _normalize_financial_ts_code(ticker)
    if not ts_code:
        return VendorFail(f"非 A 股代码或无法归一化: {ticker!r} (tushare)")

    try:
        df, err_cat, err_note = _query_financial_api(api_name, ts_code)
    except Exception as e:  # fail-closed：任何未预期异常都不得中断主流程
        logger.warning("Tushare %s(%s) 调用异常: %s", api_name, ts_code, e)
        return VendorFail(f"Tushare {api_name} 调用异常: {e}")

    if err_cat or df is None or df.empty:
        return VendorFail(
            f"Tushare {api_name}({ts_code}) 两轮调用均失败: {err_note or err_cat}"
        )

    if "end_date" not in df.columns:
        return VendorFail(f"Tushare {api_name} 响应缺少 end_date 报告期字段")

    ann_col = "f_ann_date" if "f_ann_date" in df.columns else "ann_date"
    if ann_col not in df.columns:
        return VendorFail(f"Tushare {api_name} 响应缺少公告日字段，无法验证 PIT")

    work = df.copy()
    work["_end"] = work["end_date"].map(_parse_financial_date)
    work["_ann"] = work[ann_col].map(_parse_financial_date)
    # ann_date 缺失/无法解析的行无法验证公告可见性，PIT 防线要求剔除
    work = work[work["_end"].notna() & work["_ann"].notna()]
    work = work[work["_ann"] <= as_of]  # 公告日可见性防线（防前视偏差）
    if work.empty:
        return VendorFail(
            f"Tushare {api_name}({ts_code}) 截至 {as_of} 无公告可见数据"
        )

    if str(freq).strip().lower() in ("annual", "annually"):
        work = work[work["_end"].str.endswith("12-31")]
        max_periods = _FINANCIAL_ANNUAL_PERIODS
    else:
        max_periods = _FINANCIAL_QUARTERLY_PERIODS
    if work.empty:
        return VendorFail(
            f"Tushare {api_name}({ts_code}) 截至 {as_of} 无年报报告期数据"
        )

    # 重复报告期处理（§4.5）：完全相同的行折叠；同期不同值 fail-closed 为【数据缺失】
    conflict_periods: list[str] = []
    clean_rows = []
    value_cols = [
        c for c in work.columns
        if c not in _FINANCIAL_CONFLICT_IGNORE_COLS and not c.startswith("_")
    ]
    for end_date, group in work.groupby("_end"):
        group = group.drop_duplicates()  # 完全相同的行折叠
        if "report_type" in group.columns and len(group) > 1:
            # 多报表类型并存时优先合并报表(report_type=1)；仍多行则判冲突
            consolidated = group[group["report_type"].astype(str) == "1"]
            if not consolidated.empty:
                group = consolidated.drop_duplicates()
        if len(group) > 1:
            distinct = group[value_cols].drop_duplicates()
            if len(distinct) > 1:
                conflict_periods.append(end_date)
                continue
        clean_rows.append(group.iloc[0])

    if not clean_rows:
        return VendorFail(
            f"Tushare {api_name}({ts_code}) 报告期全部存在同期不同值冲突，fail-closed"
        )

    clean = pd.DataFrame(clean_rows).sort_values("_end", ascending=False)
    latest_end = clean["_end"].iloc[0]
    selected_ends = list(clean["_end"].head(max_periods))
    # 被截断窗口内的冲突期并入展示，标注【数据缺失】
    window_conflicts = [p for p in sorted(conflict_periods, reverse=True) if p >= selected_ends[-1]]

    display_cols = [
        c for c in _FINANCIAL_DISPLAY_COLUMNS[api_name] if c in clean.columns
    ]
    table = clean[clean["_end"].isin(selected_ends)][display_cols].copy()
    renames = {"end_date": "报告期", "ann_date": "公告日", "f_ann_date": "公告日"}
    renames.update(_FINANCIAL_HEADER_RENAMES.get(api_name, {}))
    table = table.rename(columns=renames)
    for col in table.columns:
        if col in ("end_date", "报告期"):
            table[col] = clean[clean["_end"].isin(selected_ends)]["_end"].values
        elif col in ("ann_date", "公告日"):
            table[col] = clean[clean["_end"].isin(selected_ends)]["_ann"].values
    body = table.to_markdown(index=False)

    notes = []
    if window_conflicts:
        missing_lines = "\n".join(
            f"| {p} | 【数据缺失】同报告期存在不同值冲突，已 fail-closed |"
            for p in window_conflicts
        )
        notes.append(
            "\n以下报告期因同报告期不同值冲突被 fail-closed：\n"
            "| 报告期 | 状态 |\n|---|---|\n" + missing_lines
        )
    if conflict_periods:
        notes.append(
            f"共 {len(conflict_periods)} 个报告期因同期不同值被剔除"
        )

    title = _FINANCIAL_TITLES[api_name]
    return (
        f"## {title} ({ts_code})\n\n"
        f"数据截至公告日 {as_of}（PIT 过滤），最新报告期 {latest_end}，"
        f"来源 tushare。\n\n{body}"
        + ("\n" + "\n".join(notes) if notes else "")
    )

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
    ) -> Union[str, VendorFail]:
        """资产负债表：接入 Tushare balancesheet，PIT + 重复期契约 + 重试护栏。"""
        return _financial_report("balancesheet", ticker, freq, curr_date)

    def get_cashflow(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> Union[str, VendorFail]:
        """现金流量表：接入 Tushare cashflow，PIT + 重复期契约 + 重试护栏。"""
        return _financial_report("cashflow", ticker, freq, curr_date)

    def get_income_statement(
        self, ticker: str, freq: str = "quarterly", curr_date: str = None
    ) -> Union[str, VendorFail]:
        """利润表：接入 Tushare income，PIT + 重复期契约 + 重试护栏。"""
        return _financial_report("income", ticker, freq, curr_date)

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
