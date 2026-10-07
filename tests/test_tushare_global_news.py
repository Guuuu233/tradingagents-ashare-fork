"""DAV-1374 Tushare 新闻网关 get_global_news：分段拉取/截断检测/dateline/
去重/配额/缓存/回落路径。全部使用模拟数据，不访问真实网关。"""

from __future__ import annotations

import os
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

    def query(self, api_name, params=None, fields=None, deadline=None):
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


class TestV4IncidentFixes:
    """v4 上线事故修复：窗口封顶 / single-flight / 总预算 / 429 退避。"""

    def _pool(self, day="2025-06-10"):
        return [{
            "_source": "news:cls", "datetime": f"{day} 10:00:00",
            "title": "央行宣布降准0.5个百分点", "content": "x", "channels": "加红",
        }]

    def test_lookback_clamped_to_7d_and_header_noted(self, tmp_path, monkeypatch):
        seen = {}

        def fake_pool(client, s, e, cache=None, max_workers=12, deadline=None):
            seen["range"] = (s, e)
            return (self._pool(), [], [])

        monkeypatch.setattr(tgn, "fetch_news_pool", fake_pool)
        text, err = tgn.get_global_news(
            "2025-06-10", 90, 30,
            client=FakeClient(lambda *a: ([], None, None)),
            cache=tgn.TushareNewsCache(str(tmp_path)),
        )
        assert err is None and text
        assert "回看已封顶为近 7 天" in text
        assert (seen["range"][1] - seen["range"][0]).days == 7

    def test_single_flight_one_fetch_for_concurrent_calls(self, tmp_path, monkeypatch):
        import threading, time as _t
        calls = {"n": 0}

        def fake_pool(*a, **k):
            calls["n"] += 1
            _t.sleep(0.15)
            return (self._pool(), [], [])

        monkeypatch.setattr(tgn, "fetch_news_pool", fake_pool)
        results = []

        def worker(d):
            text, err = tgn.get_global_news(
                "2025-06-10", 7, 30,
                client=FakeClient(lambda *a: ([], None, None)),
                cache=tgn.TushareNewsCache(str(tmp_path / d)),
            )
            results.append(err)

        threads = [threading.Thread(target=worker, args=(f"c{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert calls["n"] == 1          # 只拉取一次
        assert all(e is None for e in results)

    def test_budget_exceeded_returns_fallback_error(self, tmp_path, monkeypatch):
        def fake_pool(*a, **k):
            return ([], [], ["news:cls@2025-06-10: budget_exceeded"])

        monkeypatch.setattr(tgn, "fetch_news_pool", fake_pool)
        text, err = tgn.get_global_news(
            "2025-06-10", 7, 30,
            client=FakeClient(lambda *a: ([], None, None)),
            cache=tgn.TushareNewsCache(str(tmp_path)),
        )
        assert text is None and "超时" in err

    def test_429_retries_with_backoff(self, monkeypatch):
        attempts = {"n": 0}
        r429 = MagicMock(); r429.status_code = 429
        r200 = MagicMock(); r200.status_code = 200
        r200.json.return_value = {
            "code": 0, "data": {"fields": ["t"], "items": [["x"]]},
        }

        def post(*a, **k):
            attempts["n"] += 1
            return r429 if attempts["n"] < 3 else r200

        monkeypatch.setattr(tgn.requests, "post", post)
        monkeypatch.setattr(tgn.time, "sleep", lambda *a: None)
        client = tgn.TushareNewsClient(api_url="http://x", token="t")
        rows, cat, _ = client.query("news", {}, "t")
        assert cat is None and rows == [{"t": "x"}]
        assert attempts["n"] == 3


class TestCacheDirRelocation:
    """DAV-1388：新闻缓存目录移出发布目录，跨发布共享不随部署清零。"""

    def test_default_cache_dir_outside_release_tree(self, monkeypatch):
        """默认 data_cache_dir 不在仓库/发布目录内，指向 XDG state 下稳定路径。"""
        monkeypatch.delenv("TA_DATA_CACHE_DIR", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        import importlib
        import tradingagents.default_config as dc

        importlib.reload(dc)
        try:
            cache_dir = dc.DEFAULT_CONFIG["data_cache_dir"]
        finally:
            importlib.reload(dc)
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assert not os.path.abspath(cache_dir).startswith(repo_root + os.sep)
        assert "releases" not in cache_dir.split(os.sep)
        assert cache_dir.endswith(os.path.join("tradingagents", "data_cache"))
        assert ".local" in cache_dir and "state" in cache_dir

    def test_xdg_state_home_respected(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TA_DATA_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        import importlib
        import tradingagents.default_config as dc

        importlib.reload(dc)
        try:
            cache_dir = dc.DEFAULT_CONFIG["data_cache_dir"]
        finally:
            importlib.reload(dc)
        assert cache_dir == str(tmp_path / "xdg" / "tradingagents" / "data_cache")

    def test_env_override_must_be_absolute_and_outside_releases(
        self, tmp_path, monkeypatch
    ):
        import importlib
        import tradingagents.default_config as dc

        try:
            # 相对路径拒绝
            monkeypatch.setenv("TA_DATA_CACHE_DIR", "relative/dir")
            with pytest.raises(ValueError):
                importlib.reload(dc)
            # 发布目录内拒绝
            monkeypatch.setenv(
                "TA_DATA_CACHE_DIR",
                str(tmp_path / "releases" / "abc" / "cache"),
            )
            with pytest.raises(ValueError):
                importlib.reload(dc)
            # 合法绝对路径接受
            monkeypatch.setenv(
                "TA_DATA_CACHE_DIR", str(tmp_path / "stable_cache")
            )
            importlib.reload(dc)
            assert dc.DEFAULT_CONFIG["data_cache_dir"] == str(
                tmp_path / "stable_cache"
            )
        finally:
            monkeypatch.delenv("TA_DATA_CACHE_DIR")
            importlib.reload(dc)

    def test_cache_base_dir_defaults_to_config(self, tmp_path, monkeypatch):
        """TushareNewsCache 不传 base_dir 时走 config['data_cache_dir']/tushare_news。"""
        monkeypatch.setenv("TA_DATA_CACHE_DIR", str(tmp_path / "cc"))
        from tradingagents.dataflows.config import set_config
        import tradingagents.default_config as dc
        import importlib

        importlib.reload(dc)
        try:
            set_config(dc.DEFAULT_CONFIG)
            cache = tgn.TushareNewsCache()
            assert cache._base == os.path.join(
                str(tmp_path / "cc"), "tushare_news"
            )
        finally:
            monkeypatch.delenv("TA_DATA_CACHE_DIR")
            set_config({})

    def test_cache_survives_release_dir_swap(self, tmp_path, monkeypatch):
        """同一 data_cache_dir 下的缓存在两个“发布目录”进程间共享。"""
        shared = tmp_path / "shared_state" / "tradingagents" / "data_cache"
        monkeypatch.setenv("TA_DATA_CACHE_DIR", str(shared))
        from tradingagents.dataflows.config import set_config
        import tradingagents.default_config as dc
        import importlib

        importlib.reload(dc)
        try:
            set_config(dc.DEFAULT_CONFIG)
            rel_a = tgn.TushareNewsCache()  # 模拟发布目录 A 进程
            rel_a.put_raw("news:cls", "2020-01-02", [{"title": "a"}])
            rel_b = tgn.TushareNewsCache()  # 模拟发布目录 B 进程（新 sha）
            assert rel_b.get_raw("news:cls", "2020-01-02") == [{"title": "a"}]
            # 文件不落在任何 releases 目录下
            assert "releases" not in rel_a._base.split(os.sep)
        finally:
            monkeypatch.delenv("TA_DATA_CACHE_DIR")
            set_config({})

    def test_concurrent_writes_no_corruption(self, tmp_path):
        """多线程并发写同一缓存键：读到的一定是完整 JSON（原子改名）。"""
        import concurrent.futures as cf

        cache = tgn.TushareNewsCache(str(tmp_path))

        def write_loop(tag):
            for i in range(10):
                cache.put_raw("news:cls", "2020-01-02", [{"title": f"{tag}-{i}"}])

        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(write_loop, range(6)))
        got = cache.get_raw("news:cls", "2020-01-02")
        assert isinstance(got, list) and got and "title" in got[0]
        # 无残留 tmp 文件
        raw_dir = tmp_path / "raw"
        assert not [p for p in raw_dir.iterdir() if p.name.endswith(".tmp")]
