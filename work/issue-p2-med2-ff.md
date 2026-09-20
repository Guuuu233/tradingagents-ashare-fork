# P2-MED2：线性 FF 主干并回归验证（883bded）

## 授权

Cursor 已在 DAV-529 对完整 SHA **准予合入**：

`883bdedb64693d6f1a9923a9b515243a0677d89f`

父：`0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`。
独立审核员 DAV-530：✅。Cursor 核心 76 passed。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-med2-fetch-records-split` tip == `883bdedb64693d6f1a9923a9b515243a0677d89f`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`
4. **线性 Fast-Forward only** 到 `883bded`。禁止 merge。
5. `git ls-remote` 回读 tip 必须等于 `883bdedb64693d6f1a9923a9b515243a0677d89f`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_e2e_acceptance.py \
  tests/test_social_aggregator.py
```

报告精确数字（Cursor：76 passed）。

## 禁止

- 不准予部署；不删 legacy；不开 Gate4
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 交付

完整 40 位 tip + ls-remote + pytest + 未部署
