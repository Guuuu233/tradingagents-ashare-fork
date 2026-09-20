# 独立审核（只读）：D-03-2 collector 接入规模归一

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c`，只在本卡 @。

## 候选

- **审核/合入 tip：** （待填 40 位）
- **第一父 / 基线：** 必须是 `98ba99f474723d0f98a323721918cc0fb5cb8d57` 或其线性后代
- **关联开发卡：** [DAV-721](mention://issue/01a07c95-c0b5-7f18-bbcd-7bad5e14243b)

## 白名单

允许：`tradingagents/graph/data_collector.py`、新建 `tests/test_fund_flow_scale_collector.py`。改分析师 / 改纯计算语义 / 新增 fetcher / 默认 `amount` 单位即打回。

## 复核要点

1. `circ_mv` 万元；`amount` 无单位则成交额占比缺失。净额 0 不得当缺失。
2. `tests/test_fund_flow_scale_metrics.py` 与 `tests/test_h1b_gates.py` 全绿。书面 ✅ / ⚠️ / ❌。勿 FF。
