Cursor 独立复审 DAV-489 / P1-3。

候选 SHA（完整 40 位）：`6a799d460318acd9865584e80d9ad07b8e71df25`
父提交：`7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`（线性，无 merge）
分支：`agent/dev2/p1-3-backtest-calibration-isolation`

隔离 worktree `/tmp/ta-p13-6a799d4` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_backtest_calibration_isolation.py \
  tests/test_calibration_service.py \
  tests/test_backtest_security.py \
  -q --tb=short
```

结果：**67 passed**（57 warnings 仅为 JWT 测钥长度，与本卡无关）。不采信口头；本次为 Cursor 独立复跑。

相对主干 3 文件、+647/−35。白名单内。未改社交、VPA、新闻、受保护脏文件。

契约核对（含独立探针）：
- 短行情序列 → `_get_price_after` 返回 None，**不**缩短 hold_days
- `INVALID_RUN`+BUY 文本 → 不进 win_rate；`excluded_invalid`
- `WAIT` → 不坍成 HOLD 胜率样本
- `VALID`+BUY/SELL + 完整窗口 → 可计 return
- 校准暴露 `excluded_incomplete_outcome`；不足样本指标为 None

残留（不挡合入）：缺 `analysis_status` 时若正文含 BUY/SELL 会默认 `VALID`（应尽量依赖结构化字段；后续可收紧为未知即排除）。

**准予合入** SHA `6a799d460318acd9865584e80d9ad07b8e71df25`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
