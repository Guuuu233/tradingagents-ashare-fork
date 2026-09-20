**实施卡 L1 / E-01 producer（总工派工，2026-09-12）。**

## 决策前置（已定，不再征询）

依据 `work/2026-09-12-dispatch-sequence.md` L1 与 DAV-826 只读决策草案（DAV-826 已 done，推荐方案 1），**总工按 DECISIONS.md D-013 授权采纳「Claim-to-Claim / 辩论协议层」作为 E-01 producer 的最小实现落点**。

理由（DAV-826 源码核对结论）：
- `claim_cluster.reduce_evidence_claims`（`claim_cluster.py:966-1044`）硬编码 `claim_ids: Sequence[str]` 作为节点宇宙，`_reduce_supplied_relation_graph`（`:480`）用 `validate_relation_graph(graph, set(claim_ids))` 校验闭包；
- 采集层（Evidence-to-Evidence）建图的端点是 `evidence_id`，直接传入将 100% 触发 `FailClosedReason.DANGLING_REFERENCE`，须额外引入双模图投影层；
- Claim-to-Claim 端点即 `claim_id`，与下游闭包宇宙零阻抗。

**因此 `tradingagents/dataflows/news_event_evidence.py` 不在本卡范围内**（该文件属 Evidence-to-Evidence 路径；v1.1 §7 E-01 点名的白名单以本卡为准，差异理由即上述决策）。

## 基线

- 精确基线：`70b5b47bcff0618e0db1258a438b0875440d4ac5`（2026-09-12 FF 后的 trunk tip）
- 第一父 = 基线，隔离分支开工
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（**绝对路径**；worktree 内无 `.venv310`）

## 唯一关注点

让生产链路真正产出 `evidence_relation_graph`，使已接线的 E-02 seam 由空转变为生效。**不重构 reducer，不改计权算法，不新增平行路径。**

## 允许改（白名单，严格）

- `tradingagents/agents/utils/debate_utils.py`（在 claim 结算处产生 `EvidenceRelation` 边）
- `tradingagents/agents/utils/claim_cluster.py`（仅当 seam 有缺陷时最小修补；**不得改动 `reduce_evidence_claims` 的既有契约语义**）
- `tradingagents/agents/utils/evidence_relations.py`（仅当契约缺必要字段时最小增补）
- `tradingagents/agents/managers/research_manager.py`（仅在需要把新产出的图传入 `tally_cluster_votes` 时接线）
- 新增 `tests/test_evidence_relation_producer.py`

## 禁止改

`tradingagents/dataflows/news_event_evidence.py`；`prompts/`；任何 DB schema；`api/main.py`；`evidence_verifier.py`；`decision_status.py`；`credit_weighting_enabled`；辩论轮次 3/1；用户模型绑定与密钥；生产库数据。

## red_team_scenarios（D-012 §5b：本清单完备性须经**第二双眼睛**确认后才能进入实现审核）

| # | 场景 | 契约依据 | 预期 |
|---|---|---|---|
| RT-1 | 两 claim 共享同一非空 `canonical_event_id` 且 `evidence_id` 互异 | `build_canonical_source_repetition` L444-480 | 产出 `SOURCE_REPETITION` 边，折叠为同一贡献组 |
| RT-2 | 同一观测派生的两条 claim（派生关系可证） | `_RELATION_FOLDING_TYPES` L387-390 | 产出 `DERIVED_OBSERVATION` 边，折叠 |
| RT-3 | 关系端点引用了不在 `claim_ids` 宇宙内的 id | `validate_relation_graph` L480 / L1037-1044 | 必须 `DANGLING_REFERENCE` fail-closed，**不得静默丢弃或自动补节点** |
| RT-4 | 自环边（source == target） | `EvidenceRelation` 禁止自环 | 必须 fail-closed 拒绝 |
| RT-5 | 关系图存在环（A→B→A） | 环路校验 | 必须 fail-closed 拒绝，**不得靠遍历顺序侥幸通过** |
| RT-6 | 关系无法证明（标题相似 / 关键词重合 / LLM 推断） | `validate_relation` L353-366 封禁 `auto_inferred` / `similarity_score` / `verifier_status` | **不得生成边**；未知必须显式 `unknown`，不得计为独立票 |
| RT-7 | 多来源关系图歧义（state 与 debate_state 各有一份且不一致） | `_resolve_relation_graph_context` L160-167 | 返回 `RELATION_GRAPH_STATUS_INVALID`，不得择优 |
| RT-8 | 合法图输入下的端到端 | — | 合法 fixture 应产出 `available`；**但 RT-3~RT-7 的未知/歧义/断链输入必须继续为 `pending`/`unknown`/`invalid`——不得以「非 pending」作为全局门槛** |
| RT-FULL | 候选完整 SHA 真全量 `pytest -q -p no:randomly` | D-012 §4b | 对照基线 18 failed / 3992 passed 量级，**逐项比对失败集**，有新增失败即不得 PASS |

## 验收钉子

1. 上表 8 条场景 + RT-FULL 逐条实跑并贴出实际输出（含命令、解释器、SHA）
2. 真实报告复现：跑一次真实分析，`evidence_relation_status` 在**合法证据关系存在时**为 `available`，且 `claim_cluster_metrics` 中 `independent_cluster_count` / `effective_contribution_count` 有非零值；证据关系不成立时仍为 `pending`
3. 审计键齐全：`evidence_relation_reduction` 的 11 个审计键不得减少
4. `git diff --check` 洁净；改动严格限于白名单

## 交付

完整 40 位 SHA、第一父、`git diff --name-status`、`git diff --check`、8 条 RT + RT-FULL 实测输出、真实报告 id 与字段回读、工作树状态。`git push` 后用 `git ls-remote origin <分支>` 回读并贴输出。状态置 `in_review`，**由非实现者独立复审**。

## 硬约束

不 FF、不部署、不重启服务、不写生产库、不开加权、不执行真实采集。仅本卡白名单文件可改。
