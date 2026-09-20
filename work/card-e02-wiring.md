**实施卡 E-02 接线（生产消费，独立关注点）。** 依据联合审计（`work/audit-conclusion-20260910.md`）+ 计划 §7 E-02。前置 = 当前 trunk `1b9bb44d07a525e19d07f0063277cf7893a476a1`。

## 缺陷（真 trunk 核实）

`reduce_evidence_claims`（`claim_cluster.py:591`，签名 `(relations: Sequence[EvidenceRelation] | EvidenceRelationGraph) -> EvidenceReductionResult`）已实现且有测试，但**生产路径零调用**。`research_manager.py`（:440）仍用 `tally_cluster_votes`（关键词聚类）算 `claim_cluster_metrics`（independent_cluster_count / analyst_count / verified_evidence_count）喂决策。计划 §7 要求 E-02 进入 `research_manager`。

## 唯一关注点

让 `research_manager` 的"独立支持/贡献"来自 **E-01 关系图 + `reduce_evidence_claims` 的确定性去重与分组贡献约束**，而非仅关键词聚类：已判定同事实的复制**不增加**有效支持；新的独立观察**保留**增量；保留原始引用与"为何合并/不合并"的审计。

## 接入点与开放问题

- 接入点：`research_manager.py:440` 的 `tally_cluster_votes(...) -> claim_cluster_metrics`，其字段驱动决策提示与状态。用 reducer 结果**修正/替代**其中的"独立计数"口径（保持既有字段名与下游消费兼容，或同步更新消费者，二选一并说明）。
- **开放问题（coder 必须先查清并在交付说明）**：`EvidenceRelation` 图（E-01）在 research_manager 上下文如何取得——若管线已有则接入；**若尚无，必须显式 surface 为子缺口并让该样本走 pending/unknown，禁止凭空造关系或默认独立**。

## 计划 §7 硬约束

- 精确 ID 关系用硬断言；无法确定的语义复述用**冻结 fixture 覆盖并披露覆盖率**；
- **禁止**填 0.8 相关矩阵或新的默认因子权重；
- 不改辩论轮数/确认闸/H1b。

## 允许改

- `tradingagents/agents/managers/research_manager.py`（消费接线）
- 必要时 `tradingagents/agents/utils/evidence_verifier.py`、`claim_cluster.py`（仅接线所需，不改 reducer 语义）
- 新增/扩展 `tests/test_evidence_duplicate_invariance.py`、research_manager 相关测试

## red_team_scenarios（D-012 §5b）

| # | 场景 | 预期 |
|---|---|---|
| RT-1 | 公告复制 / 语义改写（同事实） | 有效支持**不增加**（去重生效） |
| RT-2 | 新的独立观察 | 增量**保留**，不被误合并 |
| RT-3 | 审计 | 输出"为何合并/不合并"，保留原始引用 |
| RT-4 | 无 E-01 关系图可用 | 该样本 pending/unknown，**不静默默认独立、不造关系** |
| RT-5 | 既有决策回归 | 现有 claim_cluster_metrics 消费者不被破坏（字段兼容或同步更新） |
| RT-6 | 换 Agent 复述同指标 | 不因换发言者变独立 |
| RT-FULL | 真全量 `pytest -q -p no:randomly` | 对 trunk 1b9bb44 基线零新增 |

## 硬约束

只读生产库、副本验证、禁 FF/部署/重启/写库/加权。交付置 in_review。

## 交付

完整 40 位 SHA、第一父（=1b9bb44）、diff --stat/--check、6+RT-FULL 实测、关系图取得方式说明、工作树状态。
