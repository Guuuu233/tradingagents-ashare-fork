# 独立审核（只读）：D-02-2 provider 可选 raw 日线通道

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `fbdc598ef527ea01fba7f6072f600bd557762715` 或其线性后代
- **关联开发卡：** [DAV-707](mention://issue/01a07ab1-9ca5-7049-8fdd-746e322c2c11)
- **Commit：** （待填）

## 白名单

允许：`tradingagents/dataflows/providers/cn_akshare_provider.py`、`tests/test_price_basis_raw_provider.py`。超出即打回：collector、backtest 缺省改 raw、dividend 旁证接线、PIT 引擎、frontend、`role_bindings`、真实打网关。

## 复核要点

1. 缺省仍 qfq/`vendor_qfq`；仅显式 `price_basis=raw` 走 `_fetch_tushare_raw_daily`。
2. raw 失败不得回退 qfq 还声称 raw。未知/`pit_raw` 本卡不得假装可用。
3. 测试 mock HTTP。定向 pytest 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
