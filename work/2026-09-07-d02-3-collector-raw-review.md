# 独立审核（只读）：D-02-3 collector 显式 raw 接线

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `f9be5c6f7aa139c01a87d792d74d0c3470714d88` 或其线性后代
- **关联开发卡：** [DAV-711](mention://issue/01a07af5-83ca-7625-b5e2-ca72a01d359b)
- **Commit：** （待填）

## 白名单

允许：`tradingagents/graph/data_collector.py`、`tests/test_price_basis_collector_raw.py`。超出即打回：改 provider 实现、dividend 接线、backtest 缺省、`core_stock_tools.py`、frontend、`role_bindings`。

## 复核要点

1. 缺省仍走现有 `get_stock_data` vendor 链；仅显式 raw 直调 `cn_akshare.get_stock_data(..., price_basis="raw")`。
2. raw 失败不得回退 qfq 还声称 raw。cache key 不得把 raw 与 qfq 混成同一份。
3. `tests/test_h1b_gates.py` 仍须全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
