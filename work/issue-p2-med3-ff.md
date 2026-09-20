# P2-MED3：线性 FF 主干并回归验证（0cda99b）

## 授权

Cursor 已在 DAV-532 对 tip **准予合入**：

`0cda99b6072116874b7a458432d0c7bc7b0a29e3`

（含 R1/R2/R3；基线 `883bded…`）。
独立审核员 DAV-533：✅。Cursor：101 + 19 passed。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-med3-integrity-residuals` tip == `0cda99b6072116874b7a458432d0c7bc7b0a29e3`
3. 确认主干当前仍是 `883bdedb64693d6f1a9923a9b515243a0677d89f`
4. **线性 Fast-Forward only** 到 `0cda99b`。禁止 merge。
5. `git ls-remote` tip 必须等于 `0cda99b6072116874b7a458432d0c7bc7b0a29e3`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_mediacrawler_importer.py \
  tests/test_run_social_ingestion_guards.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_e2e_acceptance.py \
  tests/test_social_aggregator.py
```

报告精确数字（Cursor：101 passed）。

## 禁止

不准予部署；不删 legacy；不开 Gate4；勿动脏文件。

## 交付

完整 40 位 tip + ls-remote + pytest + 未部署
