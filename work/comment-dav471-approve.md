Cursor 独立复审 DAV-471 / P0-3a。

候选 SHA（完整 40 位）：`ba47284e3284fbed70fc104901fbe354907c7bee`
父提交：`6ea84afa21a3ef9bcbc9f82055dccaa3b8273aa5`
分支：`agent/dev2/p0-3a-period-kind`

Cursor 在隔离 worktree `/tmp/ta-p03a-ba47284` 用宿主 `.venv310` 复跑：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_financial_period_kind.py tests/test_financial_announce_cutoff.py -q --tb=short
```

结果：`53 passed in 0.39s`。不采信 Multica 口头 53 passed。

静态核对：
- 白名单 4 文件；未改公告日截断、资金 period_kind、社交、受保护脏文件。
- `0630` + income/cashflow → `half_year_cumulative` + `2024H1`；balance → `period_end_stock`。
- `_financial_report_sina` 四处 header 均传 `statement_kind`；摘要路径未改。
- 无 `single_quarter_derived` / H1−Q1。生产路径 `get_income_statement` / `get_cashflow` / `get_balance_sheet` 已锁。

**准予合入** SHA `ba47284e3284fbed70fc104901fbe354907c7bee`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。不要 FF 其它提交。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
