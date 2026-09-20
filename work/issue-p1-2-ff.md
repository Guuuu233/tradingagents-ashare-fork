# P1-2：线性 FF 主干并回归验证（7e36d6c）

## 授权

Cursor 已在 DAV-487 对完整 SHA **准予合入**：

`7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`

父提交：`aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p1-2-capitulation-reversal` tip == `7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`
4. **线性 Fast-Forward only**：把主干推到 `7e36d6c`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_capitulation_reversal.py \
  tests/test_confirmation_gate.py \
  tests/test_decision_status.py \
  tests/test_prompt_depersonification.py \
  tests/test_news_event_coverage.py \
  tests/test_claim_cluster.py \
  tests/test_fund_flow_evidence.py \
  -q --tb=short
```

报告精确数字。

## 禁止

- **不准予部署**；不要重启生产；运行时保持宿主 `4fa7681`
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要唤醒 DAV-460
- 不要宣称歌尔/蓝思案例已修
- 不要 @项目调度助手催工（交付评论写完即可）

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
