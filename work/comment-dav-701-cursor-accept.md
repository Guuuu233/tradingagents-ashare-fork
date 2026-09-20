## Cursor 准予合入

独立审核 [DAV-702](mention://issue/01a07a11-ed78-7c4c-8387-1bda54f9b99a) 对候选 **`309bdbcb9a9655a9fc29828484a66cefd5a336d9`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-309bdbc-cursor`，HEAD=`309bdbcb9a9655a9fc29828484a66cefd5a336d9`）：

```
tests/test_dual_horizon_e2e.py + test_dual_horizon_bugs.py + test_report_dual_horizon.py + test_dav37_stage16_regressions.py + test_trading_graph_multi_horizon.py
# 80 passed in 4.03s
```

原先 3 个失败用例及单档 structured 覆盖 / 两档都 True 聚合均为绿。第一父 `5fbd70ee2d09637bce8aa64e441a75e003897d32`。白名单 2 文件。未改 frontend / 分析师 / collector。

**准予合入** SHA `309bdbcb9a9655a9fc29828484a66cefd5a336d9`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
