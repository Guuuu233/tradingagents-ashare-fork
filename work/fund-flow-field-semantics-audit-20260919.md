# 资金流字段口径审计：`c21456dd` 30.76% 离散根因（2026-09-19）

> 只读审计。证据取自生产库 `c21456dd.result_data.market_data_context.fund_flow_evidence.records`，生产库零写入。

## 结论：口径错配，不是真实分歧

30.76% 离散来自把**两个不同语义的上游字段**同时归一成 `r0_net` 后做同字段共识比较。当前 `fund_flow_consensus_guard` 拦截在行为上正确（确实不该放行），但**比较的两个数本来就不该放在同一字段下比**——属于字段映射层面的语义错配。

## 证据：三条记录的 `upstream_field` 对照

| 记录 | source | `r0_net` 取值 | `upstream_field` | `upstream_field_semantics` |
|---|---|---|---|---|
| [0] | `tushare_eastmoney_moneyflow_dc` | **0.40185** | `net_amount` | **今日主力净流入额**（万元） |
| [1] | `tushare_ths_moneyflow_ths` | netamount=0.115549 | `net_amount` | **资金净流入**（万元，总净额） |
| [2] | `tushare_ths_moneyflow_ths` | **0.758931** | `buy_lg_amount` | **大单净额 / 平台主力口径参考**（万元） |

离散比较取的是 `[0].r0_net=0.40185`（东财 `net_amount`）对 `[2].r0_net=0.758931`（同花顺 `buy_lg_amount`）。

## 上游字段语义本不同

`cn_akshare_provider.py` 内部注释已写明（行 2752-2754）：
- `moneyflow_dc.net_amount` = 今日**主力**净流入额
- `moneyflow_ths.net_amount` = **资金净流入**（总净额，非主力口径）
- `moneyflow_ths.buy_lg_amount` = **大单**净额 / 平台主力口径**参考**

东财 `net_amount`（主力净流入）与同花顺 `buy_lg_amount`（大单净额）在 Tushare 上游定义里**本来就不是同一口径**：主力 vs 大单的统计边界不同。`c21456dd` 里东财记录还带有 `buy_elg_amount`（超大单）字段——东财「主力」通常 = 超大单+大单，而同花顺 `buy_lg_amount` 只是大单，缺了超大单分量。这正是 0.758931 vs 0.40185 差异的主因，不是单位错误（两边都是万元→亿元，换算正确）。

## 判定

- **不是单位错误**：两边 `raw_unit=万元`、`unit=亿元` 一致，换算 `÷10000` 正确。
- **不是日期错配**：三条记录 `trade_date` 同为 2026-09-18。
- **是口径错配**：`r0_net` 字段对东财装的是「主力净流入」，对同花顺装的是「大单净额」——同名不同义，离散 30.76% 是口径差异的必然结果。

## 影响评估

1. `fund_flow_consensus_guard` 把 `r0_net` 当同义字段做共识 → 只要同花顺 `buy_lg_amount` 与东财 `net_amount` 存在主力/大单口径差，就会反复误触发 `unexplained_dispersion`。
2. 对 H1b 攒样：这是潜在的高频误杀源——任何标的都可能因主力/大单口径差被拦成 ABSTAIN，压制有侧样本产出。

## 修复方向（窄修复卡，待授权）

`r0_net` 共识应**只在同语义字段间**比较：
- 选项 A（推荐）：共识比较时按 `upstream_field` 对齐——东财 `net_amount`(主力) 只与同语义字段比；同花顺 `buy_lg_amount`(大单) 若要与东财主力对齐，应取东财 `buy_lg_amount`（若有）或标注为不同口径不参与同字段共识。
- 选项 B：同花顺 `buy_lg_amount` 不写入 `r0_net`，改写独立字段（如 `lg_net`），`r0_net` 保留给真正的主力净额源。
- 选项 C：若确认东财主力=超大单+大单，可对同花顺补取超大单字段或声明不可比。
- **不做**：放宽 20% 阈值、对两值取平均——那会掩盖真实口径差。

修复前需先取得两源上游字段的官方口径文档（Tushare `moneyflow_dc`/`moneyflow_ths` 字段定义），确认东财主力与大单的可比分量，再决定 A/B/C。
