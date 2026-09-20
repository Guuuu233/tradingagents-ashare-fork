## Cursor 不准予合入

独立审核 [DAV-718](mention://issue/01a07bcc-dff2-71f1-8758-dd35a34bc85f) 对 `7f17815f8509c6e69b6bac4acc0b6352b99d73ba` 给出 ⚠️有条件通过。Cursor 隔离复现 MED：

- mapping 首参 `net_amount=0` → 缺口「资金净额缺失」
- mapping `net_amount=Decimal(0)` + `selected_value=50` → 净额被覆盖为 50
- 关键字 `net_amount=0` 正常

`tests/test_fund_flow_scale_metrics.py` 现有用例未覆盖该路径。核心套件仍可绿，**不能**据此合入。

**不准予合入** SHA `7f17815f8509c6e69b6bac4acc0b6352b99d73ba`。开返修卡，禁止 FF。
