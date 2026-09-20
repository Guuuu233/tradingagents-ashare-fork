# 资金流：同花顺大单平级接入 + 参考可信度（非绝对判断）

**日期**：2026-09-04  
**父 tip（必钉）**：`31c32f0f877e86fc3c06eb58a34b4dc08a453044`  
**分支建议**：`codex/dav-fund-flow-lg-credibility`（从 tip **新建**；禁止从脏 host 工作树直接改）

## 背景（产品语义，必须遵守）

1. A 股平台「主力资金」是**统计口径参考**（大单/超大单代理），**不是**账户级主力身份真相。  
2. 同花顺 `moneyflow_ths` 有 `buy_lg_amount`（今日大单净流入），当前实现却只把 `net_amount` 映射成 `netamount`（总资金），与东财 DC 的 `r0_net`（主力净额）并存时触发 `incomparable_field_semantics`，`smart_money_analyst` **整篇报告替换成冲突空壳** → 主力栏名存实亡。  
3. 用户明确要求：
   - 把同花顺**大单**接进证据链，与东财主力/大单口径**平级、一并获取**；
   - 总净额 `netamount` 只作旁证，**不得**冒充主力/大单，也**不得**仅因「旁边有总净额」就清空报告；
   - 输出带**参考可信度**（同向略高、反向略低，可弱结合价量）；记住是**参考价值**，不是绝对判断价值。

## 任务（实现 · 派资深开发）

### A. Provider（`cn_akshare_provider.py`）

- THS 行在保留 `netamount←net_amount` 的同时，若 `buy_lg_amount` 有效，再产出一条与东财同级的 **`r0_net` 证据**（语义写明「大单净额 / 平台主力口径参考」）。  
- 东财 DC 路径保持 `r0_net←net_amount`（今日主力净流入额），文案也须标明**参考、非身份结论**。  
- 改原路径；禁止 `_v2` 并行函数。

### B. Selection / 可信度（`fund_flow_evidence.py`）

- **收窄/修正** `incomparable_field_semantics`：有有效 `r0_net`（含 THS 大单）时，旁证 `netamount` **不再**把 `direction_allowed` 打成 false 并导致整页死。  
- 新增明确函数（如 `score_large_order_reference_credibility`）：对同字段 `r0_net` 多源  
  - ≥2 源同向 → 可信度偏高  
  - 单源 → 中等偏低 + `single_source`  
  - ≥2 源反向 → 可信度偏低 + 报告须写分歧（仍可展示分源原值；方向仅作弱参考）  
  - 可选弱权重：与当日涨跌是否同向（不得变成「绝对正确」判决）  
- `select_fund_flow_source` 结果写入 `credibility` / `credibility_reason` / `reference_only=true`（或等价字段）。  
- 更新 `consensus_prompt_instruction`：强制「参考价值，非主力身份/流向最终结论」。

### C. Smart money（`smart_money_analyst.py`）

- **禁止**仅因旧「不可比」就把全文替换成 68 字冲突空壳。  
- 有大单/主力口径参考时：保留分析正文 + 文首固定参考说明与可信度；  
- **仍禁止**：仅有 `netamount` 时写「主力吸筹/增持/减持」等身份话（既有闸门可保留）。  
- `fund_flow_consensus_guard` 带上可信度字段。

### D. 测试（TDD：先红后绿）

至少覆盖：

1. THS `buy_lg` → `r0_net` 证据入库，与 DC `r0_net` 平级出现在 evidence。  
2. DC `r0_net` + THS `netamount` 并存 → **不再** `incomparable` 整页阻断；方向可来自 `r0_net`。  
3. DC `r0_net` 与 THS `buy_lg` 同向 → 可信度高于单源；反向 → 可信度降低且报告保留分源说明。  
4. smart_money：双源大单参考时报告**不是**冲突空壳；仅 netamount 写「主力吸筹」仍阻断。  

更新被本语义故意改掉的旧断言（`tests/test_fund_flow_evidence.py`、`tests/test_cn_akshare_backup_sources.py` 等）。

### E. 纪律

- 遵守 `AGENTS.md`；一次关注点；不部署；不改 `credit_weighting_enabled`；不碰脏文件 `AGENTS.md` / `frontend/src/services/api.ts`（除非无关）。  
- **TDD**；文末贴完整 **40 字符 SHA**；状态 `in_review`。  
- **D-010**：禁止自行 merge / FF；无 Cursor「准予合入」不得合入。

## 非目标

- 不恢复历史日 `fund_flow_board` 实时快照（前视）  
- 不开加权、不部署  
- 不把可信度包装成「已证实主力动向」

## 验收钉子

- 主力栏在「东财+同花顺都有大单/主力口径」时有可读报告 + 参考可信度  
- `netamount` 不再单独拖死大单参考报告  
- 定向 pytest 全绿；diff 仅本关注点  
