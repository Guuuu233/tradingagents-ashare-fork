# P2-T12 返修：social quality gate 契约（基线 f1c73e7）

## 为何打回（Cursor 独立复核，D-010）

候选 `f1c73e7f0b1650fc654e63fdc92f40a2876855fc`：**不准予合入**。

独立审核员 ✅通过不足以合入。Cursor 复现：

### High

1. **`evaluate_social_depth` 把默认 `disabled` / `not_applicable` / `shadow` 当成「不足态」**，要求正文必须含「不可判断」等标记。  
   复现：legacy 风格舆情正文（涨停池/雪球、无「不可判断」）+ `status=not_applicable` → `passed=False`。  
   Gate 0–3 默认仍走 `legacy_proxy`，这会在闸真正生效后误杀正常报告。

2. **生产路径未接入**：`apply_report_quality_gate` 的 `reports_map` 只有 macro/fundamentals/news/volume_price，**没有 `sentiment_report`**。  
   `evaluate_social_depth` 与 Task 12 D 在生产 apply 路径上是死代码；同时测例只直接调 `evaluate_social_depth`，锁不住接线。

### 要求（在同一隔离分支上修，父提交保持 `f1c73e7` 或 rebase 到该 tip）

1. `evaluate_social_depth`：
   - **仅**对 `mode=active`（及明确的 active 不足/失败态：empty/insufficient/failed/timeout/refused）强制「不可判断」标记；
   - `disabled` / `not_applicable` / `legacy_proxy`：**不得**因缺「不可判断」失败（legacy 舆情近似仍合法，直至 Gate 4）；
   - `shadow`：与 disabled 同（正文仍 legacy，不因缺标记失败）；bundle 不足不得当方向证据（manager/verifier 已有，勿削弱）。
2. `apply_report_quality_gate`：把 `sentiment_report`（及 state 内同名字段）纳入 `reports_map` / depth 评估，使 social 闸真正跑起来。
3. 测试（TDD）：
   - disabled + legacy 正文无「不可判断」→ depth **pass**
   - active + empty/insufficient + 无标记 → depth **fail**
   - active + empty + 有标记 → depth **pass**
   - `apply_report_quality_gate` 在 active 不足且无标记时，ledger 出现 social depth 失败条目（或等价可断言痕迹）

## 基线

- 返修起点：`f1c73e7f0b1650fc654e63fdc92f40a2876855fc`
- 分支：继续 `agent/dev2/p2-t12-social-report-gates`（或明确新分支并说明）
- 白名单：`report_quality_gate.py` + 相关测试；若需最小改 `apply` 同文件即可。禁止删 legacy、禁止部署、禁止改辩论轮次。

## 交付

单 commit（或一个清晰返修 commit）；完整 40 位 SHA；pytest 精确数字；`in_review` 后交独立审核员。不要 @调度助手。不要自行 FF。
