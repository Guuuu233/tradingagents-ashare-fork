## Cursor 准予合入

独立审核 [DAV-670](mention://issue/01a0727f-0e69-7afb-93e5-f1727b1fff33) 对候选 **`872d94e9477809a236b09139afe145b3343e3856`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-872d94e`，HEAD=`872d94e9477809a236b09139afe145b3343e3856`）：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_horizon_profile_contract.py tests/test_report_dual_horizon.py tests/test_dual_horizon_bugs.py tests/test_intent_parser.py -q
```

候选 `6 failed, 30 passed`；同一命令在 `c838818` 上 `6 failed, 16 passed`，失败用例名称一致，非本卡回归。白名单 4 文件；query/chat 不扩档；未改校准 hold_days。

**准予合入** SHA `872d94e9477809a236b09139afe145b3343e3856`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
