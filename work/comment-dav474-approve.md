Cursor 独立复审 DAV-474 / P0-3b（含 DAV-473 链）。

候选 SHA（完整 40 位）：`049b7d46ca3bdabe25006e3986e4457771dde3fa`
父提交：`5e360af95d3cd1c1a5ad88b35ce7fbd325b197fa`
祖先主干：`ba47284e3284fbed70fc104901fbe354907c7bee`
分支：`agent/dev2/p0-3b-q2-derived`

上一刀 High（H1 EPS 2.50 − Q1 1.00 写成假 Q2 EPS 1.5）已堵：可减白名单去掉 EPS；列名含「每股」进 missing。同 SHA 复现：`values` 仅有净利润 150，无 EPS。

Cursor 在隔离 worktree `/tmp/ta-p03b-049b7d4` 用宿主 `.venv310` 复跑：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_financial_period_kind.py tests/test_financial_announce_cutoff.py -q --tb=short
```

结果：`71 passed in 0.42s`。不采信 Multica 口头 71 passed。

相对主干 4 文件、+749。Sina / backup 两处接线仍在。H1 header 仍 `not_derived`。无 Q1 → `missing_q1`。balance 无 `single_quarter_derived` 金额。无 Q3/Q4 派生。未改受保护脏文件。

**准予合入** SHA `049b7d46ca3bdabe25006e3986e4457771dde3fa`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。不要 FF `5e360af`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
