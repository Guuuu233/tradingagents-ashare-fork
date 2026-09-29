"""DAV-1374 Tushare 新闻网关 get_global_news：分段拉取/截断检测/dateline/
去重/配额/缓存/回落路径。全部使用模拟数据，不访问真实网关。"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from tradingagents.dataflows import tushare_global_news as tgn
from tradingagents.dataflows import interface as iface


class FakeClient(tgn.TushareNewsClient):
    """内存客户端：handler(api, params, fields) -> (rows|None, err_cat, note)。"""

    def __init__(self, handler):
        super().__init__(api_url="http://fake", token="fake-token")
        self.handler = handler
        self.calls = []

    def query(self, api_name, params=None, fields=None):
        self.calls.append((api_name, dict(params or {}), fields))
        return self.handler(api_name, dict(params or {}), fields)


def _mk_news_row(dt, title, content="", channels="焦点"):
    return {"datetime": dt, "title": title, "content": content, "channels": channels}


# ── 客户端 ─────────────────────────────────────────────────


def test_client_missing_token_returns_token_error():
    client = tgn.TushareNewsClient(api_url="http://x", token="")
    rows, cat, note = client.query("news")
    assert rows is None and cat == "token"
    assert "missing" in note.lower()


def test_client_parses_items_to_dict_rows(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "code": 0,
        "data": {"fields": ["a", "b"], "items": [[1, 2], [3, 4]]},
    }
    monkeypatch.setattr(tgn.requests, "post", lambda *a, **k: resp)
    client = tgn.TushareNewsClient(api_url="http://x", token="t")
    rows, cat, note = client.query("news", {"p": 1}, "a,b")
    assert cat is None and rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_client_auth_error_category(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"code": 2002, "msg": "deny"}
    monkeypatch.setattr(tgn.requests, "post", lambda *a, **k: resp)
    client = tgn.TushareNewsClient(api_url="http://x", token="t")
    rows, cat, note = client.query("news")
    assert rows is None and cat == "403"


# ── 分段拉取与分页 ─────────────────────────────────────────


def test_pagination_assembles_all_pages(monkeypatch):
    # 缩小 page_size 以便模拟多页
    for spec in tgn._NEWS_SOURCE_SPECS:
        if spec["key"] == "news:sina":
            monkeypatch.setitem(spec, "page_size", 3)
    pages = {
        0: [_mk_news_row(f"2025-06-10 0{8 - i}:00:00", f"T{i}") for i in range(3)],
        3: [_mk_news_row("2025-06-10 01:00:00", "T-last")],
    }

    def handler(api, params, fields):
        if api == "news":
            off = params.get("offset", 0)
            return pages.get(off, []), None, None
        return [], None, None

    client = FakeClient(handler)
    pool, reports, fatal = tgn.fetch_news_pool(client, date(2025, 6, 10), date(2025, 6, 10))
    assert not fatal
    sina_rows = [r for r in pool if r["_source"] == "news:sina"]
    assert len(sina_rows) == 4


def test_unresponsive_pagination_subdivides_and_records_truncation():
    """网关忽略 offset（每页返回相同数据）→ 细分；到最小粒度仍截断 → 显式 fatal。"""
    full_page = [
        _mk_news_row("2025-06-10 00:30:00", f"X{i}") for i in range(800)
    ]

    def handler(api, params, fields):
        if api == "news" and params.get("src") == "sina":
            return list(full_page), None, None  # 永远返回满页且首条相同
        return [], None, None

    client = FakeClient(handler)
    pool, reports, fatal = tgn.fetch_news_pool(
        client, date(2025, 6, 10), date(2025, 6, 10)
    )
    # 细分到最小粒度仍截断 → fatal 显式记录，截断段行不进入结果池
    assert any("news:sina" in f for f in fatal)
    sina_reports = [r for r in reports if r.source == "news:sina"]
    assert len(sina_reports) > 1  # 发生了细分
    assert any(r.truncated for r in sina_reports)
    assert not [r for r in pool if r["_source"] == "news:sina"]


def test_query_error_marks_segment_failed():
    def handler(api, params, fields):
        if api == "news":
            return None, "network_error", "boom"
        return [], None, None

    client = FakeClient(handler)
    pool, reports, fatal = tgn.fetch_news_pool(client, date(2025, 6, 10), date(2025, 6, 10))
    # 三个 news 源全失败
    assert sum(1 for f in fatal if f.startswith("news:")) == 3


# ── 筛选层 ─────────────────────────────────────────────────


def _pool_row(source, dt_field, dt_val, title, content, **extra):
    row = {dt_field: dt_val, "title": title, "content": content}
    row.update(extra)
    row["_source"] = source
    return row


def test_time_cutoff_drops_future_pubtime():
    pool = [
        _pool_row("news:sina", "datetime", "2025-06-10 10:00:00", "在窗内", "", channels="焦点"),
        _pool_row("news:sina", "datetime", "2025-06-11 00:00:01", "越界未来", "", channels="焦点"),
    ]
    stats = {}
    out = tgn.filter_global_news(pool, "2025-06-10", 7, 50, stats)
    assert len(out) == 1 and out[0]["_title"] == "在窗内"
    assert stats["dropped"]["future_pubtime"] == 1


def test_dateline_future_dropped_but_schedule_kept():
    """反例：正文自称发稿日晚于元数据 → 剔除；正文提到未来日程 → 保留。"""
    pool = [
        _pool_row(
            "major_news", "pub_time", "2025-08-11 11:56:00",
            "元数据异常条目", "8月12日消息，某公司发布财报",
            src="财联社",
        ),
        _pool_row(
            "major_news", "pub_time", "2025-08-11 10:00:00",
            "正常条目", "央行公告称将于8月20日举行议息会议，8月25日消息披露安排待定",
            src="财联社",
        ),
    ]
    stats = {}
    out = tgn.filter_global_news(pool, "2025-08-11", 7, 50, stats)
    titles = [c["_title"] for c in out]
    assert "正常条目" in titles
    assert "元数据异常条目" not in titles
    assert stats["dropped"]["dateline_future"] == 1


def test_channel_policy_unknown_label_kept_and_logged(caplog):
    pool = [
        _pool_row("news:cls", "datetime", "2025-06-10 10:00:00", "央行开展逆回购操作", "", channels="新频道X"),
        _pool_row("news:cls", "datetime", "2025-06-10 10:05:00", "美联储宣布降息25个基点", "", channels="加红"),
    ]
    with caplog.at_level("INFO"):
        out = tgn.filter_global_news(pool, "2025-06-10", 7, 50, {})
    assert len(out) == 2
    assert any("unknown channel" in r.message for r in caplog.records)


def test_major_news_src_whitelist():
    pool = [
        _pool_row("major_news", "pub_time", "2025-06-10 10:00:00", "白名单来源", "", src="财联社"),
        _pool_row("major_news", "pub_time", "2025-06-10 10:01:00", "非白名单", "", src="某小站"),
    ]
    out = tgn.filter_global_news(pool, "2025-06-10", 7, 50, {})
    assert [c["_title"] for c in out] == ["白名单来源"]


def test_event_dedup_clusters_same_event():
    pool = [
        _pool_row("news:sina", "datetime", "2025-06-10 10:00:00",
                  "中美元首举行会晤", "短稿", channels="焦点"),
        _pool_row("major_news", "pub_time", "2025-06-10 10:30:00",
                  "中美元首举行会晤：双方发表联合声明", "详细长稿内容" * 50, src="财联社"),
        _pool_row("news:sina", "datetime", "2025-06-10 11:00:00",
                  "完全不同的事件标题", "", channels="焦点"),
    ]
    out = tgn.filter_global_news(pool, "2025-06-10", 7, 50, {})
    titles = [c["_title"] for c in out]
    # 会晤两条聚为一条，保留正文最完整的
    assert "中美元首举行会晤：双方发表联合声明" in titles
    assert "中美元首举行会晤" not in titles
    assert "完全不同的事件标题" in titles


def test_quota_covers_each_day_without_flat_average():
    pool = []
    # 第一天 5 条低分，后几天各 1 条高分
    for i in range(5):
        pool.append(_pool_row("news:sina", "datetime", f"2025-06-03 0{i}:00:00",
                              f"股市行情快报{i}", "", channels="国际"))
    for d in range(4, 11):
        pool.append(_pool_row("news:cls", "datetime", f"2025-06-{d:02d} 10:00:00",
                              f"重大{d}", "", channels="加红"))
    out = tgn.filter_global_news(pool, "2025-06-10", 7, 8, {})
    days = {c["_pub"].strftime("%Y-%m-%d") for c in out}
    # limit 不压低 30–50 条口径的下限：候选有多少取多少
    #（sina 五条近似标题被同事件去重合并为 1，7 天 cls + 1 sina = 8）
    assert len(out) == 8
    # 每个有新闻的日期至少保留 1 条（不机械按天平均）
    assert "2025-06-03" in days
    assert len(days) == 8


def test_limit_caps_at_50():
    # 标题用互不重复的随机汉字串，避免被事件去重聚合
    pool = [
        _pool_row(
            "news:sina", "datetime", f"2025-06-10 {i % 24:02d}:{i % 60:02d}:00",
            "".join(chr(0x4E00 + (i * 37 + k) % 2000) for k in range(8)),
            "", channels="焦点",
        )
        for i in range(60)
    ]
    out = tgn.filter_global_news(pool, "2025-06-10", 7, 999, {})
    assert len(out) == 50


# ── 缓存 ─────────────────────────────────────────────────


def test_raw_cache_roundtrip_and_ttl(tmp_path, monkeypatch):
    cache = tgn.TushareNewsCache(str(tmp_path))
    rows = [{"title": "a"}]
    cache.put_raw("news:sina", "2020-01-02", rows)
    assert cache.get_raw("news:sina", "2020-01-02") == rows
    assert cache.get_raw("news:sina", "2020-01-03") is None


def test_filtered_cache_skips_fetch(tmp_path):
    cache = tgn.TushareNewsCache(str(tmp_path))
    cache.put_filtered("2025-06-10", 7, 30, "## 缓存文本")
    client = FakeClient(lambda *a: (_ for _ in ()).throw(AssertionError("must not fetch")))
    text, err = tgn.get_global_news("2025-06-10", 7, 30, client=client, cache=cache)
    assert err is None and text == "## 缓存文本"


def test_raw_pool_cache_hit_skips_client(tmp_path):
    cache = tgn.TushareNewsCache(str(tmp_path))
    day = "2020-01-02"
    for spec in tgn._NEWS_SOURCE_SPECS:
        cache.put_raw(spec["key"], day, [{"title": "c", "datetime": f"{day} 10:00:00",
                                          "pub_time": f"{day} 10:00:00",
                                          "date": day.replace("-", "")}])
    client = FakeClient(lambda *a: (_ for _ in ()).throw(AssertionError("must not fetch")))
    pool, reports, fatal = tgn.fetch_news_pool(
        client, date(2020, 1, 2), date(2020, 1, 2), cache
    )
    assert not fatal
    assert len(pool) == len(tgn._NEWS_SOURCE_SPECS)


# ── 端到端 & 路由回落 ───────────────────────────────────────


def test_end_to_end_render_and_failure_modes(tmp_path):
    def handler(api, params, fields):
        if api == "news" and params.get("src") == "sina":
            if params.get("offset"):
                return [], None, None
            return [
                _mk_news_row("2025-06-10 10:00:00", "降息落地",
                             "<p>6月10日讯，美联储宣布降息</p>", "焦点"),
            ], None, None
        if api == "cctv_news":
            return [{"date": "20250610", "title": "联播", "content": "内容"}], None, None
        return [], None, None

    client = FakeClient(handler)
    text, err = tgn.get_global_news(
        "2025-06-10", 7, 30, client=client, cache=tgn.TushareNewsCache(str(tmp_path))
    )
    assert err is None
    assert "来源：Tushare 新闻网关" in text
    assert "降息落地" in text
    assert "发布时间: 2025-06-10 10:00:00" in text
    assert "<p>" not in text  # HTML 已清洗


def test_provider_returns_vendorfail_on_pool_truncation():
    from tradingagents.dataflows.providers.tushare_provider import TushareProvider
    from tradingagents.dataflows.vendor_result import VendorFail

    with patch(
        "tradingagents.dataflows.tushare_global_news.get_global_news",
        return_value=(None, "Tushare 新闻池存在截断段"),
    ):
        out = TushareProvider().get_global_news("2025-06-10", 7, 30)
    assert isinstance(out, VendorFail)
    assert "截断" in out.error


def test_router_falls_back_tushare_to_investoday():
    from tradingagents.dataflows.vendor_result import VendorFail

    tushare = MagicMock()
    tushare.get_global_news.return_value = VendorFail("tushare_news: gateway down")
    investoday = MagicMock()
    investoday.get_global_news.return_value = "## 全球市场新闻（来源：今日投资"

    with patch.object(iface, "_registry") as reg:
        reg.list_names.return_value = ["tushare", "cn_investoday"]
        reg.get.side_effect = {"tushare": tushare, "cn_investoday": investoday}.get
        with patch.object(iface, "get_vendor", return_value="tushare,cn_investoday"):
            out = iface.route_to_vendor("get_global_news", "2025-06-10", 7, 30)
    assert "今日投资" in out
    investoday.get_global_news.assert_called_once()


def test_router_both_fail_returns_explicit_failure():
    tushare = MagicMock()
    tushare.get_global_news.return_value = MagicMock()  # VendorFail-like
    from tradingagents.dataflows.vendor_result import VendorFail
    tushare.get_global_news.return_value = VendorFail("down")
    investoday = MagicMock()
    investoday.get_global_news.side_effect = OSError("net down")

    with patch.object(iface, "_registry") as reg:
        reg.list_names.return_value = ["tushare", "cn_investoday"]
        reg.get.side_effect = {"tushare": tushare, "cn_investoday": investoday}.get
        with patch.object(iface, "get_vendor", return_value="tushare,cn_investoday"):
            out = iface.route_to_vendor("get_global_news", "2025-06-10", 7, 30)
    assert out.startswith("【数据获取失败】")


def test_historical_allowlist_includes_tushare(past_date=None):
    from datetime import timedelta
    from tradingagents.dataflows.trade_calendar import now_cn

    past = (now_cn().date() - timedelta(days=90)).isoformat()
    tushare = MagicMock()
    tushare.get_global_news.return_value = "## 全球市场新闻（来源：Tushare"
    akshare = MagicMock()
    akshare.get_global_news.side_effect = AssertionError("not allowed")

    with patch.object(iface, "_registry") as reg:
        reg.list_names.return_value = ["tushare", "cn_akshare", "cn_investoday"]
        reg.get.side_effect = {
            "tushare": tushare, "cn_akshare": akshare, "cn_investoday": MagicMock(),
        }.get
        with patch.object(iface, "get_vendor", return_value="tushare,cn_akshare"):
            out = iface.route_to_vendor("get_global_news", past, 7, 30)
    assert "Tushare" in out
    akshare.get_global_news.assert_not_called()


class TestIntradayRawCachePromotion:
    """🟡 复审修复：当天写入的日内部分快照不得被次日'升格'为永久缓存。

    规则：raw 文件仅当写入时间已过该段日期的 CN 零点（段完整收口）才永久；
    日内中途写入的快照一律按 15min TTL 计。
    """

    def _write_payload(self, cache, name, ts):
        import json, os
        path = cache._path("raw", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": ts, "data": [{"title": "x"}]}, f)

    def _yesterday_and_ts(self):
        from datetime import datetime, timedelta
        from tradingagents.dataflows.trade_calendar import CN_TZ, cn_today_str
        today = cn_today_str()
        y = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1))
        y_str = y.strftime("%Y-%m-%d")
        noon = datetime(y.year, y.month, y.day, 10, 0, tzinfo=CN_TZ).timestamp()
        after = (y + timedelta(days=1, hours=1)).replace(tzinfo=CN_TZ).timestamp()
        return y_str, noon, after

    def test_intraday_snapshot_not_promoted(self, tmp_path):
        cache = tgn.TushareNewsCache(str(tmp_path))
        y_str, noon, _ = self._yesterday_and_ts()
        # 昨天 10:00 写入的昨日段：至今已超 15min，读侧必须判失效（不升格永久）
        self._write_payload(cache, "news_cls_%s.json" % y_str, noon)
        assert cache.get_raw("news:cls", y_str) is None

    def test_completed_day_is_permanent(self, tmp_path):
        cache = tgn.TushareNewsCache(str(tmp_path))
        y_str, _, after = self._yesterday_and_ts()
        # 次日 01:00 写入的昨日段（该日已完整收口）：永久有效
        self._write_payload(cache, "news_cls_%s.json" % y_str, after)
        assert cache.get_raw("news:cls", y_str) == [{"title": "x"}]

    def test_today_segment_short_ttl(self, tmp_path):
        import time as _time
        from tradingagents.dataflows.trade_calendar import cn_today_str
        cache = tgn.TushareNewsCache(str(tmp_path))
        today = cn_today_str()
        # 刚写入的当天段：15min 内命中
        self._write_payload(cache, "news_cls_%s.json" % today, _time.time())
        assert cache.get_raw("news:cls", today) == [{"title": "x"}]
        # 当天段但写入时间已在 20min 前：过期
        self._write_payload(cache, "news_cls_%s.json" % today, _time.time() - 1200)
        assert cache.get_raw("news:cls", today) is None

    def test_filtered_intraday_result_not_promoted(self, tmp_path):
        """D 日中途写入的筛选结果，D+1 后读取必须判过期（与 raw 同规则）。"""
        cache = tgn.TushareNewsCache(str(tmp_path))
        y_str, noon, after = self._yesterday_and_ts()
        import json, os
        # get_filtered 内部将 limit 归一为 50，构造键时需一致
        key = cache._filtered_key(y_str, 7, 50)
        path = cache._path("filtered", f"{key}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 昨日 10:00 写入的昨日筛选结果 → 至今已超 15min → 必须过期
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": noon, "data": "## 旧结果"}, f)
        assert cache.get_filtered(y_str, 7, 30) is None
        # 次日 01:00 写入的昨日筛选结果 → 永久有效
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": after, "data": "## 完整结果"}, f)
        assert cache.get_filtered(y_str, 7, 30) == "## 完整结果"
