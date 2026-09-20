# Track A5：v2 winner → T+5 shadow 离线回填

来源：`work/2026-08-27-unified-final-plan.md` A5；`work/2026-08-27-data-verdict-repair-plan.md` P5。  
A6（v2-only 门槛口径）已合入 tip `46a6dfee9601ca679b8d22c0c86a931fccb59b63`。

## 基线

主干 tip：`46a6dfee9601ca679b8d22c0c86a931fccb59b63`（开工前 `git ls-remote` 核验）。  
分支建议：`agent/dev2/a5-tplus5-shadow-backfill`。  
**一个关注点一个 commit**（本卡只做 T+5 回填；勿混 industry 持久化 / calibration API 改造）。

## 问题

`calculate_shadow_credit_metrics` 仅在传入 `t_plus_5_price` 时才写 `t_plus_5_direction_hit`；生产路径常留 `None`。  
门槛 T+5 完整率可因 `due_count=0` 空窗虚高。v2 `manager_verdict.winner` 未接到到期后的 hit 回填。

## 只做这件事

1. 新增（或扩展）离线脚本：对 **completed + 合格 v2**（与 A6 同口径：`is_qualifying_v2_report` / `filter_v2_completed_reports`）报告：
   - 交易日历严格 T+5（**禁止**缩短 hold 窗口）
   - 解析收盘价；缺失 / 停牌分状态（`data_missing` / `suspension` 等既有枚举若有则复用）
   - 用 **`manager_verdict.winner`**（bull↑ / bear↓ / tie±既有 band）写入 `shadow_credit_metrics.t_plus_5_direction_hit`（及必要的 due/evaluated 字段）
2. 支持 `--dry-run`；写回须幂等、可审计（改 `result_data` 时不得丢其它字段）
3. 定向测试：到期命中 / 未到期保持 None / 缺价 / 停牌（夹具，不依赖外网也可跑通核心逻辑）

## 验收

- fixture 上 `due_count > 0` 且完整率不是「空 due 集 → 1.0」
- hit 判定来自 **winner**，不是旧 `decision` 字符串胡猜
- 不开 `credit_weighting_enabled`；脚本默认不改生产 flag

## 禁止

- Gate4 / 删 `legacy_proxy` / 部署
- 开加权 flag
- 改辩论 3/1、用户模型绑定
- 脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 同 commit 混 industry 列持久化或 `/v1/calibration` 大改（另卡）

## 交付

先 push；完整 40 位 tip SHA + pytest → `in_review`。  
等独立审核 + Cursor 准予合入。
