## Cursor 准予合入

独立审核 [DAV-712](mention://issue/01a07af5-d1e5-7e5e-94ee-bf0b1dafd073) 对候选 **`166b705b31a60e994fe6330555b6dd05563db4c7`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-166b705-cursor`，HEAD=`166b705b31a60e994fe6330555b6dd05563db4c7`）：

```
tests/test_price_basis_collector_raw.py + test_price_basis_raw_provider.py + test_h1b_gates.py
# 114 passed
tests/test_data_collector.py + test_horizon_cache_isolation.py + test_price_basis_pipeline.py + test_backtest_calibration_isolation.py
# 109 passed
```

第一父 `f9be5c6f7aa139c01a87d792d74d0c3470714d88`。白名单 2 文件。缺省仍 vendor 链；显式 raw 直调 cn_akshare；cache key 分口径。未改 provider / 回测缺省 / dividend。

**准予合入** SHA `166b705b31a60e994fe6330555b6dd05563db4c7`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
