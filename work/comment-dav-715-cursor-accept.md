## Cursor 准予合入

独立审核 [DAV-716](mention://issue/01a07b8a-1401-7171-8b13-0cc4cfeb77eb) 对候选 **`30e17a46688b3c8f392ad265d96e858ebfa52886`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-30e17a4-cursor`，HEAD=`30e17a46688b3c8f392ad265d96e858ebfa52886`）：

```
tests/test_price_basis_dividend_collateral.py + test_tushare_dividend_contract.py + test_price_basis_collector_raw.py + test_h1b_gates.py
# 130 passed
tests/test_price_basis_pipeline.py + test_price_basis_raw_provider.py + test_price_basis_backtest_consumer.py + test_data_collector.py + test_data_collector_social_integration.py
# 143 passed
```

第一父 `d4103af21c6ba19483716a74594b90af361f90a2`。白名单 2 文件。经 registry 调已有 `_fetch_tushare_dividend(as_of=trade_date)`；空表显式「不得据此判断无分红」；失败分类上报；未改日线/`price_basis`；未改 provider 分红读取。残留 LOW：非 3 元组返回被写成空表语义（主要为既有 MagicMock 兼容），生产路径仍是 3 元组。

**准予合入** SHA `30e17a46688b3c8f392ad265d96e858ebfa52886`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
