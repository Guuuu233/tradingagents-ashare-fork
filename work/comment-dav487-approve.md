Cursor 独立复审 DAV-487 / P1-2。

候选 SHA（完整 40 位）：`7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`
父提交：`aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`（线性，无 merge）
分支：`agent/dev2/p1-2-capitulation-reversal`

隔离 worktree `/tmp/ta-p12-7e36d6c` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_capitulation_reversal.py \
  tests/test_confirmation_gate.py \
  tests/test_decision_status.py \
  tests/test_prompt_depersonification.py \
  -q --tb=short
```

结果：**36 passed in 0.40s**。不采信口头；本次为 Cursor 独立复跑。

相对主干 6 文件、+657/−8。白名单内。未改社交、新闻、回测、受保护脏文件。

契约核对（含独立探针）：
- cutoff 截断后 follow-through 不可把状态抬成 `reversal_confirmed`（防 T+1）
- `capitulation_candidate` 未确认 → `WAIT` + `VALID`（非永久 NO_TRADE）
- `reversal_confirmed` 允许 BUY，但 `position_pct` 封顶 10%
- Trader 复用既有 WAIT 短路；P0-5a 去人格化测仍绿

残留（不挡合入）：`research_manager` 对 `resolve_staged_entry_position` 使用了函数内 import（顶部已有 `decision_status` 导入，后续可上移）；`MAX_REVERSAL_STAGED_POSITION_PCT` 在 data_collector / decision_status 各有一份。

**准予合入** SHA `7e36d6c9ddd8022c8646284cb13ad8e75eb0e6df`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。** 不宣称歌尔/蓝思案例已修。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
