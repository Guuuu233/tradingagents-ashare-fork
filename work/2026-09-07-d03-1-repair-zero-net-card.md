# D-03-1 返修：evidence 字典净额 0 不得被 or 吃掉

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** 从 `7f17815f8509c6e69b6bac4acc0b6352b99d73ba`（`origin/agent/1/adb808aed5ff`）开隔离分支，第一父必须是该 SHA。不要 FF 主干。脏宿主禁止当工作树。  
**一个关注点：** 修独立审核 MED：`calculate_fund_flow_scale_metrics` 把 mapping 当首参时，用 `or` 解包 `net_amount`，`0` / `Decimal("0")` 被当成缺失；若另有非零 `selected_value` 会覆盖真实 0 净额。  
**禁止：** FF/部署；改 collector；新增 fetcher；frontend；`role_bindings`；H1b 补样本；打印 token。本卡评论禁止 @独立代码审核员。

Cursor 隔离已复现（SHA `7f17815f8509c6e69b6bac4acc0b6352b99d73ba`）：

```python
calculate_fund_flow_scale_metrics({"ts_code":"600519.SH","trade_date":"2026-08-14","net_amount":0, ...})
# gaps 含「资金净额 (net_amount) 缺失，拒算」

calculate_fund_flow_scale_metrics({"net_amount": Decimal("0"), "selected_value": 50, ...})
# net_amount 变成 50
```

关键字参数 `net_amount=0` 目前正确。现有 11 个测试没覆盖 mapping 路径的 0 净额。

## 允许改

- `tradingagents/dataflows/fund_flow_evidence.py`
- `tests/test_fund_flow_scale_metrics.py`（必须加能在修复前失败的用例）

## 必须

1. mapping / kwargs 解包一律 `is not None`，禁止用 `or` 取净额。
2. 新增测试：`{"net_amount": 0}` 与 `Decimal("0")` 算出 `net_to_circ_mv == 0`；`net_amount=0` 且 `selected_value=50` 仍用 0，不得变成 50。
3. 建议一并修审核 LOW（非法分母日期应拒算；`DecimalRatio.__hash__`）。不要顺手接线 collector。
4. 一个 commit。pytest：

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q tests/test_fund_flow_scale_metrics.py tests/test_tushare_daily_basic.py tests/test_h1b_gates.py
```
