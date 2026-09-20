## Cursor 准予合入

独立审核 [DAV-704](mention://issue/01a07a61-6943-7e13-8b5b-9a37ff16cfee) 对候选 **`12455e9b26d433c35a17b20097b0cc3c61a92edb`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-12455e9-cursor`，HEAD=`12455e9b26d433c35a17b20097b0cc3c61a92edb`）：

```
定向 3 文件：63 passed
tests/test_tushare_*.py + tests/test_fund_flow_*.py：143 passed
```

第一父 `309bdbcb9a9655a9fc29828484a66cefd5a336d9`。白名单 3 文件。未改 collector / 回测 / report_service。未接 `adj_factor`。空 dividend 为 `no_rows`。测试 mock HTTP；`api.tushare.pro` 仅出现在既有缺省 URL 断言，无真实打网、无 token。

**准予合入** SHA `12455e9b26d433c35a17b20097b0cc3c61a92edb`。线性 FF `origin/codex/dav-4-p2a-trunk`。不准予部署。
