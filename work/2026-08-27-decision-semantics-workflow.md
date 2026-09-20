# 决策语义闭环 — 工作流入口（D-009）

日期：2026-08-27  
状态：**已采纳为当前最高优先级施工队列**  
权威审计：`work/2026-08-27-audit-decision-semantics-plan.md`  
与既有方案关系：

| 文档 | 关系 |
|---|---|
| `work/2026-08-27-audit-decision-semantics-plan.md` | **决策语义 / PIT / 回测污染** 的权威施工设计 |
| `work/2026-08-27-unified-final-plan.md` | 仍作双轨总纲；**Track A 施工顺序以本文件 P0→P1 为准** |
| `work/2026-08-27-data-verdict-repair-plan.md` | Track A 细节仍可用；与本文件冲突时以本文件 / 审计稿为准 |
| `docs/social_data/implementation_plan.md` | Track B 不变；可并行做 provider 基建，**不得抢占 P0 合入/验收，active 仍走 Gate** |

---

## 0. 已本地核验的事实（2026-08-27 晚）

| 主张 | 证据 | 结论 |
|---|---|---|
| 7/7 上游 502 仍可 `completed` + HOLD | `300433.SZ@2026-05-06` 报告 `f8724342…`：七段正文均为「分析报告生成失败：Error code: 502」，`decision=HOLD` `confidence=25` | **成立** |
| 恢复后可出真实报告 | 同标的同日重跑 `dfcc30e5…`：BUY/65，七段 2.6k–5.5k 字，非 502 残次 | **成立**（说明问题是语义坍缩，不是“永远跑不出来”） |
| API 只认 BUY/SELL/HOLD | `api/main.py` `legal_decisions`；无 WAIT/NO_TRADE/ABSTAIN/INVALID_RUN | **成立** |
| ReportDB 无运行有效性字段 | `analysis_status`/`risk_status` 不存在；生命周期 `status` ≠ 分析有效性 | **成立** |
| 资金流 guard 伪装成中性 | `research_manager.py` blocked 时 `direction=中性` `winner=tie` | **成立** |
| smart-money/量价 prompt 人格化 | `prompts/zh.py` 明确「假摔洗盘」「主力成本区间」等 | **成立** |
| 历史价默认 qfq/forward | `cn_akshare_provider` 等存在 `adjust=qfq` / forward 路径 | **成立** |
| 回测缩短 hold_days + 未知→HOLD | `backtest_service._get_price_after` / `_classify_decision` | **成立** |
| 校准只筛 `completed` | `calibration_service` 无 `analysis_status` 过滤 | **成立** |
| 社交未进主路径 | `social/` 仅 contracts/archive/importer/entity；无 provider/aggregator | **成立** |
| R1/R2/R3 可完整离线重放 | 仓库无四案完整冻结 fixture/manifest | **待补**（设计可执行，重放未完成） |

HEAD 核验：`codex/dav-4-p2a-trunk` @ `de88de4eb33b7595d6fcb9a4c4e84d0a80967db5`。

---

## 1. 核心原则（写入决策账本 D-009）

**禁止语义坍缩：**

`上游失败 / 前视 / 证据冲突 / 方向未确认` → **不得** 编码为 Neutral/HOLD/`completed` 合格样本。

必须拆开：

```text
analysis_status: VALID | PARTIAL | ABSTAIN | INVALID_RUN | DATA_ERROR
direction:       BULL | BEAR | NEUTRAL | N/A
trade_action:    BUY | SELL | HOLD | WAIT | NO_TRADE
risk_status:     OK | ELEVATED | BLOCKED | UNKNOWN
confirmation_state: CONFIRMED | PARTIAL | UNRESOLVED
```

旧字段 `decision` 可兼容保留；**UI 展示与校准/回测主键改读新状态。**

---

## 2. 施工队列（Multica / Cursor 派工用）

### 并行规则

- **P0 决策语义** 与 **社交 Task 5+ 基建** 可并行，但：
  - 禁止把社交与 P0 打进同一 commit；
  - 社交 **active / 删 legacy_proxy** 不得早于 Gate，且不得宣称“决策语义已修好”；
  - P0 合入主干优先于社交合入冲突文件时的仲裁。
