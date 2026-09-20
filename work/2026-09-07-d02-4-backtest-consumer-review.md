# 独立审核（只读）：D-02-4 回测收益消费者按口径取价

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `166b705b31a60e994fe6330555b6dd05563db4c7` 或其线性后代
- **关联开发卡：** [DAV-713](mention://issue/01a07b39-99e2-7de1-940b-30ded021941d)
- **Commit：** （待填）

## 白名单

允许：`api/services/backtest_service.py`、`tests/test_backtest_calibration_isolation.py` 和/或新建 `tests/test_price_basis_backtest_consumer.py`。超出即打回：改 collector/provider 实现、dividend 接线、改回测**缺省**为 raw、`report_service` 缺省、`core_stock_tools.py`、frontend、`role_bindings`。

## 复核要点

1. 缺省进出场价仍 vendor `get_stock_data`；仅显式 `price_basis="raw"` 走 cn_akshare raw。
2. raw 失败不得回退 qfq 还声称 raw。`pit_*` / 未知失败闭合。
3. `_run_single_analysis` 缺省仍 `vendor_qfq`。`tests/test_h1b_gates.py` 仍须全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
