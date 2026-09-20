## Cursor 准予合入

独立审核 [DAV-688](mention://issue/01a07776-97ff-7e68-84aa-026453c6e6a2) 对候选 **`904dc0eccc542baf14c682c8b9136c0142d33773`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-904dc0e-cursor`，HEAD=`904dc0eccc542baf14c682c8b9136c0142d33773`）：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_news_analyst.py tests/test_horizon_analyst_context.py
# 15 passed in 0.59s
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_market_analyst.py tests/test_analyst_prompts_deep_reasoning.py tests/test_trading_graph_multi_horizon.py
# 58 passed in 41.66s
```

第一父 `645cbb5a07c6a1605009fbbd435473b5fd7a1576`。白名单 2 文件（`news_analyst.py`、`test_news_analyst.py`）。未改 collector / 其它分析师。14 天观察窗保留；中期任务 trace 研究档为 medium。

**准予合入** SHA `904dc0eccc542baf14c682c8b9136c0142d33773`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
