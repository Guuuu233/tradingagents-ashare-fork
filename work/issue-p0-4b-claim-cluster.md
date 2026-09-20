# P0-4b：claim 按证据 cluster 去重计票；analyst_count 不得当权重

## 目标

D-009 / 审计稿 §P0-4 的**第二刀**：每个 claim 绑定底层 `evidence_ids` 与 `cluster_id`。技术、量价、资金若都只依赖同一收盘价冲击、成交量和当日价格变化，只计**一个**底层 cluster。方向权重用 `independent_cluster_count`，**禁止**用 `analyst_count` 当独立票。

报告可以继续逐一列出七位分析师（DAV-336 不得回退），但那是解释，不是额外票。

本卡**不做** VWMA/主力成本去人格化（P0-5）、**不做**资金流 selection（P0-4a 已在主干 `12120c7`）、不是社交、不是部署。不开 `credit_weighting_enabled`。不改辩论 3/1 轮次。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`
- **新建**隔离分支，例如 `agent/dev2/p0-4b-claim-cluster`
- **不要**在 `agent/dev2/p0-4a-selection-not-consensus` 或宿主 `agent/senior-dev-2/p0-1-r5-graph-tests` 上继续堆
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-4、§5「Agent 证据相关性」
- 验收钉子原文：工业富联模拟三份 price-derived report 只能形成一个 cluster，不得得到 60% 独立权重
- `work/2026-08-27-decision-semantics-workflow.md` P0-4

## 已复现（主干 `12120c7`）

1. 仓库内 **没有** `cluster_id` 字段或 cluster 计票函数（`tradingagents/` grep 为空）。
2. 英文 `research_manager_prompt` 仍写 `Tally analyst verdicts and compute bull/bear ratio`（`tradingagents/prompts/en.py`）。
3. 中文 prompt 虽写「严禁简单数人头」，但仍按 **horizon** 给七位分析师动态权重；技术面 / 量价 / 主力资金可以同时对同一价格冲击各拿一票。`battlefield` 把 `price_volume` 与 `capital_flow` 拆开，不能当 cluster。
4. `evaluation_schemas` 的 `unique_claim_count` 是文本 clone_rate，不是证据 cluster。
5. Claim 机读字段只有 `claim / evidence / confidence / target_claim_ids / battlefield`（`debate_utils._MACHINE_CLAIM_FIELDS`），没有 `evidence_ids` / `cluster_id`。

## 行为契约

新增确定性模块（允许新文件，禁止 `_v2` 平行旧函数）：`tradingagents/agents/utils/claim_cluster.py`。**不要用 LLM 赋 cluster_id。**

### cluster 规则

- cluster 按**底层可观察证据**聚合，不按分析师角色、不按 `battlefield`。
- 同一标的、同一交易日、同属「价格冲击」的 close / volume / 当日涨跌幅 → **同一个** `cluster_id`（稳定哈希，测试可锁死）。
- 技术分析师、量价分析师、资金分析师若证据只引用上述同一组字段，必须落入同一 cluster。
- 独立基本面（例如营收/净利润，且不是从同一根 K 线推出来的）必须是**另一个** cluster。
- 缺可聚类字段、无 `evidence_ids`、只有叙事：该 claim `unsupported`（或等价 status），**不得**进入 `independent_cluster_count` / `verified_evidence_count`。

### 计票输出（必须同时报告）

| 字段 | 含义 |
|---|---|
| `analyst_count` | 发言/报告人数。只作解释 |
| `independent_cluster_count` | 去重后的底层 cluster 数。方向权重用这个 |
| `verified_evidence_count` | 核验通过的证据条数 |

`analyst_count` 不得直接当权重。3 份同 cluster 的看多报告 → 该方向独立权重是 **1 个 cluster**，不是 3/5=60%。

每 cluster 对同一方向最多一票。报告正文不是额外票。

把这三项写进研究总监可消费的 state（例如 `investment_debate_state` 或既有 evidence/guard 摘要）。不要另起一套平行评分然后旧的还在数人头。

### Prompt

- **英文**：删掉或改掉 `Tally analyst verdicts and compute bull/bear ratio` 作为计票方法。改成按独立 cluster 计票；分析师名单只作解释。
- **中文**：保留 DAV-336 要求的七位分析师逐一列出（`tests/test_adjudication_risk_prompts_deep_reasoning.py::test_research_manager_seven_analysts_verdict_overview_prompt` **必须仍绿**）。补一句：计票按 `cluster_id` 去重；`analyst_count` 不得当独立权重；同价格冲击的技术/量价/资金只计一票。
- 不要为了本卡大改五步裁决框架。

### 接线

改原路径：

- claim 规范化处允许并保留 `evidence_ids` / `cluster_id`（若 LLM 乱填，以 Python 重算为准）。
- 在 claim 入账或总监裁决前调用 cluster 函数，覆盖/补齐 `cluster_id`。
- 不要把 cluster 逻辑塞进 `shadow_credit.py`，也不要打开 `credit_weighting_enabled`。

## 不要改

- 资金流 `select_fund_flow_source` / P0-4a 已合入行为
- 财务 `period_kind` / Q2
- VWMA / 主力成本 prompt（P0-5）
- 辩论 3/1、`credit_weighting_enabled`
- 社交 DAV-460
- 受保护脏文件
- 数据库 schema

## 允许修改

- `tradingagents/agents/utils/claim_cluster.py`（新建）
- `tradingagents/agents/utils/debate_utils.py`（claim 字段规范化；不要把 2000 行文件改成另一套辩论协议）
- `tradingagents/agents/managers/research_manager.py`（只接线 cluster 统计；不要重写裁决）
- `tradingagents/agents/utils/evidence_verifier.py` 或 `evaluation_schemas.py`（仅当必须把三项计数暴露给既有摘要；能不加字段就不要扩 schema）
- `tradingagents/prompts/zh.py`、`tradingagents/prompts/en.py`（仅计票语义，见上）
- `tests/test_claim_cluster.py`（新建）
- 若 prompt 测试必须跟着改：`tests/test_adjudication_risk_prompts_deep_reasoning.py`（**不得删掉七分析师逐一列出**）

## 阅读纪律

1. 完整读 `claim` 规范化（`debate_utils` 里 `_MACHINE_CLAIM_FIELDS` 附近）和 `research_manager.py` 里如何把七份报告交给总监。
2. 读审计稿 §P0-4 与 §5 那一行验收标准。
3. grep `Tally analyst`、`analyst_count`、`unique_claim_count`、`battlefield`。
4. 改原路径。函数超过 60 行就拆。

## 测试（TDD）

先在 **`12120c7` 上写会失败的测试**，再写产品代码。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_claim_cluster.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  tests/test_research_manager_run_integrity.py \
  -q --tb=short
```

必须覆盖：

1. **工业富联钉子**：三份报告（市场/技术、量价、资金）只引用同一收盘价、同一成交量、同一日涨跌幅 → `independent_cluster_count == 1`，`analyst_count == 3`。该方向独立权重不得变成 60%（断言权重分母是 cluster 数，或等价：三票坍成一票）。
2. **独立基本面**：第四份报告引用可核验的营收/净利润（不是同一根 K 线）→ `independent_cluster_count == 2`。
3. **无证据 claim**：只有叙事、无 clusterable 字段 → 不进入 `independent_cluster_count`，status 为 unsupported（或你们锁死的等价码）。
4. **同 cluster 同向最多一票**：三份同 cluster 看多，计票后该 cluster 对 bull 仍是 1。
5. **DAV-336 回归**：七位分析师仍须在中文 prompt 中逐一列出；禁止合并/省略的句子仍在。
6. **P0-1 回归**：`test_research_manager_run_integrity.py` 里资金闸仍是 ABSTAIN / `DIRECTION_NA`，不是中性。

不要 `assert result is not None`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`
- 精确 pytest 数字
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」
- **不准予部署**
- 不要 @项目调度助手催工
