# 独立审核（只读）：D-01 Tushare raw daily + dividend 读取契约

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `309bdbcb9a9655a9fc29828484a66cefd5a336d9` 或其线性后代
- **关联开发卡：** [DAV-703](mention://issue/01a07a61-6402-77bc-8265-af73508c0cf0)
- **Commit：** （待填）

## 白名单

仅允许：`cn_akshare_provider.py`、`tests/test_tushare_raw_daily_contract.py`、`tests/test_tushare_dividend_contract.py`。超出即打回：collector、backtest、report_service、`adj_factor` 接入、frontend、`role_bindings`、真实打 `api.tushare.pro`。

## 复核要点

1. 按列名取数；空 dividend ≠ 无分红；`as_of` 前视拒绝；无 token 不发网。
2. 未接业务流、未实现复权引擎、未接 C-09-3。
3. 测试 mock HTTP，含正常 / 失败 / 结构异常。日志/diff 无 token。
4. 定向 pytest 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
