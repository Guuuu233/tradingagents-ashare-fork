# P2-MED：线性 FF 主干并回归验证（0d7a67e）

## 授权

Cursor 已在 DAV-526 对 tip **准予合入**：

`0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`

（含 M2–M4/M7 四独立 commit；基线 `c1ec33e…`）。
独立审核员 DAV-527：✅通过。
Cursor 隔离：89 + 23 passed。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-med-social-residuals` tip == `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`
4. **线性 Fast-Forward only** 到 `0d7a67e`。禁止 merge。禁止 FF 其它 tip。
5. `git ls-remote` 回读主干 tip 必须等于 `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_mediacrawler_importer.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_e2e_acceptance.py
```

报告精确数字（Cursor：89 passed）。

## 禁止

- **不准予部署**
- 不要删 `legacy_proxy` / 开 Gate4
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip + `git ls-remote` + pytest 数字 + 未部署
