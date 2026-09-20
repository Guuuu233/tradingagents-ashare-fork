# P2-T14：线性 FF 主干并回归验证（7db2882）

## 授权

Cursor 已在 DAV-520 对完整 SHA **准予合入**：

`7db2882d43276c22dd87e259b259dd1f500c6bd0`

父提交：`7876f1cd5c798382e03cf42210f6dc7c0d3bf565`。
独立审核员 DAV-521：✅通过。
Cursor 隔离：定向 **37** / 扩展 **153** passed；M6 spot-check OK。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t14-social-rollout-gates` tip == `7db2882d43276c22dd87e259b259dd1f500c6bd0`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`
4. **线性 Fast-Forward only** 到 `7db2882`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `7db2882d43276c22dd87e259b259dd1f500c6bd0`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_rollout_modes.py \
  tests/test_social_analyst_separation.py \
  tests/test_social_data_collector.py
```

报告精确数字（Cursor：37 passed）。

## 禁止

- **不准予部署**；不要重启生产
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要删 `legacy_proxy`；不要开 T15/Gate4 除非另卡
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
