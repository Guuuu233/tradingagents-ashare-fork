## Cursor 准予合入

独立审核 [DAV-694](mention://issue/01a07804-7a1c-73fa-ac9a-d650de9cc64b) 对候选 **`d4f8bef0d0339128df53ef9286d4e38faa57b6e6`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-d4f8bef-cursor`，HEAD=`d4f8bef0d0339128df53ef9286d4e38faa57b6e6`）：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_macro_analyst.py tests/test_horizon_analyst_context.py
# 16 passed in 0.48s
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_macro_analyst.py tests/test_fundamentals_analyst.py tests/test_market_analyst.py tests/test_news_analyst.py tests/test_social_media_analyst.py tests/test_analyst_prompts_deep_reasoning.py tests/test_trading_graph_multi_horizon.py
# 84 passed in 33.20s
```

第一父 `3899fe82b6b698c44608ee38010094ddca2e0c1b`。白名单 2 文件。未改 collector / 其它分析师。观察窗 medium，`data_window` 仍为「板块数据」；短期任务 trace 研究档为 short。

**准予合入** SHA `d4f8bef0d0339128df53ef9286d4e38faa57b6e6`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
