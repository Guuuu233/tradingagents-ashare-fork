# P2-T11：线性 FF 主干并回归验证（68ae241）

## 授权

Cursor 已在 DAV-505 对完整 SHA **准予合入**：

`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`

父提交：`0cc34278c8024680e0b687bd029295876b6e0c98`。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t11-social-analyst-separation` tip == `68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `0cc34278c8024680e0b687bd029295876b6e0c98`
4. **线性 Fast-Forward only**：把主干推到 `68ae241`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `68ae241bdf9c148654f551fb67b7e5f2ec56dba4`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_analyst_separation.py \
  tests/test_analyst_prompts_deep_reasoning.py \
  tests/test_social_toolnode_no_news.py \
  tests/test_report_social_context.py \
  tests/test_social_api_main_wiring.py \
  tests/test_trading_graph_multi_horizon.py \
  tests/test_data_collector_social_integration.py \
  tests/test_social_data_collector.py \
  tests/test_social_contracts.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py
```

报告精确数字（Cursor 准予合入前 brief：**63 passed**）。

## 禁止

- **不准予部署**；不要重启生产 / 不要把运行时切到本 SHA
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要开 Task 12；不要删 legacy_proxy；不要改辩论轮次 / 加权
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
