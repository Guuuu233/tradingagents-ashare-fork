## Cursor 准予合入

独立审核 [DAV-696](mention://issue/01a079a6-6555-786e-a7fd-3e82f2b9d3f7) 对候选 **`a822a866c3301cc3acd474d073b3ef823d5f6916`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-a822a86-cursor`，HEAD=`a822a866c3301cc3acd474d073b3ef823d5f6916`）：

```
tests/test_smart_money_analyst.py + test_horizon_analyst_context.py + test_smart_money_fund_flow_semantics.py
# 21 passed in 0.67s
smart_money + market + news + social + fundamentals + macro + deep_reasoning + multi_horizon
# 92 passed in 29.94s
```

第一父 `d4f8bef0d0339128df53ef9286d4e38faa57b6e6`。白名单 2 文件。未改 collector / 其它分析师 / 资金流语义。观察窗 short，`data_window` 仍为「近期可用」。

**准予合入** SHA `a822a866c3301cc3acd474d073b3ef823d5f6916`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
