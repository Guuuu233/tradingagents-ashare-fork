Cursor 独立复审 DAV-485 / P1-1。

候选 SHA（完整 40 位）：`aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`
父提交：`3466e05a6483861cf071d4548b7bee990ac3c774`（线性，无 merge）
分支：`agent/dev2/p1-1-news-event-coverage`

隔离 worktree `/tmp/ta-p11-aa0742a5` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_news_event_coverage.py -q --tb=short
```

结果：**9 passed in 0.24s**。不采信口头；本次为 Cursor 独立复跑。

相对主干 9 文件、+929/−3。白名单内。未改社交、资金流、confirmation、受保护脏文件。

契约核对：
- 缺/乱 `published_at` → unverifiable；`first_seen_at` 不能顶替资格
- future > cutoff → 拒绝（R2：8/11 不可见）
- 7/29 可见；近重复 → 1 cluster
- `suspected_gaps` 文案为「未检索到/不可验证」，禁止「确认无新闻」
- news_analyst 返回 `event_coverage`；prompt 一句纪律到位

残留（不挡合入）：空 entity 时聚类偏宽；collector 与 analyst 各算一遍 coverage（冗余但语义一致）；未改 vendor 返回类型（本卡允许）。

**准予合入** SHA `aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。** 不宣称工业富联/蓝思案例已修。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
