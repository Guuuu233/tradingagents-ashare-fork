## Cursor 准予合入

独立审核 [DAV-684](mention://issue/01a076f3-66e0-75c8-981f-a16a978cb1b0) 对候选 **`f03466c6a9d7525671749e856c0cfc03bc2a0190`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-f03466c-cursor`，HEAD=`f03466c6a9d7525671749e856c0cfc03bc2a0190`）：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_horizon_analyst_context.py tests/test_intent_parser.py
# 14 passed in 0.55s
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_trading_graph_multi_horizon.py tests/test_horizon_entrypoints.py
# 29 passed in 0.90s
```

第一父 `8325943e23e67b2b8f81438d03aa297d9f7eccfe`。白名单 5 文件。未改分析师 / collector / research_manager。研究档与专业观察窗分两行；未绑定不冒充。

**准予合入** SHA `f03466c6a9d7525671749e856c0cfc03bc2a0190`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
