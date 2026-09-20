# 独立审核（只读）：D-02-5 collector 分红旁证

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `d4103af21c6ba19483716a74594b90af361f90a2` 或其线性后代
- **关联开发卡：** [DAV-715](mention://issue/01a07b89-b6fa-74cd-9118-979b23524402)
- **Commit：** （待填）

## 白名单

允许：`tradingagents/graph/data_collector.py`、新建 `tests/test_price_basis_dividend_collateral.py`。超出即打回：改 dividend 读取实现、用分红改日线、宣称 PIT、改 backtest 缺省、frontend、`role_bindings`。

## 复核要点

1. 经 registry 调已有 `_fetch_tushare_dividend(as_of=trade_date)`；空表≠无分红。
2. 注入已清洗压缩；失败显式上报。日线 `price_basis` 与收盘价不得被本卡改写。
3. `tests/test_h1b_gates.py` 与 `tests/test_tushare_dividend_contract.py` 仍须全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
