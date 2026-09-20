## Cursor 打回

独立审核 [DAV-708](mention://issue/01a07ab2-8b69-7c53-86e7-54667225e076) 对候选 **`6ee6272b00f1ce6015f4524cada07ba400eeeed0`** 给出 ❌打回。

Cursor 隔离复测同 SHA（`/tmp/ta-iso-6ee6272-cursor`）：

```
tests/test_price_basis_raw_provider.py + test_tushare_raw_daily_contract.py + test_price_basis_pipeline.py + test_shadow_credit.py + test_h1b_gates.py
# 1 failed, 169 passed
```

失败：`tests/test_h1b_gates.py::TestH1bVerifyGatesDbPath::test_cli_subprocess_execution_with_db_path`（`ImportError: route_to_vendor` / circular import）。根因：`cn_akshare_provider.py` 顶层 `from api.services.price_basis_labels import ...`，底层 provider 逆向依赖 `api.services`。

原卡允许该 import 是错误选项。本 SHA **不准予合入**。返修另开，禁止带病 FF。不准予部署。
