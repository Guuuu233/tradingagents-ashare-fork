# DAV-287 P1：历史案例 T+1 实际值回填任务（§5.4 闭环补全）

**基线父提交：`cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。独立分支，不合主干。**

## 缺口（已由 Hermes 审计坐实）

`historical_cases` 案例仅在报告 completed 落库时计算一次 T+1。评估日（T+1）未到时 `actual_outcome=【数据缺失】`，此后**没有任何代码路径回填**——案例库将永远缺失「实际」，§5.4「预测 vs 实际」闭环断在右半边。现有实例：`d44117e4`（000725.SZ，eval_date=2026-08-24）。

## 契约

1. 新增回填入口（建议 `tradingagents/knowledge/historical_cases.py` 内 `backfill_pending_cases(db, as_of=None)`）：扫描 `actual_outcome=【数据缺失】` 且 `eval_date <= as_of`（交易日）的案例，用现有 `calculate_t1_return` 重算并更新；仍取不到行情保持【数据缺失】并记录失败台账，禁止填 0 或臆造。
2. 触发点：API 启动时 + 每次报告 completed 落库后顺带执行（同进程、幂等、异常只记日志不得影响报告主流程）。
3. 回填成功后重算 `is_error`（复用 `evaluate_prediction_error`）。
4. 禁止改 `calculate_t1_return` 的防前视语义、禁止 `_v2`、禁止碰用户配置/providers/`.env`。

## 验收

- `tests/` 新增：回填成功路径、eval_date 未到不回填、行情缺失保持缺失、is_error 重算、幂等（重复跑不重复写）。
- `.venv310` 定向测试 + `compileall` + `git diff --check` 全绿；推送独立分支精确 SHA。
- 不得 @项目调度助手。
