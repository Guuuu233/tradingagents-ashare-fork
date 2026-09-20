## Cursor 准予合入

独立审核 [DAV-710](mention://issue/01a07ad8-1361-7b43-812e-94e1d889e5f5) 对候选 **`f9be5c6f7aa139c01a87d792d74d0c3470714d88`** 给出 ✅通过。

Cursor 隔离复测（`/tmp/ta-iso-f9be5c6-cursor`，HEAD=`f9be5c6f7aa139c01a87d792d74d0c3470714d88`）：

```
tests/test_price_basis_raw_provider.py + test_tushare_raw_daily_contract.py + test_price_basis_pipeline.py + test_h1b_gates.py + test_shadow_credit.py
# 170 passed
```

`cn_akshare_provider.py` 中无 `api.services`。第一父 `6ee6272b00f1ce6015f4524cada07ba400eeeed0`，再上为主干 `fbdc598ef527ea01fba7f6072f600bd557762715`。相对主干白名单 2 文件。缺省仍 `vendor_qfq`。未接 collector。

**准予合入** SHA `f9be5c6f7aa139c01a87d792d74d0c3470714d88`。线性 FF `origin/codex/dav-4-p2a-trunk`（含 D-02-2 功能提交及其返修）。不准予部署。
