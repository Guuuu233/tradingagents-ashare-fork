# 独立审核（只读）：D-03-1 返修 净额 0 解包

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c`，只在本卡 @。

## 候选

- **审核/合入 tip：** （待填 40 位）
- **第一父：** 必须是 `7f17815f8509c6e69b6bac4acc0b6352b99d73ba` 或其线性后代（且仍为 `30e17a46688b3c8f392ad265d96e858ebfa52886` 的后代）
- **关联开发卡：** [DAV-719](mention://issue/01a07c54-ce1c-7761-8b78-4a519703c441)

## 白名单

允许：`tradingagents/dataflows/fund_flow_evidence.py`、`tests/test_fund_flow_scale_metrics.py`。接线 collector 即打回。

## 复核要点

1. mapping 路径 `net_amount=0` / `Decimal("0")` 不得报缺失，不得被 `selected_value` 覆盖。
2. 必须有修复前会失败的测试。`tests/test_h1b_gates.py` 全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
