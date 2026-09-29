"""Tushare 新闻网关 → get_global_news 主来源（DAV-1374）。

结构：
- ``TushareNewsClient``：与 ``_query_tushare_api`` 同协议的独立网关客户端，
  读取 ``TUSHARE_NEWS_API_URL`` / ``TUSHARE_NEWS_TOKEN``（与财报 ``TUSHARE_*``
  完全独立，token 不进入日志/错误信息）。
- ``fetch_news_pool``：按来源、按日期分段拉取原始池。单页 page_size 恒小于
  网关单请求上限（news 快讯 1500、major_news 800），段内 limit+offset 分页；
  若分页疑似失效（返回与上一页完全相同）或翻页到 MAX_PAGES 仍为满页，则递归
  二分细分时段，直至最小粒度；仍截断则显式标记 ``truncated``——截断段绝不
  当作完整结果，provider 层整体 VendorFail 回落今日投资。
- ``filter_global_news``：确定性筛选层（不调用 LLM）：
  时点过滤 → 频道策略（按来源、带版本号，未知标签记日志不静默丢弃）→
  dateline 异常剔除（仅剔除"正文自称发稿日晚于元数据日期"的条目；正文中的
  未来日程预告一律保留）→ 事件去重（标题标准化 + 时间邻近 + 字符 bigram
  Jaccard 聚类）→ 主题分类 → 打分（来源/频道/时间衰减）→ 配额（窗口内每天
  至少保留一条重大事件，其余按分排序，不按天平均）。
- 缓存：原始池按 (source, date) 存 JSON——历史日期永久有效，当天短 TTL；
  筛选结果按 (analysis_date, lookback_days, policy_version, limit) 缓存。
  目录为 ``data_cache_dir/tushare_news``（已被 .gitignore 的 data_cache 覆盖）。
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .config import get_config
from .trade_calendar import cn_today_str

logger = logging.getLogger(__name__)

# ── 网关客户端 ──────────────────────────────────────────────
_NEWS_TIMEOUT = 10
_NEWS_AUTH_CODES = {2001, 2002, 40101, 40102, 40103}
_NEWS_RATE_LIMIT_CODES = {2003, 40203, 40204, 40205, 40206}


def _get_news_api_url() -> str:
    config = get_config()
    return (
        str(config.get("tushare_news_api_url", "")).strip()
        or os.getenv("TUSHARE_NEWS_API_URL", "").strip()
    )


def _get_news_token() -> str:
    """安全读取新闻网关 token，严禁硬编码或打印到日志/错误信息。"""
    config = get_config()
    return (
        str(config.get("tushare_news_token", "")).strip()
        or os.getenv("TUSHARE_NEWS_TOKEN", "").strip()
    )


class TushareNewsClient:
    """Tushare 新闻独立权限网关客户端（协议同 _query_tushare_api）。"""

    def __init__(
        self,
        api_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = _NEWS_TIMEOUT,
    ) -> None:
        self._api_url = api_url if api_url is not None else _get_news_api_url()
        self._token = token if token is not None else _get_news_token()
        self._timeout = timeout

    # 可重试的瞬时错误；api_error 也重试一次（网关在并发下偶发返回非 0 code）
    _RETRYABLE = frozenset({"timeout", "network_error", "rate_limited", "http_error"})
    _RETRY_ATTEMPTS = 3
    _RETRY_BACKOFF_S = 1.5

    def query(
        self,
        api_name: str,
        params: Optional[Dict[str, Any]] = None,
        fields: Optional[str] = None,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], Optional[str]]:
        """返回 (rows, err_cat, err_note)。rows 为 dict 列表；失败为 None。

        瞬时错误（超时/网络/限频/http/网关偶发 api_error）带退避重试。
        """
        last: Tuple[Optional[List[Dict[str, Any]]], Optional[str], Optional[str]] = (
            None, "unknown", "no attempt"
        )
        attempts = self._RETRY_ATTEMPTS
        for i in range(attempts):
            last = self._query_once(api_name, params, fields)
            cat = last[1]
            if cat is None or cat == "empty_rows":
                return last
            if cat in self._RETRYABLE or (cat == "api_error" and i < attempts - 1):
                if i < attempts - 1:
                    time.sleep(self._RETRY_BACKOFF_S * (i + 1))
                continue
            return last
        return last

    def _query_once(
        self,
        api_name: str,
        params: Optional[Dict[str, Any]] = None,
        fields: Optional[str] = None,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str], Optional[str]]:
        if not self._token:
            return None, "token", "Tushare 新闻网关 Token 未配置 (TUSHARE_NEWS_TOKEN missing)"
        if not self._api_url:
            return None, "config", "Tushare 新闻网关地址未配置 (TUSHARE_NEWS_API_URL missing)"

        payload = {
            "api_name": api_name,
            "token": self._token,
            "params": dict(params or {}),
            "fields": fields or "",
        }
        try:
            resp = requests.post(self._api_url, json=payload, timeout=self._timeout)
        except requests.Timeout as e:
            return None, "timeout", f"Tushare 新闻请求超时: {type(e).__name__}"
        except requests.RequestException as e:
            return None, "network_error", f"Tushare 新闻请求异常: {type(e).__name__}"
        except Exception as e:  # pragma: no cover - 防御
            return None, "network_error", f"Tushare 新闻调用未知异常: {type(e).__name__}"

        if resp.status_code == 403:
            return None, "403", "Tushare 新闻 HTTP 403 权限不足"
        if resp.status_code == 429:
            return None, "rate_limited", "Tushare 新闻 HTTP 429 请求超限"
        if resp.status_code != 200:
            return None, "http_error", f"Tushare 新闻 HTTP 错误 (status={resp.status_code})"

        try:
            res_json = resp.json()
        except Exception:
            return None, "parse_error", "Tushare 新闻响应 JSON 解析失败"
        if not isinstance(res_json, dict):
            return None, "parse_error", "Tushare 新闻响应格式非法 (非 dict)"

        code = res_json.get("code")
        msg = str(res_json.get("msg") or "")
        try:
            code_val = int(code) if code is not None else -1
        except (TypeError, ValueError):
            code_val = -1

        if code_val != 0:
            msg_lower = msg.lower()
            # 不将 msg 原文带入返回说明，避免网关回显敏感信息
            if code_val in _NEWS_AUTH_CODES or any(
                k in msg_lower for k in ("权限", "permission", "403", "unauthor")
            ):
                return None, "403", f"Tushare 新闻 API 权限不足 (code={code_val})"
            if code_val in _NEWS_RATE_LIMIT_CODES or any(
                k in msg_lower for k in ("频率", "rate", "limit")
            ):
                return None, "rate_limited", f"Tushare 新闻 API 触发限频 (code={code_val})"
            return None, "api_error", f"Tushare 新闻 API 错误 (code={code_val})"

        data = res_json.get("data")
        if not isinstance(data, dict):
            return None, "empty_rows", "Tushare 新闻未返回有效数据（data 字段缺失）"
        resp_fields = data.get("fields") or []
        items = data.get("items") or []
        if not items:
            return [], None, None
        rows: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, (list, tuple)):
                rows.append({f: item[i] if i < len(item) else None for i, f in enumerate(resp_fields)})
            elif isinstance(item, dict):
                rows.append(dict(item))
        return rows, None, None


# ── 原始池分段拉取 ──────────────────────────────────────────
# 每页 page_size 恒小于网关单请求上限（cap），因此正常分页绝不会触碰上限；
# 细分递归仅作为分页失效时的兜底。
_NEWS_API_ROW_CAP = 1500
_MAJOR_NEWS_ROW_CAP = 800
_MAX_PAGES_PER_SEGMENT = 20
_MIN_SEGMENT_MINUTES = 30

# (source_key, api_name, extra_params, fields, time_field, page_size, cap)
_NEWS_SOURCE_SPECS: List[Dict[str, Any]] = [
    {
        "key": "news:cls",
        "api": "news",
        "params": {"src": "cls"},
        "fields": "datetime,title,channels",
        "fields_full": "datetime,title,content,channels",
        "time_field": "datetime",
        "page_size": 800,
        "row_cap": _NEWS_API_ROW_CAP,
        "segment": "day",
    },
    {
        "key": "news:wallstreetcn",
        "api": "news",
        "params": {"src": "wallstreetcn"},
        "fields": "datetime,title,channels",
        "fields_full": "datetime,title,content,channels",
        "time_field": "datetime",
        "page_size": 800,
        "row_cap": _NEWS_API_ROW_CAP,
        "segment": "day",
    },
    {
        "key": "news:sina",
        "api": "news",
        "params": {"src": "sina"},
        "fields": "datetime,title,channels",
        "fields_full": "datetime,title,content,channels",
        "time_field": "datetime",
        "page_size": 800,
        "row_cap": _NEWS_API_ROW_CAP,
        "segment": "day",
    },
    {
        "key": "major_news",
        "api": "major_news",
        "params": {},
        "fields": "title,pub_time,src",
        "fields_full": "title,content,pub_time,src",
        "time_field": "pub_time",
        "page_size": 400,
        "row_cap": _MAJOR_NEWS_ROW_CAP,
        "segment": "day",
    },
    # 东方财富 (src=eastmoney) 频道全部为 "7*24全球直播"，按策略默认排除，
    # 不发起拉取。
    {
        "key": "cctv_news",
        "api": "cctv_news",
        "params": {},
        "fields": "date,title,content",
        "fields_full": "date,title,content",
        "time_field": "date",
        "page_size": 0,  # 无分页，单请求全天
        "row_cap": 0,
        "segment": "day",
    },
]


def _dt_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class SegmentReport:
    """单个 (source, 日期, 时段) 拉取结果的可审计记录。"""

    __slots__ = ("source", "start", "end", "rows", "truncated", "errors")

    def __init__(self, source: str, start: datetime, end: datetime) -> None:
        self.source = source
        self.start = start
        self.end = end
        self.rows: List[Dict[str, Any]] = []
        self.truncated = False
        self.errors: List[str] = []


def _fetch_window_paginated(
    client: TushareNewsClient,
    spec: Dict[str, Any],
    start: datetime,
    end: datetime,
    report: SegmentReport,
    fields: Optional[str] = None,
) -> bool:
    """对 [start, end] 窗口分页拉取。返回 True=完整，False=截断/失败。"""
    page_size = spec["page_size"]
    req_fields = fields or spec["fields"]
    params_base = dict(spec["params"])
    params_base["start_date"] = _dt_str(start)
    params_base["end_date"] = _dt_str(end)

    offset = 0
    prev_first: Optional[str] = None
    for page in range(_MAX_PAGES_PER_SEGMENT):
        params = dict(params_base)
        params["limit"] = page_size
        if offset:
            params["offset"] = offset
        rows, err_cat, err_note = client.query(spec["api"], params, req_fields)
        if rows is None:
            report.errors.append(f"{err_cat}: {err_note}")
            return False
        if page == 0 and not rows:
            return True  # 确认空
        if not rows:
            return True  # 翻页耗尽
        # 分页失效检测：offset>0 时首页与上一页首条相同 → 网关忽略 offset，
        # 立即转细分而不是重复堆叠同一批数据。
        first_key = repr(rows[0])
        if offset and first_key == prev_first:
            report.errors.append("pagination-unresponsive: offset 被忽略")
            return False
        prev_first = first_key
        report.rows.extend(rows)
        if len(rows) < page_size:
            return True
        offset += page_size
    # 翻页到上限仍满页 → 疑似截断，交细分
    return False


def _fetch_segment(
    client: TushareNewsClient,
    spec: Dict[str, Any],
    start: datetime,
    end: datetime,
    reports: List[SegmentReport],
    depth: int = 0,
    fields: Optional[str] = None,
) -> None:
    report = SegmentReport(spec["key"], start, end)
    reports.append(report)
    ok = _fetch_window_paginated(client, spec, start, end, report, fields=fields)
    if ok:
        report.rows = _dedupe_rows(report.rows, spec)
        return
    span = end - start
    if span <= timedelta(minutes=_MIN_SEGMENT_MINUTES) or depth >= 8:
        report.truncated = True
        logger.warning(
            "tushare_news segment truncated: %s %s..%s errors=%s",
            spec["key"], _dt_str(start), _dt_str(end), report.errors,
        )
        return
    # 二分细分；细分后原 report 保留 errors 作为审计，但 rows 清空以免重复计数
    mid = start + span / 2
    mid = mid.replace(second=0, microsecond=0)
    report.rows = []
    _fetch_segment(client, spec, start, mid, reports, depth + 1, fields=fields)
    _fetch_segment(client, spec, mid, end, reports, depth + 1, fields=fields)


def _dedupe_rows(rows: List[Dict[str, Any]], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按 (标准化标题, 发布时间分钟) 去重分页重叠/重复行。"""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in rows:
        title = _normalize_title(str(r.get("title") or ""))
        tval = str(r.get(spec["time_field"]) or "")[:16]
        key = (title, tval, str(r.get("src") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _cctv_date_param(d: date) -> str:
    return d.strftime("%Y%m%d")


def _fetch_source_day(
    client: TushareNewsClient,
    spec: Dict[str, Any],
    day: date,
    fields: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[SegmentReport], Optional[str]]:
    """拉取一个 (source, 日期) 段。返回 (rows, reports, fatal_or_none)。"""
    seg_start = datetime(day.year, day.month, day.day)
    seg_end = seg_start + timedelta(days=1) - timedelta(seconds=1)
    reports: List[SegmentReport] = []
    if spec["api"] == "cctv_news":
        rep = SegmentReport(spec["key"], seg_start, seg_end)
        reports.append(rep)
        params = dict(spec["params"])
        params["date"] = _cctv_date_param(day)
        rows, err_cat, err_note = client.query(spec["api"], params, spec["fields"])
        if rows is None:
            rep.errors.append(f"{err_cat}: {err_note}")
            rep.truncated = True
            return [], reports, f"{spec['key']}@{day.isoformat()}: {err_cat}"
        rep.rows = rows
        return rows, reports, None
    _fetch_segment(client, spec, seg_start, seg_end, reports, fields=fields)
    seg_rows: List[Dict[str, Any]] = []
    seg_failed = False
    for rep in reports:
        seg_rows.extend(rep.rows)
        if rep.truncated:
            seg_failed = True
    if seg_failed:
        return [], reports, f"{spec['key']}@{day.isoformat()}: truncated/failed segment"
    return seg_rows, reports, None


def fetch_news_pool(
    client: TushareNewsClient,
    start_date: date,
    end_date: date,
    cache: Optional["TushareNewsCache"] = None,
    max_workers: int = 12,
) -> Tuple[List[Dict[str, Any]], List[SegmentReport], List[str]]:
    """拉取全部来源原始池。返回 (rows, reports, fatal_errors)。

    rows 中每条附带 ``_source``（spec key）。任何一段截断/失败都会显式记录
    在 reports / fatal_errors；存在 fatal_errors 时调用方必须走回落。
    (source, 日期) 段之间有界并发，串行拉满一个 7 天窗口在分钟级以内。
    """
    all_rows: List[Dict[str, Any]] = []
    reports: List[SegmentReport] = []
    fatal: List[str] = []

    pending: List[Tuple[Dict[str, Any], date]] = []
    day = start_date
    while day <= end_date:
        for spec in _NEWS_SOURCE_SPECS:
            cached_rows: Optional[List[Dict[str, Any]]] = None
            if cache is not None:
                cached_rows = cache.get_raw(spec["key"], day.isoformat())
            if cached_rows is not None:
                rep = SegmentReport(
                    spec["key"],
                    datetime(day.year, day.month, day.day),
                    datetime(day.year, day.month, day.day)
                    + timedelta(days=1) - timedelta(seconds=1),
                )
                rep.rows = cached_rows
                reports.append(rep)
                for r in cached_rows:
                    rr = dict(r)
                    rr["_source"] = spec["key"]
                    all_rows.append(rr)
            else:
                pending.append((spec, day))
        day += timedelta(days=1)

    if pending:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [
                (spec, day, ex.submit(_fetch_source_day, client, spec, day))
                for spec, day in pending
            ]
            for spec, day, fut in futures:
                rows, reps, fatal_err = fut.result()
                reports.extend(reps)
                if fatal_err is not None:
                    if spec["key"] in _NONFATAL_SOURCES:
                        logger.warning(
                            "tushare_news degraded segment (non-fatal): %s",
                            fatal_err,
                        )
                    else:
                        fatal.append(fatal_err)
                    continue
                if cache is not None:
                    cache.put_raw(spec["key"], day.isoformat(), rows)
                for r in rows:
                    rr = dict(r)
                    rr["_source"] = spec["key"]
                    all_rows.append(rr)
    return all_rows, reports, fatal


# ── 筛选层 ─────────────────────────────────────────────────
FILTER_POLICY_VERSION = "v3.1"  # 口径：条目≤50、正文截100字（体积≤对照源1.5×）

# 频道策略：每来源 label→tier；tier ∈ {high, medium, supplemental, exclude}
# 未知标签 → 记入日志并归入 "supplemental"（不静默丢弃）。
SOURCE_CHANNEL_POLICY: Dict[str, Any] = {
    "version": FILTER_POLICY_VERSION,
    "news:cls": {
        "high": {"加红"},
        "medium": {"看盘", "公司", "港美股"},
        "supplemental": {"基金", "提醒"},
    },
    "news:wallstreetcn": {
        "high": {"要闻"},
        "supplemental": {
            "A股", "债券", "外汇", "石油", "大宗", "黄金", "黄金外汇",
            "美股", "港股", "金融", "科技",
        },
    },
    "news:sina": {
        "high": {"焦点", "宏观", "央行"},
        "supplemental": {"国际"},
    },
    "news:eastmoney": {"exclude": {"*"}},  # 全部为 "7*24全球直播"
    # major_news 无频道列，以 src 白名单为准
    "major_news": {
        "src_whitelist": {
            "财联社", "华尔街见闻", "财新网", "新华网", "中证网",
            "中新经纬", "新浪财经", "同花顺", "凤凰财经",
        },
        "high_src": {"财联社", "华尔街见闻", "财新网", "新华网"},
    },
    "cctv_news": {"high": {"*"}},
}

_TIER_ORDER = {"high": 3, "medium": 2, "supplemental": 1, "exclude": 0}

_SOURCE_WEIGHT = {
    "news:cls": 3.0,
    "news:wallstreetcn": 3.0,
    "news:sina": 2.0,
    "major_news": 2.5,
    "cctv_news": 3.0,
}

# 主题分类 v3：两阶段。先判中国语境锚点（机构/工具/市场），命中即归入
# 国内主题；否则按海外主题分类。避免 "非农就业" 里的 "就业" 等泛词
# 把海外数据条目错标为国内宏观。
_CN_ANCHORS: Tuple[str, ...] = (
    "国务院", "国常会", "国新办", "发改委", "财政部", "商务部", "证监会",
    "金融监管", "统计局", "全国人大", "全国政协", "政协", "两会", "政治局",
    "政府工作报告", "赤字率", "特别国债", "降准", "LPR", "MLF", "逆回购",
    "买断式逆回购", "存量房贷", "互换便利", "再贷款", "汇金", "中国人民银行",
    "央行", "海关总署", "工信部", "中方", "我国", "人民币", "A股", "沪指",
    "创业板", "科创板", "沪深", "新三板", "北交所", "社保基金",
)
_CN_FISCAL_POLICY = ("财政", "减税", "专项债", "国债发行", "政策", "补贴",
                     "关税", "加征", "反制", "贸易协定", "出口管制", "产业",
                     "规划", "战略")
_CN_MARKET_EVENT = ("停牌", "复牌", "退市", "熔断", "IPO", "新股", "并购",
                    "重组", "解禁", "减持", "增持", "回购", "分红", "配股",
                    "ST", "龙虎榜", "涨停", "跌停")
_GLOBAL_TOPIC_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("地缘政治", ("冲突", "战争", "制裁", "导弹", "军演", "袭击", "伊朗", "俄罗",
                 "乌克兰", "以色列", "加沙", "红海", "朝鲜", "台海", "关税威胁",
                 "特朗普威胁", "会晤", "谈判破裂", "元首", "国事访问", "访美",
                 "访华", "白宫", "会谈", "通电话", "磋商")),
    ("全球央行/利率", ("美联储", "联储", "加息", "降息", "利率决议", "鲍威尔",
                     "欧洲央行", "日本央行", "FOMC", "加息预期", "降息预期",
                     "缩表", "量化宽松", "非农", "SOFR", "通胀率", "纪要")),
    ("原油/黄金/大宗", ("原油", "油价", "OPEC", "欧佩克", "黄金", "金价", "白银",
                      "铜价", "大宗", "铁矿", "天然气", "布伦特", "WTI")),
    ("股债汇", ("美股", "标普", "纳斯达克", "道指", "日经", "DAX", "收益率",
              "美债", "国债", "汇率", "美元", "股市", "债市", "汇市", "港股")),
]


def _classify_topic(text: str) -> str:
    has_cn = any(kw in text for kw in _CN_ANCHORS)
    if has_cn:
        for kw in _CN_MARKET_EVENT:
            if kw in text:
                return "A股重大市场事件"
        for kw in _CN_FISCAL_POLICY:
            if kw in text:
                return "财政/政策"
        return "国内宏观/货币"
    for topic, keywords in _GLOBAL_TOPIC_RULES:
        for kw in keywords:
            if kw in text:
                return topic
    return "其他"

_TOPIC_BONUS = {
    # v3：国内政策与数据事件最高优先，高于海外央行讲话/交易员押注类
    "国内宏观/货币": 1.8,
    "财政/政策": 1.6,
    "地缘政治": 1.2,
    "全球央行/利率": 0.9,
    "原油/黄金/大宗": 0.8,
    "股债汇": 0.9,
    "A股重大市场事件": 0.9,
    "其他": 0.0,
}

# 重大事件标题关键词（确定性加权；均为宏观/政策/地缘级事件锚点）
_EVENT_KEYWORDS: Tuple[str, ...] = (
    "降息", "降准", "加息", "FOMC", "纪要", "非农", "CPI", "PPI", "PMI",
    "GDP", "LPR", "政治局", "两会", "政府工作报告", "赤字率", "特别国债",
    "关税", "加征", "反制", "汇金", "国事访问", "会谈", "会晤", "通话",
    "磋商", "签署", "暴跌", "熔断", "国新办", "互换便利", "再贷款",
    "存量房贷", "买断式逆回购", "杰克逊霍尔", "霍尔木兹", "红海", "访华",
    "访美", "白宫", "收益率新高", "GDP目标", "全国人大", "政协", "中全会",
    "国事访问", "美债", "金价", "储备", "增持", "进出口", "峰会", "开幕",
    # v3 补充：经济数据/央行决议/军事冲突类锚点（含盲列实测缺口）
    "非农", "CPI", "PPI", "PMI", "纪要", "议息", "加息", "降息", "胡塞",
    "空袭", "OPEC", "欧佩克", "演习", "演训", "议息会议", "采购经理",
)
_EVENT_TITLE_BONUS = 3.0

# ── v3 返修：排序层规则 ─────────────────────────────────────
# 新闻联播（cctv_news）只保留经济/金融/产业/市场/宏观政策类条目：
# 标题（含正文首 200 字）必须命中下列锚点词才保留，否则一律排除；
# 时政/外事/会务/纪念/宣传类即使提及合作也不放行。
_CCTV_ECON_ANCHORS: Tuple[str, ...] = (
    "经济", "金融", "财政", "央行", "降准", "降息", "利率", "LPR",
    "逆回购", "货币", "通胀", "CPI", "PPI", "GDP", "PMI", "社融",
    "信贷", "存款", "贷款", "汇率", "人民币", "美元", "债券", "国债",
    "股市", "证券", "基金", "期货", "A股", "沪指", "创业板", "科创板",
    "港股", "美股", "上市", "退市", "并购", "重组", "分红", "回购",
    "减持", "增持", "关税", "贸易", "进出口", "外贸", "出口", "进口",
    "制造业", "工业", "产能", "营商", "消费", "零售", "物价", "就业",
    "失业", "工资", "收入", "房地产", "楼市", "房贷", "土地", "大宗",
    "原油", "油价", "黄金", "金价", "铜", "钢铁", "粮食", "能源",
    "电力", "煤炭", "天然气", "物流", "航运", "运价", "供应链", "统计",
    "数据", "增长", "预算", "赤字", "税", "补贴", "投资", "融资",
    "保险", "银行", "债", "企业",
)
# cctv 每日上限 5 条（时政噪声源；保底阶段也受约束）
_CCTV_DAILY_CAP = 5
# cctv 仪式/时政动词：即便标题含经济泛词（如“投资峰会开幕式”）也排除，
# 除非命中硬宏观锚点（数据/政策动作类）。
_CCTV_CEREMONY_RE = re.compile(
    r"(会见|会晤|会谈|出席|主持|看望|慰问|视察|考察|调研|致辞|开幕式|"
    r"闭幕式|开幕|闭幕|启动|纪念|宣讲|宣誓|献花|致信|回信|致贺|贺信|"
    r"发表署名文章|参观|观摩|座谈会|拜会|通话|通电话|致电|当选|审议|"
    r"主席团|报告会|宣传周|出版发行|祝贺|致以|学习问答|走基层|接受审查|"
    r"接受调查|被开除党籍|双开)"
)
# 非 cctv 源同样排除会务/仪式噪声，但保留会谈/会晤/通话/磋商等
# 外交接触动词（元首通话、经贸磋商本身就是市场事件）
_CEREMONY_RE_ALL = re.compile(
    r"(出席|主持|看望|慰问|视察|考察|调研|致辞|开幕式|闭幕式|开幕|闭幕|"
    r"启动|纪念|宣讲|宣誓|献花|致信|回信|致贺|贺信|发表署名文章|参观|"
    r"观摩|座谈会|当选|审议|主席团|报告会|宣传周|出版发行|致以|祝贺|"
    r"学习问答|走基层|接受审查|接受调查|被开除党籍|双开|当选)"
)
# 硬宏观锚点命中时仪式动词放行（如“央行宣布降准”“国家统计局发布CPI”）
_HARD_ANCHOR_ALL: Tuple[str, ...] = (
    "GDP", "CPI", "PPI", "PMI", "降准", "降息", "加息", "利率", "LPR",
    "逆回购", "MLF", "进出口", "外贸", "关税", "赤字", "特别国债",
    "政府工作报告", "社融", "同比", "环比", "百分点", "亿元", "万亿",
    "指数", "数据", "统计", "增速", "油价", "金价", "汇率", "存款",
    "贷款", "用电量", "产销", "规模以上", "增加值", "固定资产",
    "磋商", "谈判", "协议", "签署", "制裁", "关税", "法案",
)
_CCTV_HARD_ANCHORS: Tuple[str, ...] = (
    "GDP", "CPI", "PPI", "PMI", "降准", "降息", "利率", "LPR", "逆回购",
    "进出口", "外贸", "关税", "赤字", "特别国债", "政府工作报告", "社融",
    "同比", "环比", "百分点", "亿元", "万亿", "指数", "数据", "统计", "增长",
    "增速", "油价", "金价", "汇率", "利率", "存款", "贷款", "就业", "失业",
    "用电量", "货运", "快递", "票房", "产销", "规模以上", "零售", "销售",
    "增加值", "固定资产", "投资完成", "房地产", "楼市", "房价",
)

# 连续评论类快讯（交易员押注/官员放风/预期管理）：按 (日, 源) 设上限
_FLASH_COMMENTARY_RE = re.compile(
    r"(交易员|押注|降息预期|加息预期|放风|暗示|官员|声称|表示：|称：|"
    r"据悉|定价|概率|下调.{0,6}预期|削减.{0,6}预期|料将|或将继续)"
)
_COMMENTARY_PER_DAY_SOURCE_CAP = 2

# 琐事过滤：个别公司 CEO 言论、体育文娱等（A股重大市场事件除外）
_TRIVIA_RE = re.compile(
    r"(CEO|球星|赛季|夺冠|票房|演唱会|综艺|明星|电影|剧集|足球|篮球|"
    r"赛事|选手|奥运|世界杯|娱乐圈|网红|主播|离婚|结婚|逝世|去世)"
)

# 标题尾部媒体栏目后缀（去重前剥掉，避免 "xx｜环球市场" 类变体逃过合并）
_TITLE_SUFFIX_RE = re.compile(r"[|｜].{0,12}$")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_PUNCT_RE = re.compile(r"[\s　，,。.!！?？:：;；'\"'\"（）()【】\[\]《》<>|/\\\-—_…·]+")
# 正文开头自称发稿日："6月10日讯"、"6月10日消息"、"6月10日电"、"财联社6月10日讯"
_DATELINE_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日(消息|电|讯|快讯)")
_CONTENT_MAX_CHARS = 100
_OUTPUT_MAX_CHARS = 60000  # 约为今日投资典型输出(~40k 字符)的 1.5 倍上限


def strip_html(text: Any) -> str:
    """去 HTML 标签并压缩空白。"""
    if not text:
        return ""
    s = html.unescape(str(text))
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()


def _normalize_title(title: str) -> str:
    s = unicodedata.normalize("NFKC", title or "")
    s = _TITLE_SUFFIX_RE.sub("", s)
    return _TITLE_PUNCT_RE.sub("", s).lower()


def _title_tokens(title: str) -> frozenset:
    """标题字符 bigram 集合（中文无分词依赖）。"""
    s = _normalize_title(title)
    if len(s) <= 2:
        return frozenset([s]) if s else frozenset()
    return frozenset(s[i:i + 2] for i in range(len(s) - 1))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def _overlap_coef(a: frozenset, b: frozenset) -> float:
    """min(len) 归一的重合度：短标题是长标题近子集时判为同事件。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / min(len(a), len(b))


def _parse_row_time(row: Dict[str, Any], time_field: str) -> Optional[datetime]:
    raw = row.get(time_field)
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _dateline_self_date(content_text: str) -> Optional[Tuple[int, int]]:
    """正文开头首句内"X月X日消息/电/讯"的自称发稿日 (month, day)。

    只针对正文开头首句（至首个句读符号或前 80 字）的自称发稿日期格式；
    正文后续出现的未来日程（预告、会议、假期安排）不在首句范围内，必须保留。
    """
    text = (content_text or "")[:80]
    m_end = re.search(r"[。！？；，、：!?;\n,]", text)
    head = text[: m_end.start()] if m_end else text
    m = _DATELINE_RE.search(head)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _dateline_is_future(month: int, day: int, pub: datetime) -> bool:
    """自称发稿日是否显著晚于元数据日期（跨年用最近一年解释）。"""
    best: Optional[date] = None
    for year in (pub.year, pub.year - 1, pub.year + 1):
        try:
            cand = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((cand - pub.date()).days) < abs((best - pub.date()).days):
            best = cand
    if best is None:
        return False
    return (best - pub.date()).days >= 1


def _row_tier(source: str, row: Dict[str, Any]) -> Tuple[str, List[str]]:
    """返回 (tier, unknown_labels)。不在策略中的来源默认 supplemental。"""
    policy = SOURCE_CHANNEL_POLICY.get(source)
    if policy is None:
        return "supplemental", [f"<no-policy:{source}>"]
    if source == "major_news":
        src = str(row.get("src") or "").strip()
        whitelist = set(policy.get("src_whitelist") or set())
        if whitelist and src not in whitelist:
            return "exclude", [f"<src:{src}>"]
        high = set(policy.get("high_src") or set())
        return ("high" if src in high else "medium"), []
    raw = row.get("channels")
    if raw is None or str(raw).strip() == "":
        labels = ["<empty>"]
    else:
        labels = [x.strip() for x in str(raw).split(",") if x.strip()]
    tiers: List[str] = []
    unknown: List[str] = []
    for lab in labels:
        tier = None
        for t, labs in policy.items():
            if t in ("src_whitelist", "high_src"):
                continue
            if "*" in labs or lab in labs:
                tier = t
                break
        if tier is None:
            unknown.append(lab)
            tier = "supplemental"  # 未知标签不静默丢弃
        tiers.append(tier)
    # 全部标签均显式排除才排除该行；否则取最高 tier
    non_excluded = [t for t in tiers if t != "exclude"]
    if not non_excluded:
        return "exclude", unknown
    best = max(non_excluded, key=lambda t: _TIER_ORDER.get(t, 0))
    return best, unknown


def _time_decay(pub: datetime, analysis_end: datetime) -> float:
    age_h = max(0.0, (analysis_end - pub).total_seconds() / 3600.0)
    return min(age_h / 24.0 * 0.15, 1.2)  # 每天衰减 0.15，封顶 1.2


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


_DEDUP_WINDOW_H = 24
_DEDUP_JACCARD = 0.5
# 聚类前每（日, 来源）候选上限：按来源分桶而非全局 top-N，防止高频来源
# （如 wscn 要闻）淹没国内政策类条目；最终配额 ≤50，分桶上限远超需要
_PRECLUSTER_PER_DAY_SOURCE_CAP = 80
# 段失败级别：cctv_news 为补充来源（每日单请求、网关偶发 api_error），
# 其失败记为降级不致命；news/major_news 截断仍致命（不得当作完整结果）。
_NONFATAL_SOURCES = frozenset({"cctv_news"})

# 配额层每来源软上限（占最终条数比例）与保底条数
_PER_SOURCE_CAP_RATIO = 0.3
_PER_SOURCE_FLOOR = 3
# 政策/宏观密度最高的两个来源：每窗口日各保底 1 条（cctv=新闻联播通稿，
# major_news=综合要闻），保证两会、国新办、元首会晤等重大事件不缺席
_DAILY_SOURCE_FLOOR_SOURCES = frozenset({"cctv_news", "major_news"})


def _cluster_events(cands: List[Dict[str, Any]]) -> List[List[int]]:
    """同一事件聚类：标题 bigram Jaccard ≥ 阈值 且发布时间邻近。

    按时间排序后只与 24h 滑窗内的前驱比较，避免 O(n²) 全配对。
    """
    n = len(cands)
    uf = _UnionFind(n)
    toks = [c["_tokens"] for c in cands]
    norm_titles = [c["_norm_title"] for c in cands]
    times = [c["_pub"] for c in cands]
    order = sorted(range(n), key=lambda i: times[i])
    window = timedelta(hours=_DEDUP_WINDOW_H)
    for a in range(n):
        i = order[a]
        # 必须从最近的先行者往回扫：time 升序排列下越早差距越大，
        # 正向遍历遇超时即 break 会漏掉真正的近邻
        for b in range(a - 1, -1, -1):
            j = order[b]
            if times[i] - times[j] > window:
                break
            na, nb = norm_titles[i], norm_titles[j]
            if na and na == nb:
                uf.union(i, j)
                continue
            # 同事件常见形态：一条标题是另一条的前缀/子串（追加细节）
            if na and nb and min(len(na), len(nb)) >= 4 and (na in nb or nb in na):
                uf.union(i, j)
                continue
            if _jaccard(toks[i], toks[j]) >= _DEDUP_JACCARD:
                uf.union(i, j)
                continue
            # 近重复标题：短标题 bigram 重合度 ≥0.6 判同事件（变体/栏目后缀）
            if _overlap_coef(toks[i], toks[j]) >= 0.6:
                uf.union(i, j)
    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)
    return list(groups.values())


def _pick_representative(group: List[int], cands: List[Dict[str, Any]]) -> int:
    """簇内保留正文最完整、来源优先级最高的一条。"""
    def key(i: int):
        c = cands[i]
        return (
            len(c["_clean_content"]),
            _SOURCE_WEIGHT.get(c["_source"], 0.0),
            c["_pub"],
        )
    return max(group, key=key)


def filter_global_news(
    pool: List[Dict[str, Any]],
    analysis_date: str,
    look_back_days: int,
    limit: int,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """确定性筛选。返回按分数降序的选中条目（含渲染字段）。"""
    end_dt = datetime.strptime(analysis_date, "%Y-%m-%d") + timedelta(days=1) - timedelta(seconds=1)
    start_dt = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=look_back_days)

    if stats is None:
        stats = {}
    stats.setdefault("pool_rows", len(pool))
    stats.setdefault("dropped", {})

    def _drop(reason: str) -> None:
        stats["dropped"][reason] = stats["dropped"].get(reason, 0) + 1

    cands: List[Dict[str, Any]] = []
    for row in pool:
        source = row.get("_source") or ""
        spec = next((s for s in _NEWS_SOURCE_SPECS if s["key"] == source), None)
        time_field = spec["time_field"] if spec else "datetime"
        pub = _parse_row_time(row, time_field)
        if pub is None:
            _drop("no_pubtime")
            continue
        if pub > end_dt:
            _drop("future_pubtime")
            continue
        if pub < start_dt:
            _drop("before_window")
            continue
        tier, unknown = _row_tier(source, row)
        for lab in unknown:
            logger.info("tushare_news unknown channel label: source=%s label=%r", source, lab)
        if tier == "exclude":
            _drop("channel_excluded")
            continue
        title = _WS_RE.sub(" ", strip_html(row.get("title")))[:120]
        clean = strip_html(row.get("content"))
        # cctv：时政/外事/会务/纪念类一律排除——标题必须命中经济/金融/
        # 产业/市场/宏观政策锚点才保留（正文易含“合作/投资”等泛词导致放水，
        # 仅看标题；联播快讯类无标题信息的聚合条目一并排除）；含仪式动词且
        # 无硬宏观锚点的“领导出席/会见/致辞”类同样排除
        _hard_anchor = any(kw in title for kw in _HARD_ANCHOR_ALL)
        if source == "cctv_news":
            _has_anchor = any(kw in title for kw in _CCTV_ECON_ANCHORS)
            _ceremony = bool(_CCTV_CEREMONY_RE.search(title))
            if not _has_anchor or (_ceremony and not _hard_anchor):
                _drop("cctv_non_econ")
                continue
        elif _CEREMONY_RE_ALL.search(title) and not _hard_anchor:
            # 全源会务噪声排除（两会闭幕程序稿、领导出席致辞、干部任命等）
            _drop("ceremony")
            continue
        # 琐事过滤：CEO 言论、体育文娱等（A股重大市场事件除外）
        topic_pre = _classify_topic(f"{title} {clean[:200]}")
        if _TRIVIA_RE.search(title) and topic_pre != "A股重大市场事件":
            _drop("trivia")
            continue
        # 低信号：非高 tier 的 "其他" 主题条目（非重大事件锚点）
        has_event_kw = any(kw in title for kw in _EVENT_KEYWORDS)
        if (
            topic_pre == "其他"
            and not has_event_kw
            and tier in ("supplemental", "medium")
        ):
            _drop("low_signal")
            continue
        # dateline 反异常：正文自称发稿日明显晚于元数据日期 → 元数据不可靠
        dl = _dateline_self_date(clean)
        if dl is not None and _dateline_is_future(dl[0], dl[1], pub):
            _drop("dateline_future")
            continue
        cands.append({
            "_row": row,
            "_source": source,
            "_pub": pub,
            "_tier": tier,
            "_title": title,
            "_norm_title": _normalize_title(title),
            "_tokens": _title_tokens(title),
            "_clean_content": clean,
            "_topic": topic_pre,
            "_commentary": bool(_FLASH_COMMENTARY_RE.search(title)),
            "_event_kw": has_event_kw,
        })
    stats["after_filters"] = len(cands)

    # 打分（在去重前完成：高分代表优先保留）
    for c in cands:
        src_w = _SOURCE_WEIGHT.get(c["_source"], 1.0)
        tier_w = {"high": 2.0, "medium": 1.0, "supplemental": 0.4}.get(c["_tier"], 0.4)
        topic_w = _TOPIC_BONUS.get(c["_topic"], 0.0)
        c["_score"] = src_w + tier_w + topic_w - _time_decay(c["_pub"], end_dt)
        if any(kw in c["_title"] for kw in _EVENT_KEYWORDS):
            c["_score"] += _EVENT_TITLE_BONUS

    # 预裁剪：按（日, 来源）分桶取 top-N，防止高频来源淹没国内政策类条目；
    # 最终产出 ≤50 条，低分长尾对结果无影响，分桶上限已远超需要。
    by_bucket: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for c in cands:
        by_bucket.setdefault(
            (c["_pub"].strftime("%Y-%m-%d"), c["_source"]), []
        ).append(c)
    trimmed: List[Dict[str, Any]] = []
    for key, bucket in by_bucket.items():
        bucket.sort(key=lambda c: (-c["_score"], -c["_pub"].timestamp()))
        trimmed.extend(bucket[:_PRECLUSTER_PER_DAY_SOURCE_CAP])
    stats["after_pretrim"] = len(trimmed)

    # 事件去重 + 事件显著性：同事件被越多来源/条目报道越重大，
    # 簇大小作为确定性加分（log 缩放，封顶 2.0）。
    import math

    kept: List[Dict[str, Any]] = []
    for group in _cluster_events(trimmed):
        rep = trimmed[_pick_representative(group, trimmed)]
        rep["_score"] += min(0.6 * math.log2(max(len(group), 1)), 2.0)
        kept.append(rep)
    stats["after_dedup"] = len(kept)
    kept.sort(key=lambda c: (-c["_score"], -c["_pub"].timestamp()))

    # 配额：① 窗口内每个有候选的日期保底 1 条最高分；② 权威宏观源每日
    # 保底 1 条；③ 每来源保底 _PER_SOURCE_FLOOR 条；④ 其余按分填满，单一
    # 来源不得超过 _PER_SOURCE_CAP_RATIO——保证来源多样性、不按天平均。
    # 卡文口径：最终 30–50 条；limit 仅作上限（封顶 50），下限按 40 执行。
    max_items = min(max(int(limit), 50), 50)
    per_source_cap = max(int(max_items * _PER_SOURCE_CAP_RATIO), _PER_SOURCE_FLOOR)
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    by_src: Dict[str, List[Dict[str, Any]]] = {}
    for c in kept:
        by_day.setdefault(c["_pub"].strftime("%Y-%m-%d"), []).append(c)
        by_src.setdefault(c["_source"], []).append(c)
    selected: List[Dict[str, Any]] = []
    chosen_ids: set = set()
    src_count: Dict[str, int] = {}
    day_src_cnt: Dict[Tuple[str, str], int] = {}
    commentary_count: Dict[Tuple[str, str], int] = {}
    analysis_day = analysis_date
    _CN_POLICY_TOPICS = frozenset({"国内宏观/货币", "财政/政策", "A股重大市场事件"})

    def _add(
        c: Dict[str, Any],
        enforce_cap: bool = True,
        bypass_source_cap: bool = False,
    ) -> bool:
        if len(selected) >= max_items or id(c) in chosen_ids:
            return False
        src = c["_source"]
        day_key = c["_pub"].strftime("%Y-%m-%d")
        if enforce_cap:
            if not bypass_source_cap and src_count.get(src, 0) >= per_source_cap:
                return False
            # cctv 每日上限（时政噪声源）
            if src == "cctv_news" and day_src_cnt.get((day_key, src), 0) >= _CCTV_DAILY_CAP:
                return False
            # 评论类快讯每（日,源）上限
            if c.get("_commentary") and commentary_count.get(
                (day_key, src), 0
            ) >= _COMMENTARY_PER_DAY_SOURCE_CAP:
                return False
        selected.append(c)
        chosen_ids.add(id(c))
        src_count[src] = src_count.get(src, 0) + 1
        day_src_cnt[(day_key, src)] = day_src_cnt.get((day_key, src), 0) + 1
        if c.get("_commentary"):
            commentary_count[(day_key, src)] = (
                commentary_count.get((day_key, src), 0) + 1
            )
        return True

    for day_key in sorted(by_day):
        # 每日覆盖下限优先于来源多样性：保底不入则该日完全缺席；
        # 每日保底 2 条最高分（窗口内每日“头条级”事件必入选）
        for top in sorted(by_day[day_key], key=lambda c: -c["_score"])[:2]:
            _add(top, enforce_cap=False)
    # 分析日当天的国内重大政策必须入选（保底 3 条，绕过来源/评论上限）
    cn_today = [
        c for c in by_day.get(analysis_day, [])
        if c["_topic"] in _CN_POLICY_TOPICS
    ]
    for c in sorted(cn_today, key=lambda c: -c["_score"])[:3]:
        _add(c, enforce_cap=False)
    for day_key in sorted(by_day):
        # 权威宏观源每日保底 1 条
        day_srcs = {s: False for s in _DAILY_SOURCE_FLOOR_SOURCES}
        for c in sorted(by_day[day_key], key=lambda c: -c["_score"]):
            if len(selected) >= max_items:
                break
            src = c["_source"]
            if src in day_srcs and not day_srcs[src] and id(c) not in chosen_ids:
                # cctv 保底仍受每日上限约束（时政噪声源不可绕过）
                if _add(c, enforce_cap=(src == "cctv_news")):
                    day_srcs[src] = True
    for src in by_src:
        floor_cands = sorted(by_src[src], key=lambda c: -c["_score"])
        for c in floor_cands[:_PER_SOURCE_FLOOR]:
            _add(c)
    for c in kept:
        if len(selected) >= max_items:
            break
        # 事件锚点条目（降准/LPR/会议纪要/元首通话等）只受 cctv 每日上限与
        # 评论类上限约束，不因来源软上限出局
        _add(c, bypass_source_cap=bool(c.get("_event_kw")))
    # 来源多样性为软约束：其它来源已耗尽而仍有空位时，放宽上限填满
    if len(selected) < max_items:
        for c in kept:
            if len(selected) >= max_items:
                break
            if id(c) in chosen_ids:
                continue
            selected.append(c)
            chosen_ids.add(id(c))
            src_count[c["_source"]] = src_count.get(c["_source"], 0) + 1
    selected.sort(key=lambda c: c["_pub"])
    stats["selected"] = len(selected)
    stats["days_covered"] = len({c["_pub"].strftime("%Y-%m-%d") for c in selected})
    stats["_kept_tail"] = [
        c for c in kept if id(c) not in chosen_ids
    ][: _ENRICH_BENCH + max_items]
    return selected


def render_global_news(
    selected: List[Dict[str, Any]],
    analysis_date: str,
    look_back_days: int,
    start_label: str,
) -> str:
    latest = max(c["_pub"] for c in selected)
    parts: List[str] = [
        f"## 全球市场新闻（来源：Tushare 新闻网关；数据窗口：{start_label} 至 {analysis_date}；"
        f"筛选策略 {FILTER_POLICY_VERSION}；最新发布时间：{latest.strftime('%Y-%m-%d %H:%M:%S')}）：\n"
    ]
    total = len(parts[0])
    for c in selected:
        content = c["_clean_content"][:_CONTENT_MAX_CHARS]
        src_label = {
            "news:cls": "财联社快讯",
            "news:wallstreetcn": "华尔街见闻",
            "news:sina": "新浪快讯",
            "major_news": f"major_news/{c['_row'].get('src', '')}",
            "cctv_news": "新闻联播",
        }.get(c["_source"], c["_source"])
        block = (
            f"### {c['_title']}\n"
            f"发布时间: {c['_pub'].strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"来源: {src_label} | 主题: {c['_topic']}\n"
        )
        if content:
            block += f"正文: {content}\n"
        block += "\n"
        if total + len(block) > _OUTPUT_MAX_CHARS and len(parts) > 1:
            break
        parts.append(block)
        total += len(block)
    return "".join(parts).rstrip() + "\n"


# ── 正文回补（两阶段：扫描不拉正文，只为入选条目回补）────────
_ENRICH_BENCH = 10  # 入选外候补，dateline 剔除后顶替


def _row_match_key(source: str, row: Dict[str, Any], time_field: str) -> Tuple[str, str, str]:
    return (
        _normalize_title(str(row.get("title") or "")),
        str(row.get(time_field) or "")[:16],
        str(row.get("src") or ""),
    )


def enrich_selected_content(
    client: TushareNewsClient,
    selected: List[Dict[str, Any]],
    bench: List[Dict[str, Any]],
    max_items: int,
    cache: Optional["TushareNewsCache"] = None,
    stats: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """为入选+候补条目回补正文（按来源/日聚合，全量段带 content 字段重拉）。

    回补后对入选条目重新做 dateline 自检；被剔除者由候补按分递补。
    """
    need_days: Dict[Tuple[str, date], None] = {}
    for c in list(selected) + list(bench):
        if c["_clean_content"]:
            continue
        need_days[(c["_source"], c["_pub"].date())] = None

    content_map: Dict[Tuple[str, str, str], str] = {}
    specs_needed: Dict[str, Dict[str, Any]] = {}
    for (src, day) in list(need_days):
        spec = next((s for s in _NEWS_SOURCE_SPECS if s["key"] == src), None)
        if spec is None or spec["api"] == "cctv_news":
            continue
        specs_needed[src] = spec

    def _enrich_one(src: str, day: date) -> List[Dict[str, Any]]:
        spec = specs_needed[src]
        if cache is not None:
            hit = cache.get_raw(f"{src}__full", day.isoformat())
            if hit is not None:
                return hit
        rows, _reps, ferr = _fetch_source_day(
            client, spec, day, fields=spec.get("fields_full")
        )
        if ferr is not None:
            logger.warning("tushare_news enrich failed: %s", ferr)
            return []
        if cache is not None and rows is not None:
            cache.put_raw(f"{src}__full", day.isoformat(), rows)
        return rows or []

    tasks = [
        (src, day)
        for (src, day) in need_days
        if src in specs_needed
    ]
    if tasks:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as ex:
            for (src, day), rows in zip(
                tasks, ex.map(lambda t: _enrich_one(*t), tasks)
            ):
                spec = specs_needed[src]
                for r in rows:
                    k = _row_match_key(src, r, spec["time_field"])
                    txt = strip_html(r.get("content"))
                    if txt:
                        content_map[k] = txt

    for c in list(selected) + list(bench):
        if c["_clean_content"]:
            continue
        key = _row_match_key(c["_source"], c["_row"], _spec_time_field(c))
        c["_clean_content"] = content_map.get(key, "")
        if not c["_clean_content"] and stats is not None:
            # 回补失败/无正文：条目保留（降级），dateline 终检跳过，计数观测
            stats.setdefault("dropped", {})["enrich_empty_content"] = (
                stats["dropped"].get("enrich_empty_content", 0) + 1
            )

    # dateline 终检：仅在有正文可核时执行
    final: List[Dict[str, Any]] = []
    dropped_ids: set = set()
    for c in selected:
        dl = _dateline_self_date(c["_clean_content"])
        if dl is not None and _dateline_is_future(dl[0], dl[1], c["_pub"]):
            dropped_ids.add(id(c))
            if stats is not None:
                stats["dropped"]["dateline_future_enriched"] = (
                    stats["dropped"].get("dateline_future_enriched", 0) + 1
                )
            continue
        final.append(c)
    for c in bench:
        if len(final) >= max_items:
            break
        if id(c) in dropped_ids:
            continue
        dl = _dateline_self_date(c["_clean_content"])
        if dl is not None and _dateline_is_future(dl[0], dl[1], c["_pub"]):
            continue
        final.append(c)
    final.sort(key=lambda c: c["_pub"])
    return final


def _spec_time_field(c: Dict[str, Any]) -> str:
    spec = next((s for s in _NEWS_SOURCE_SPECS if s["key"] == c["_source"]), None)
    return spec["time_field"] if spec else "datetime"


# ── 缓存 ───────────────────────────────────────────────────
_RAW_TODAY_TTL_S = 15 * 60
_FILTERED_TODAY_TTL_S = 15 * 60


class TushareNewsCache:
    """本地 data_cache_dir 下 JSON 文件缓存（不进仓库）。"""

    def __init__(self, base_dir: Optional[str] = None) -> None:
        config = get_config()
        self._base = (
            base_dir
            or os.path.join(
                str(config.get("data_cache_dir", "data")), "tushare_news"
            )
        )

    def _path(self, kind: str, name: str) -> str:
        return os.path.join(self._base, kind, name)

    @staticmethod
    def _load(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict) or "data" not in payload:
            return None
        return payload

    def _read(self, path: str, ttl: Optional[float]) -> Optional[Any]:
        payload = self._load(path)
        if payload is None:
            return None
        if ttl is not None:
            ts = payload.get("ts", 0)
            if time.time() - float(ts) > ttl:
                return None
        return payload["data"]

    def _write(self, path: str, data: Any) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "data": data}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            logger.warning("tushare_news cache write failed: %s", path)

    @staticmethod
    def _date_ttl(date_str: str, written_ts: float) -> Optional[float]:
        """按日 keyed 缓存（raw 段 / filtered 结果）统一有效期规则：

        - 当天：15min TTL；
        - 历史日期：仅当文件写入时间已过该日 CN 零点（即该日已完整收口后
          才写入）才永久有效——日内中途写入的快照即使过了零点也降级为
          15min TTL，防止部分日快照/结果在次日被'升格'为永久缓存。"""
        if date_str >= cn_today_str():
            return _RAW_TODAY_TTL_S
        from .trade_calendar import CN_TZ

        day_end = (
            datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=CN_TZ)
            + timedelta(days=1)
        ).timestamp()
        if written_ts >= day_end:
            return None
        return _RAW_TODAY_TTL_S

    def _read_dated(
        self, path: str, date_str: str
    ) -> Optional[Any]:
        """读取按日 keyed 缓存，套用 _date_ttl 写入时间感知规则。"""
        payload = self._load(path)
        if payload is None:
            return None
        ts = float(payload.get("ts") or 0)
        ttl = self._date_ttl(date_str, ts)
        if ttl is not None and time.time() - ts > ttl:
            return None
        return payload["data"]

    def get_raw(self, source: str, date_str: str) -> Optional[List[Dict[str, Any]]]:
        name = f"{source.replace(':', '_')}_{date_str}.json"
        return self._read_dated(self._path("raw", name), date_str)

    def put_raw(self, source: str, date_str: str, rows: List[Dict[str, Any]]) -> None:
        name = f"{source.replace(':', '_')}_{date_str}.json"
        self._write(self._path("raw", name), rows)

    def _filtered_key(
        self, analysis_date: str, look_back_days: int, limit: int
    ) -> str:
        raw = f"{analysis_date}|{look_back_days}|{FILTER_POLICY_VERSION}|{limit}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def get_filtered(
        self, analysis_date: str, look_back_days: int, limit: int
    ) -> Optional[str]:
        # 与 raw 同一规则：分析日当天中途生成的结果不永久化——
        # 仅当写入时间已过该日 CN 零点才视为完整永久，否则 15min TTL。
        limit = min(max(int(limit), 50), 50)
        return self._read_dated(
            self._path("filtered", f"{self._filtered_key(analysis_date, look_back_days, limit)}.json"),
            analysis_date,
        )

    def put_filtered(
        self, analysis_date: str, look_back_days: int, limit: int, text: str
    ) -> None:
        limit = min(max(int(limit), 50), 50)
        self._write(
            self._path("filtered", f"{self._filtered_key(analysis_date, look_back_days, limit)}.json"),
            text,
        )


# ── 对外入口 ───────────────────────────────────────────────
def get_global_news(
    curr_date: str,
    look_back_days: int = 7,
    limit: int = 50,
    client: Optional[TushareNewsClient] = None,
    cache: Optional[TushareNewsCache] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """返回 (rendered_text, error_reason)。error_reason 非空时必须回落。

    注：limit 按卡文口径仅作上限（封顶 50），实现上固定按 50 条筛选，
    传入更小值不会减少条数——如需更少的条数请改 FILTER_POLICY_VERSION
    系列常量，勿依赖本参数。
    """
    try:
        end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    except ValueError:
        return None, f"分析日期非法: {curr_date!r}"
    start_dt = end_dt - timedelta(days=look_back_days)
    start_label = start_dt.strftime("%Y-%m-%d")

    if cache is None:
        cache = TushareNewsCache()
    cached = cache.get_filtered(curr_date, look_back_days, min(max(int(limit), 50), 50))
    if cached is not None:
        return cached, None

    if client is None:
        client = TushareNewsClient()

    pool, reports, fatal = fetch_news_pool(
        client, start_dt.date(), end_dt.date(), cache
    )
    seg_count = len(reports)
    err_segs = [r for r in reports if r.truncated or r.errors]
    logger.info(
        "tushare_news pool: %d rows, %d segments, %d bad segments for %s lookback=%d",
        len(pool), seg_count, len(err_segs), curr_date, look_back_days,
    )
    if fatal:
        return None, (
            f"Tushare 新闻池存在截断/失败段（{'; '.join(fatal[:5])}），"
            "不得将截断结果当作完整结果"
        )
    if not pool:
        return None, "Tushare 新闻网关全部来源无数据或不可用"

    stats: Dict[str, Any] = {}
    selected = filter_global_news(pool, curr_date, look_back_days, limit, stats)
    if not selected:
        return None, "Tushare 新闻池经筛选后为空"
    max_items = min(max(int(limit), 50), 50)
    bench = stats.pop("_kept_tail", [])
    selected = enrich_selected_content(
        client, selected, bench, max_items, cache=cache, stats=stats
    )
    logger.info(
        "tushare_news filter stats: %s",
        {k: v for k, v in stats.items() if not k.startswith("_")},
    )
    if not selected:
        return None, "Tushare 新闻池经筛选后为空"

    text = render_global_news(selected, curr_date, look_back_days, start_label)
    cache.put_filtered(curr_date, look_back_days, min(max(int(limit), 50), 50), text)
    return text, None
