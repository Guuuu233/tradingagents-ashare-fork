# P2 回顾 H1：线性 FF 主干并回归验证（0d21d19）

## 授权

Cursor 已在 DAV-511 对完整 SHA **准予合入**：

`0d21d1950350d42f65a7e3cb42040c05552eb3e0`

父提交：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`。
独立审核员 DAV-512：✅通过。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-retro-h1-empty-window` tip == `0d21d1950350d42f65a7e3cb42040c05552eb3e0`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
4. **线性 Fast-Forward only** 到 `0d21d19`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `0d21d1950350d42f65a7e3cb42040c05552eb3e0`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_*.py \
  tests/test_data_collector_social_integration.py \
  tests/test_report_social_context.py
```

报告精确数字（Cursor：定向 43 / 扩跑 128 passed）。

## 禁止

- **不准予部署**；不要重启生产
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要推进 T12 FF；不要删 legacy
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
