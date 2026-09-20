# P2-T12：线性 FF 主干并回归验证（c0bfac5）

## 授权

Cursor 已在 DAV-514 对完整 SHA **准予合入**：

`c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`

父提交：`0d21d1950350d42f65a7e3cb42040c05552eb3e0`（含 H1）。
独立审核员 DAV-508：✅通过。
Cursor 隔离复测：核心 **50 passed**；扩展 social/report **165 passed**；质量闸 High 返修 spot-check 通过。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t12-social-report-gates` tip == `c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `0d21d1950350d42f65a7e3cb42040c05552eb3e0`
4. **线性 Fast-Forward only** 到 `c0bfac5`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `c0bfac5a655d228ae3d24e7954eefb8fdcbbb79e`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_downstream_gates.py \
  tests/test_social_data_api.py \
  tests/test_report_data_gaps.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_data_collector_social_integration.py \
  tests/test_report_social_context.py \
  tests/test_report_quality_gate.py \
  tests/test_social_aggregator.py \
  tests/test_social_analyst_separation.py \
  tests/test_social_api_main_wiring.py \
  tests/test_social_archive_provider.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_social_toolnode_no_news.py
```

报告精确数字（Cursor 准予合入前：核心 50 / 扩展 165 passed）。

## 禁止

- **不准予部署**；不要重启生产
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要删 `legacy_proxy`；不要开 T13+
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
