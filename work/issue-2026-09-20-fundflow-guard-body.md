# 资金流守卫短路治理（BLOCKED —— 待总工定级排期）

**本卡暂不派发，优先级待总工裁定。** 建议理由：它是当前**唯一还在产出 ABSTAIN 的活跃机制**——数据层（全球指数/财报）已修干净，但决策层仍被它堵着，H1b 样本攒不上。

## 背景（审计已定案，勿重新讨论）

- 审计报告 `work/2026-09-19-global-indices-data-source-audit.md` §1：报告「阉割」真因是资金流守卫拦截导致下游四章节短路，**不是数据采集失败**；
- 实测案例（DAV-1102 验收 job `ce9e7a59`）：`fund_flow_evidence.consensus_audit.direction = blocked`——THS 侧 `netamount` 与东财侧 `r0_net` 均判 `blocked`、仅 `lg_net` 为 `inflow`，跨口径离散触发拦截；
- 存量归因（opus 实测、我方复核一致）：生产库 ABSTAIN **201 条 = 资金流守卫 44 + manager_consistency_hard_gate 157，交集 0、恰好互补**。
  - 44 条签名：`final_trade_decision` 含「资金流来源选择 guard 已阻断」；
  - 157 条为一致性强闸家族（`result_data` 含 `manager_consistency_hard_gate`）；
  - ~~「44+37」系早前另一组统计拼串，作废~~（若按加法读成 81，治理范围会被高估近一倍）；
- 时间分布：44 条里 32 条为 09-02 老批次，**7 条 09-18、2 条 09-19**——最近两条报告（`31dcbebb`、`ce9e7a59`）均为该守卫拦截；
- 交接说明 §3.5 明确：该问题与数据源无关，**另行处理**——即本卡。

### 统计陷阱（实施者必读）

- 守卫数据在 `market_data_context.fund_flow_evidence.consensus_audit`（上文引用路径正确），但**根级另有一个 `fund_flow_consensus_guard`**，且 `same_field_consensus_audit` 与 `consensus_audit` 同为 23 键、疑为镜像——**统计必须固定一条路径**（根级近似取值会取到空值）；
- `result_data` 为 ~1MB 的 JSON 全量上下文，LIKE 统计必须用具体签名文本（如「资金流来源选择 guard 已阻断」、`manager_consistency_hard_gate`），宽泛子串（`%fund_flow%`、`%manager_consistency%`）会把 201 条全命中。

## 范围（待总工确认后细化）

- 诊断守卫拦截的口径离散判定逻辑（THS `netamount` vs 东财 `r0_net` vs `lg_net` 的冲突规则）；
- 明确「守卫正常工作 vs 过度拦截」的边界：跨口径离散时哪些情况应降级为警示而非阻断决策；
- 验收须含：历史被拦截样本的离线复放对照（只读），修复后 `ce9e7a59` 同类场景不再 ABSTAIN。

## 红线

- 不得放宽守卫来让报告变绿——守卫存在是为了防错误资金流数据污染决策，治理目标是**判定逻辑的准确性**，不是放行；
- 与 `manager_consistency_hard_gate` 家族（157 条主矛盾）分开处理，不混卡；
- 只读诊断先行，施工待总工另行放行；不动用户配置、不改历史报告。
