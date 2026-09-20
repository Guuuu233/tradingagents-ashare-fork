# 卡A：Confirmation Gate claim 生命周期修正（P0/P1 边界）

**日期**：2026-09-04  
**父 tip（必钉）**：`c72dd7b6098297efbec80931dda8bf509c8d8709`  
**分支建议**：`codex/dav-confirmation-gate-lifecycle`（从 tip **新建**隔离 worktree；禁止在脏 host checkout 上改）

## 背景（已复现）

美的 `000333.SZ@2026-08-24`（报告 `5e9e04307a244888b536198d5d6bac07`）：

- 研究经理正文与 `MANAGER_VERDICT`：`winner=bull`，有入场/止损/目标；
- `manager_verdict.decision_status`：`VALID` + `direction=BULL` + `trade_action=WAIT` + `confirmation_state=UNRESOLVED`；
- `reason_codes` 含 `fatal_contradicted_claims:INV-6`；
- `INV-6`：**空头** claim，已在 `rejected_claim_ids`，证据聚合 `decision=reject`（`contradicted:1`）；
- focus 为 `INV-1/INV-2`（已充分验证）；adopted=`INV-1/2/3`；
- Trader / Risk 按 D-009 短路输出 stub → Web `decision=WAIT`（观望）。

根因：`evaluate_confirmation_state`（`tradingagents/agents/utils/decision_status.py`）把**任意** claim 的 `contradicted/source_unavailable` 扫进 `global_fatal_cids`；即使非 focus、未 adopted、经理已 reject、聚合器也判 reject，仍经 `has_global_fatal` 压成 `UNRESOLVED→WAIT`。

影响面：非单例。近期 Batch7 多条 bull/bear winner 最终 WAIT，多带同类 `fatal_contradicted_claims`。

## 权威约束

- D-009：四元拆分；未确认不得伪装成可执行 BUY/SELL。
- Codex + Cursor 共识：**不能**「忽略所有 rejected」；**不能**按多空立场写「对手方被驳倒就不阻断」。
- 不部署、不开加权、不改 3/1、不碰用户模型绑定。

## 精确规则（必须实现）

构造确认相关集合（概念）：

```text
confirmation_relevant_claims =
    focus
    ∪ adopted
    ∪ partially_adopted
    ∪ rejected_but_deterministically_adopted
```

| Claim 状态 | 证据聚合结论 | 应有处理 |
|---|---|---|
| focus / adopted | `reject` 或 fatal | `UNRESOLVED → WAIT` |
| partially adopted | 证据不完整 | `PARTIAL` + `WAIT`，不得直接执行 |
| rejected、非核心 | `reject` | **不再**阻断方向确认；保留审计 reason/日志 |
| rejected | `partial` | 不得静默忽略；保守 → `PARTIAL/WAIT` 或明确风险事项（实现须可测、对称） |
| rejected | `adopt`（证据充分却被拒） | 裁决一致性失败 → `ABSTAIN` + `NO_TRADE` |
| 未参与经理裁决的重大 claim | `adopt/partial` | 不得丢弃；触发完整性/一致性检查（可测最小集） |

美的 fixture **预期**：`INV-6`（rejected + deterministic reject）不再全局 fatal → 确认可 `CONFIRMED`，方向动作可进入 BUY；**风控仍可独立阻断**。页面语义应是「研究看多 / 若风控阻断则写风控阻断」，不是前后自相矛盾。

## 范围（窄）

只改原路径：

- `tradingagents/agents/utils/decision_status.py`（相关性过滤 + 一致性）
- manager verdict → confirmation 接线处（若需）
- 必要测试（见下）
- DB/API 状态字段一致性（decision / trade_action / confirmation_state）

**禁止**：改模型、改权重、部署、大改前端、塞进证据数字匹配（那是卡B）。

前端解释可另开薄卡；本卡最低要求：后端状态正确，且既有 API 字段能让前端日后分层展示（不强制本卡做完整 UI）。

## 最低回归（TDD）

1. 美的真实 fixture：rejected + deterministic reject 的非核心 contradiction **不**阻断。
2. focus/adopted claim contradicted → 仍 `WAIT`。
3. rejected + deterministic adopt → 必须 `ABSTAIN/NO_TRADE`，不得放行。
4. rejected + partial → 有明确保守状态（写进测试）。
5. bull/bear **对称**测试。
6. 无 focus 时的 fallback 行为有测试。
7. 歌尔类「未确认核心 claim」继续 `WAIT`。
8. trader / risk / DB `decision` 与 API 一致（BUY 路径可执行时不再是 stub；WAIT/ABSTAIN 仍 stub）。
9. （可选附证据）对 Batch7 子集重放或静态重算 confirmation，对比 WAIT 原因分布；逐条审计新放行样本。

## 纪律

- 从 tip `c72dd7b6098297efbec80931dda8bf509c8d8709` 建隔离分支/worktree。
- `AGENTS.md`：改原路径、删死代码、一次关注点、不自行合入主干。
- 文末贴完整 **40 字符 SHA**；转 `in_review`。
- **D-010**：独立审核员审 exact SHA → Cursor 同 SHA 隔离复测「准予合入」→ 运维线性 FF。禁止自合并。

## 非目标

- 卡B 证据数字伪 contradiction  
- 生产部署 / 开 `credit_weighting_enabled`  
- 只改文案粉饰矛盾  
