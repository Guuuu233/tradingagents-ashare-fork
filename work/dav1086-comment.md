**前置口径确认完毕（Tushare 官方文档已核）**

| 源 | 字段 | 官方语义 |
|---|---|---|
| moneyflow_dc | net_amount | 今日主力净流入额（=超大单+大单） |
| moneyflow_dc | buy_elg_amount / buy_lg_amount | 超大单 / 大单净流入额 |
| moneyflow_ths | net_amount | 资金净流入（总资金，非主力口径） |
| moneyflow_ths | buy_lg_amount | 今日大单净流入额（无超大单分量） |

**结论**：东财 `net_amount`(主力) ⊃ 同花顺 `buy_lg_amount`(大单)，语义层级不同却同装 `r0_net` → 30.76% 假离散 → ABSTAIN 误杀，实锤。同花顺无"主力"分量可与东财 `net_amount` 对齐。

**选定方案：B** —— 同花顺 `buy_lg_amount` 改独立字段（不并入 `r0_net`），`r0_net` 仅承载真主力口径；东财 `buy_lg_amount` 与同花顺 `buy_lg_amount` 可在"大单对大单"层做同义共识。语义不可比时 `semantic_incomparable` fail-closed。不放宽 20% 阈值、不取平均。

另注意 `_field_semantics_are_valid` 对 `r0_net` 现接受 `"主力" or "大单"`，需同步收紧，否则闸门仍把大单当主力放行。

开绿灯施工：资深开发1 请按 issue body 的修复要求 + 方案 B 实施，TDD 先红后绿，完评报 40 字符 SHA。 [@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3) [@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
