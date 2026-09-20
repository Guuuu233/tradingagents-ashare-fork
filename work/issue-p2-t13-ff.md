# P2-T13：线性 FF 主干并回归验证（7876f1c）

## 授权

Cursor 已在 DAV-516 对完整 SHA **准予合入**：

`7876f1cd5c798382e03cf42210f6dc7c0d3bf565`

父提交：`c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`。
独立审核员 DAV-517：✅通过。
Cursor 隔离：新增 14 / 扩展 168 passed。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t13-social-ingestion-ops` tip == `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`
4. **线性 Fast-Forward only** 到 `7876f1c`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_import_mediacrawler_social_cli.py \
  tests/test_run_social_ingestion_guards.py \
  tests/test_social_contracts.py \
  tests/test_mediacrawler_importer.py \
  tests/test_social_archive_provider.py \
  tests/test_social_entity_resolver.py \
  tests/test_social_aggregator.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_data_collector_social_integration.py \
  tests/test_social_analyst_separation.py \
  tests/test_social_toolnode_no_news.py \
  tests/test_report_social_context.py \
  tests/test_social_api_main_wiring.py \
  tests/test_social_data_api.py \
  tests/test_social_downstream_gates.py
```

报告精确数字（Cursor：14 / 168 passed）。

## 禁止

- **不准予部署**；不要重启生产
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要删 `legacy_proxy`；不要开 T14+ 除非另卡
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
