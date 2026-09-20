# Track A8：`/v1/calibration` v2 样本诚实性（winner-only）

来源：`work/2026-08-27-unified-final-plan.md` A5 剩余；`work/2026-08-27-data-verdict-repair-plan.md` P5（`sample_size>0`）。  
A5 已合入 tip 上的 T+5 shadow 回填；**本卡只改校准 API 选样诚实性**。

## 基线

主干 tip：`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`（开工前 `git ls-remote`）。  
分支建议：`agent/dev2/a8-calibration-v2-sample-honesty`。  
**单关注点 commit。**

## 问题

`api/services/calibration_service.py` 的 `_query_reports` 硬过滤 `ReportDB.probability IS NOT NULL`，再经 `is_calibration_eligible` 再次要求 probability。  
合格 v2 常有 `manager_verdict.winner` 但 **合法** `probability=NULL` → 永远进不了样本，接口可长期 `sample_size=0`，且排除统计也看不见这类行。

## 只做这件事

1. 在校准选样中承认 **completed + 合格 v2**（与 A6 `is_qualifying_v2_report` 同口径）且 `winner ∈ {bull,bear}`、`probability` 为空的行进入可评估集合（hold 窗口 / outcome 解析纪律不变；**禁止缩短 hold**）。
2. **禁止**用 confidence / 文案瞎编 probability。若需曲线分桶：仅用已有显式 `probability`/`odds` 字段；winner-only 行可走「方向命中」计数或单独 bucket，须在代码注释 + 测试里写清契约。
3. 响应或内部计数暴露：`winner_only_admitted`（或等价）>0 可测。
4. 已有 probability 路径行为保持不变。

## 验收

- 夹具：v2 completed、`winner=bull|bear`、`probability=NULL`、hold 已到期且 outcome 可解析 → `sample_size > 0`（或文档化的方向命中样本数 >0，且 HTTP/返回体可观测）
- 夹具：无 winner 且无 probability → 仍不计入
- 夹具：有 probability 的旧路径回归绿
- 定向 `tests/test_calibration_service.py`（必要时补 `decision_status` 测）

## 禁止

- 部署 / 开 `credit_weighting_enabled` / 宣称 D-007 `ELIGIBLE_FOR_ACTIVATION`
- schema / `ReportDB.industry` 列
- A0 / `frontend/src/services/api.ts`
- 社交 / Gate4 再改
- 改辩论 3/1、用户模型绑定
- 脏文件 trio：`AGENTS.md`、`api.ts`、`work/h1b_gates_report.json`
- 重写 A5 backfill 主逻辑（可复用 helper，勿混大改）

## 交付

先 push；完整 40 位 tip + pytest → `in_review`。D-010。
