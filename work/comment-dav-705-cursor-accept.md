## Cursor 准予合入

独立审核 [DAV-706](mention://issue/01a07a87-fda6-7040-90bd-2f5fa3c4a04a) 对候选 **`fbdc598ef527ea01fba7f6072f600bd557762715`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-fbdc598-cursor`，HEAD=`fbdc598ef527ea01fba7f6072f600bd557762715`）：

```
tests/test_price_basis_pipeline.py + test_backtest_calibration_isolation.py + test_cohort_metadata_persistence.py
# 84 passed
tests/test_shadow_credit.py + tests/test_h1b_gates.py
# 66 passed
```

第一父 `12455e9b26d433c35a17b20097b0cc3c61a92edb`。白名单 5 文件。映射失败闭合；`pit_raw` ≠ `pit_adjusted`。回测缺省仍 `vendor_qfq`；报告缺省仍 `price_basis.unspecified`。未接 collector / Tushare 业务流。

**准予合入** SHA `fbdc598ef527ea01fba7f6072f600bd557762715`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
