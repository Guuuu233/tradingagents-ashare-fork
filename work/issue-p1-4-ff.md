# P1-4：线性 FF 主干并回归验证（ca4747c）

## 授权

Cursor 已在 DAV-491 对完整 SHA **准予合入**：

`ca4747c8653afe1de83f410b666537e511a26b0f`

父提交：`6a799d460318acd9865584e80d9ad07b8e71df25`。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p1-4-provider-red-lights` tip == `ca4747c8653afe1de83f410b666537e511a26b0f`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `6a799d460318acd9865584e80d9ad07b8e71df25`
4. **线性 Fast-Forward only**：把主干推到 `ca4747c`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `ca4747c8653afe1de83f410b666537e511a26b0f`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_as_of.py \
  tests/test_financial_announce_cutoff.py \
  tests/test_backtest_calibration_isolation.py \
  tests/test_decision_status.py \
  -q --tb=short
```

报告精确数字（含 deselected 若有）。

## 禁止

- **不准予部署**；不要重启生产；运行时保持宿主 `4fa7681`
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要唤醒 DAV-460
- 不要默认跑 `-m network` 实网集
- 不要 @项目调度助手催工（交付评论写完即可）

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
