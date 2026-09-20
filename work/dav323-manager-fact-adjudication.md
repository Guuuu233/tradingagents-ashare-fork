# P0：研究总监七报告事实核验与裁决自洽硬闸

**前置依赖：DAV-320 精确 SHA 独立复审 PASS 后，将该 SHA 填为父提交再开工。当前不得开工。**

## 已复现缺陷

- 总监虽然能看辩论历史，但 market/news/fundamentals/macro 被 `build_evidence_summary` 截至约300字；看不到完整七报告。
- 未注入 `market_data_context.source_provenance/data_failure_ledger/data_gaps`。
- 辩论 evidence 数字无确定性核验；总监不能识别辩手引用失败数据或虚构数字。
- VERDICT 只写文本，未结构化解析与仓位/止损/胜负一致性检查。

## 契约

1. 输入：七分析师报告全部可用。为控制token，可用“结构化高密度摘要+按需原文证据片段”，但每份输入 manifest 记录原长、传入模式、传入字符数；禁止四份统一粗暴300字截断。
2. 注入 provenance 上下文：analysis_baseline_date、source_provenance、data_failure_ledger、data_gaps；显式告诉总监哪些数据可用/partial/unavailable。
3. EvidenceFactualTruthEvaluator：对 claims.evidence 与裁决正文中数值/百分比/日期/关键实体，和七报告+market_data_context做确定性匹配（精确/单位归一/可配置容差）。结果写 `evidence_verification[]`：raw、matched_role/source、status=verified|unsupported|contradicted|source_unavailable。
4. 使用失败账本中 unavailable 指标的方向性数字，必须标 fatal hallucination，不允许纳入裁决。
5. 总监 Prompt与后处理要求：逐条列证据充分/薄弱/缺失；对 unsupported/contradicted claim降权或驳回；不能只凭修辞判断。
6. 结构化 `manager_verdict`：direction、winner（bull/bear/tie）、reason、position_pct、entry、target、stop_loss、upside/downside/odds、adopted_claim_ids、rejected_claim_ids、consistency_check_passed、failed_checks。
7. 自洽硬闸：空头胜不得Buy高仓位；多头胜需有效止损；tie/Hold需验证信号与机会成本。正文与VERDICT矛盾必须有限重试或fail-closed，不得继续Trader。
8. 保留当前fund_flow_consensus_guard，不放宽；不改用户模型/轮数/Provider。
9. result_data持久化上述 manager/evidence/input manifest；历史前端后续另卡展示。

## 白名单
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/utils/evidence_summary.py`
- 可新增 `tradingagents/agents/utils/evidence_verifier.py`
- `tradingagents/prompts/zh.py` / `en.py`
- `tradingagents/agents/utils/agent_states.py`
- `api/services/report_service.py` 或构建payload处（仅持久化）
- 对应测试

## 验收
- fixture：真实数字可验证；不存在数字unsupported；失败源数字fatal；单位亿元/元归一；日期不前视。
- 结构化裁决多头胜/空头胜/tie的正反自洽测试。
- 七报告manifest均present；provenance/gaps传入。
- `.venv310` 定向测试/compileall/diff-check；推精确SHA。禁止 @项目调度助手。
