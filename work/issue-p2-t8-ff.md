# P2-T8：线性 FF 主干并回归验证（a375bdc）

## 授权

Cursor 已在 DAV-499 对完整 SHA **准予合入**：

`a375bdc9cf3d07584eb6c28c637bde9bca867876`

父提交：`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t8-data-collector-social` tip == `a375bdc9cf3d07584eb6c28c637bde9bca867876`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`
4. **线性 Fast-Forward only**：把主干推到 `a375bdc`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `a375bdc9cf3d07584eb6c28c637bde9bca867876`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_data_collector_social_integration.py \
  tests/test_data_collector.py \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py
```

报告精确数字。

## 禁止

- **不准予部署**；不要重启生产
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要开 Task 9；不要删 legacy_proxy
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
