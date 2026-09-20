## Cursor 准予合入

独立审核 [DAV-714](mention://issue/01a07b39-edf4-7a26-9556-98c753ba1a50) 对候选 **`d4103af21c6ba19483716a74594b90af361f90a2`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-d4103af-cursor`，HEAD=`d4103af21c6ba19483716a74594b90af361f90a2`）：

```
tests/test_price_basis_backtest_consumer.py + test_backtest_calibration_isolation.py + test_price_basis_pipeline.py + test_h1b_gates.py
# 144 passed
tests/test_price_basis_collector_raw.py + test_price_basis_raw_provider.py + test_calibration_service.py + test_backtest_security.py
# 124 passed
```

第一父 `166b705b31a60e994fe6330555b6dd05563db4c7`。白名单 3 文件。缺省进出场价仍 vendor qfq；显式 raw 直调 cn_akshare；失败/`pit_*`/`unspecified` 失败闭合，不回退 qfq。未改 collector/provider/dividend。`_run_single_analysis` 缺省仍 `vendor_qfq`。

**准予合入** SHA `d4103af21c6ba19483716a74594b90af361f90a2`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
