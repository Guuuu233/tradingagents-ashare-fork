# 窄修复卡：资金流 `r0_net` 语义错配致系统性 ABSTAIN 误杀（2026-09-19）

> 仅修复卡文档，未授权施工。依据 `work/fund-flow-field-semantics-audit-20260919.md` 只读审计结论起草。

## 问题陈述

`fund_flow_consensus_guard` 把**语义不同**的上游字段塞进同一个 `r0_net` 字段做同字段共识比较，导致系统性误杀：

- 东财 `moneyflow_dc.net_amount` = 今日**主力**净流入额（≈超大单+大单）→ `r0_net = 0.40185`
- 同花顺 `moneyflow_ths.buy_lg_amount` = **大单**净额（缺超大单分量）→ `r0_net = 0.758931`

同名不同义 → `unexplained_dispersion` 30.76% > 20% → `ABSTAIN`/`NO_TRADE`/`BLOCKED`。

**非单位错误**（两边万元→亿元换算一致）、**非日期错配**（同 2026-09-18）。是主力 vs 大单的口径差。若取东财自身 `buy_lg_amount=0.874816` 与同花顺 `buy_lg_amount=0.758931` 比，离散仅 ~14%，在阈值内——证明错在字段错配而非数据分歧。

## 影响

- `c21456dd` 为实测例证；凡东财/同花顺并存且主力≠大单的标的都可能被误拦成 ABSTAIN。
- 直接压制 H1b v1 cohort 有侧样本产出（bull/bear 各需 ≥25，当前 1/3）。
- 在修复前批量攒样本会持续污染样本池，故暂停攒样，先修后攒。

## 修复要求（不可妥协）

1. **字段分离**：`net_amount`（主力）与 `buy_lg_amount`（大单）分开保存，不得归一成同名 `r0_net` 后互比。
   - 同花顺 `buy_lg_amount` 改写独立字段（建议 `lg_net` 或保留 `r0_net` 但标注 `peer_field=lg_net`），`r0_net` 仅承载真主力净额口径。
   - 东财侧若能拆出超大单/大单分量（`buy_elg_amount`/`buy_lg_amount`），应与同花顺 `buy_lg_amount` 同语义对齐。
2. **同义才可比**：共识比较仅在同语义、同单位、同日期字段间进行。
   - 主力对主力（东财 `net_amount` vs 其他主力口径源）
   - 大单对大单（东财 `buy_lg_amount` vs 同花顺 `buy_lg_amount`）
3. **不可比即 fail-closed**：当同语义字段无对端可比值时，标记 `semantic_incomparable`，保持 `blocked`/`not_checked`，不得退化为跨口径比较或放行。
4. **不放宽阈值**：20% 离散阈值不变；不对两值取平均；不合并不同口径。
5. **口径文档化**：在 `fund_flow_evidence.py` 字段语义表与 provider 注释中写明东财 `net_amount`=主力、同花顺 `buy_lg_amount`=大单，禁止后续再归一混装。

## 回归测试（TDD，先红后绿）

- [ ] **复现 `c21456dd`**：以报告实际证据记录构造 fixture（东财 `net_amount=4018.5万`、同花顺 `buy_lg_amount=7589.31万`），断言修复后**不再**产生 `unexplained_dispersion`，且按语义落入可比/不可比分支而非误杀。
- [ ] **真实同字段冲突**：构造两个同语义源给出真分歧（如两个主力口径源差 >20%），断言仍 `unexplained_dispersion` + `blocked`——确保修复不误放行真冲突。
- [ ] **语义不可比**：仅单源单口径可用时，断言 `semantic_incomparable` + fail-closed，不误判共识。
- [ ] **大单对大单**：东财 `buy_lg_amount` 与同花顺 `buy_lg_amount` 离散 <20% 时断言可形成共识（如可行）。

## 允许改动范围

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tradingagents/dataflows/fund_flow_evidence.py`
- `tradingagents/agents/analysts/smart_money_analyst.py`（如需配合字段改名）
- 相关 `tests/`（新增回归 + 更新被语义改掉的旧断言）

## 禁止

- 部署；改 `credit_weighting_enabled`（保持 false）；自行 merge/FF 主干
- 碰 `AGENTS.md`、`frontend/`、社交开关、H1b 门槛常量
- 放宽 `relative_dispersion_threshold`、取平均、跨口径合并
- 改生产库数据

## 验证流程

1. `.backup()` 副本 + 测试 fixture 验证，不直连生产库。
2. 施工在独立分支，跑 TDD 红→绿 + 相关回归。
3. 独立复审（同 SHA 只读），通过后报 40 字符 SHA 给 David 决意合入。
4. 合入并受控部署后，用**一次受控分析**确认该路径不再误杀（单独授权）。
5. 确认修复生效后才启动批次 1 攒样。

## 前置确认（施工前必办）

取得 Tushare `moneyflow_dc` / `moneyflow_ths` 官方字段口径文档，确认：
- 东财「主力净流入」是否严格 = 超大单+大单；
- 东财是否返回 `buy_lg_amount` 可直接与同花顺 `buy_lg_amount` 对齐（`c21456dd` 记录中已见该字段）；
- 同花顺 `buy_lg_amount` 是否含/缺超大单。

据此在选项 A（按 upstream_field 同义对齐）/ B（同花顺大单改独立字段）/ C（补超大单分量）中定最终方案。
