# 独立审核（只读）：D-03-1 资金规模归一纯计算

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `30e17a46688b3c8f392ad265d96e858ebfa52886` 或其线性后代
- **关联开发卡：** [DAV-717](mention://issue/01a07bcc-ad71-7eea-a278-b34e4aa7ebed)
- **Commit：** （待填）

## 白名单

允许：`tradingagents/dataflows/fund_flow_evidence.py`、新建 `tests/test_fund_flow_scale_metrics.py`。超出即打回：改 collector、新增 fetcher、改 provider、改分析师、frontend、`role_bindings`。

## 复核要点

1. 纯计算；同单位可复算；错单位/跨日/跨证券/零负分母失败闭合。
2. `circ_mv` 官方单位万元；`amount` 不得默认单位。禁止跨股排名。禁止把一致度当影响大小。
3. `tests/test_tushare_daily_basic.py` 与 `tests/test_h1b_gates.py` 仍须全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
