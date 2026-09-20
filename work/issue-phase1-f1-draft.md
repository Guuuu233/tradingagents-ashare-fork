# Phase 1 草稿（先写后派）：三段式对抗层 — 免改拓扑双盲

**本卡现在不派。** 派工闸门（全部满足才 `in_progress`）：
- DAV-339 精确 SHA 复审通过并合入
- DAV-345 主干合入 + 服务重启完成
- 核库 `user_llm_configs` 仍为辩论 3 / 风险 1
- `/api/health` 返回 JSON；role-bindings 无明文 key
- C 泳道 `fix/ta-audit-c`（64f9167）已合入主干（challenge 复合句依赖多行聚合）

蓝图：`work/2026-08-23-v2-blueprint.md`（Hermes 闸门 1–4 + F1 实现缺口全文有效）。

## 范围（单卡）

F1 三段式（映射到 max_debate_rounds=3）+ F3 裁决增强 + F4 信念轨迹 + H1a 影子采集（零加权）+ F2 立论战场覆盖约束。
捆绑洗白若 DAV-xxx 补丁已合入则本卡不再改 Check D；未合入则并入本卡。

## 首个里程碑：免改拓扑双盲（不是 fan-out/join）

图保持串行乒乓，**不改 `setup.py` 拓扑**。

1. researcher 按 `stage=opening|challenge|tiebreak` 构造 prompt：
   - opening：history/current_response 置空；claims/focus/unresolved 只己方；past_memory 对本 ticker `n_matches=0`
2. **Check B/C 必须按 stage 分相**（`debate_utils.py:768/798` 现为 `message_index>=2`）。顺序图上空头立论是第 2 次发言，不改闸则空头 R1 被强制反驳 → invalid_protocol。`stage` 进 payload/state。
3. `conditional_logic.should_continue_debate`：opening 2 条后进 challenge；challenge 2 条后若无需加赛则 `tiebreak_skipped=true` 进总监（count=4 不得再送回 Bull）。
4. challenge 段：`new_claims` 必须空；输出 `challenges[]`；evidence 过 `EvidenceFactualTruthEvaluator`；fatal 未 verified 不得否决 claim。
5. 总监 prompt 去掉写死「基于3轮辩论严格执行」，按实际轮次/跳过注入。
6. 前端抽屉：适配 4 条+challenges+tiebreak_skipped，或显式另卡并保证旧抽屉不崩。
7. H1a：影子分落库，不进采纳公式。

## 验收（结构性闸门，利用率 70% 不做本卡否决项）

- 零数字级回收克隆
- Check B/C 按 stage；空头 opening 不被拒
- challenge≥2 且证据过闸；未 verified fatal 不否决
- 库内仍 3/1；600276 式自洽闸/协议闸不回归
- 前端旧报告可打开
- 上线验收换全新标的；金标准三只只做离线回归
- 禁止改 role_bindings / providers / API Key