- 不改：辩论轮次 3/1、`credit_weighting_enabled`、用户模型绑定/密钥、历史 snapshot refusal。
- 不碰脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`（除非该任务明确授权）。

### P0 — 先堵错误决策与回测污染（本周主线）

| ID | 任务 | 主要文件（示意） | 验收钉子 |
|---|---|---|---|
| **P0-1** | RunIntegrity + 状态机；7/7 失败 → INVALID_RUN/NO_TRADE | 新增 `run_integrity.py`/`decision_status.py`；`data_collector`；`research_manager`；`api/main`；ReportDB 兼容字段 | R3：502 七失败无方向/概率/百分比；不进校准 |
| **P0-2** | EvidenceRecord + PIT 防火墙 | provenance / provider 采用点；拒绝 future/缺日期 | 任一 `effective_as_of > cutoff` 不得进方向 claim |
| **P0-3** | 财务 `period_kind` + 已红 provider 测 | `financial_announce.py`；announce cutoff 红测 | H1≠Q2；无 Q1 则单季 N/A；红测转绿 |
| **P0-4a** | 资金流 selection≠consensus（已在主干 `12120c7`） | `fund_flow_evidence.py` | EM/THS 不同字段 → direction_allowed=false，不自动全局中性 |
| **P0-4b** | claim cluster 去重计票 | `claim_cluster.py`；claim 评分 | 三份 price-derived 报告只形成一个 cluster；`analyst_count` 不得当权重 |
| **P0-5a** | smart-money/量价 prompt 去人格化 | `prompts/zh.py` | VWMA≠主力成本；滞涨=candidate；无身份不得写机构结论 |
| **P0-5b** | confirmation gate → WAIT | `decision_status` / manager | `confirmation_state=UNRESOLVED` → WAIT/NO_TRADE |

### P1 — 恢复研究可用性

| ID | 任务 | 验收钉子 |
|---|---|---|
| P1-1 | NewsEvidence / EventCluster / coverage | R2：7/29 可见、8/11 future 不可见 |
| P1-2 | capitulation/reversal + staged entry | candidate≠买入；确认后小仓试探 |
| P1-3 | backtest/calibration 隔离 INVALID/ABSTAIN/NO_TRADE；禁止缩短 hold_days | eligible/excluded counts；严格 T+N |
| P1-4 | provider 红灯最小修复（announce/OOR/as_of smoke fixture 化） | 定向集 0 fail |

### P2 — 社交链路（既有 plan）

严格 `docs/social_data/implementation_plan.md` Task 5–15 / Gate 0–4。当前 Task 5 provider 可继续，但 **Gate 4 删除 legacy_proxy 前不得宣布完成**。

### 回归样本（离线 fixture，未齐前不得宣称“案例已修”）

- **R1** 歌尔 `002241` / 2026-05-28  
- **R2** 工业富联 `601138` / 2026-07-30  
- **R3** 蓝思 `300433` / 2026-05-06（七失败 INVALID）  
- R4/R5 蓝思换锚：仅有冻结新闻快照时才启用  

---

## 3. 派工简报模板（复制到 Multica issue）

```text
目标：P0-? <标题>
基线：codex/dav-4-p2a-trunk @ <精确 SHA>
权威：work/2026-08-27-audit-decision-semantics-plan.md §?
不可违反：D-006/007/008/009；不改 3/1；不开加权；不碰用户配置；不混社交 commit
允许改：<文件列表>
禁止改：AGENTS.md / frontend api.ts 脏改 / 社交 active 路径（除非本卡明确）
验收：<对应 R 或单测名>
交付：分支 + SHA + 定向测试命令与结果；勿宣称全仓完成
```

---

## 4. 下一步建议动作（待 David 确认后执行）

1. **P0-1 本地 WIP 已完成两轮审查修补**（见 `work/2026-08-27-p0-1-handoff.md`）；**仍未 commit**。新窗口先复审或等「可提交」。  
2. 社交 Task 5 保持并行基建，但看板标明「不阻塞 / 不抢 P0」。  
3. 同步补 R3 fixture（可用已落库的 `f8724342` 作为失败形态参考，但正式验收仍要 typed failure fixture）。  
4. 批准提交并合入后，下一张开 **P0-2**（PIT / 证据契约）；遵守 Cursor×Multica 章程：功能施工默认团队，Cursor 总控验收。
