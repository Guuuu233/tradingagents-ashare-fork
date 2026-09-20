# 独立审核（只读）：D-02-2 返修循环导入

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父：** 须为 `6ee6272b00f1ce6015f4524cada07ba400eeeed0` 的线性后代，且能回到 `fbdc598ef527ea01fba7f6072f600bd557762715`
- **关联开发卡：** [DAV-709](mention://issue/01a07ad7-650e-7be6-9388-4018e373fab6)
- **Commit：** （待填）

## 白名单

仅允许：`cn_akshare_provider.py`、`tests/test_price_basis_raw_provider.py`。超出即打回：新建 `price_basis.py` 下沉、collector、backtest 缺省、`api.services` 大改、frontend。

## 复核要点

1. `cn_akshare_provider.py` 不得 `import api.services*`。
2. `tests/test_h1b_gates.py::TestH1bVerifyGatesDbPath::test_cli_subprocess_execution_with_db_path` 必须通过。
3. 缺省仍 `vendor_qfq`；显式 raw 通道保留。书面 ✅ / ⚠️ / ❌。勿 FF。
