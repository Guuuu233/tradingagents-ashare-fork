# 独立审核（只读）：H-02c 派生结果键隔离

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `afd89503b9f93fc4b4a5b6b5a868bca686de030f` 或其线性后代
- **关联开发卡：** [DAV-675](mention://issue/01a07349-5ee4-7e83-9565-c1b768c5c9ef)
- **Commit：** （待填）

## 白名单

仅允许：

- `tradingagents/graph/trading_graph.py`
- 若测试证明混档：`api/services/report_service.py`（仅 get_latest 类读取路径）
- `tests/test_horizon_cache_isolation.py` 和/或 `tests/test_horizon_run_metadata.py`
- 最小回归：`tests/test_data_collector.py`、`tests/test_trading_graph_multi_horizon.py`、`tests/test_report_social_context.py`

超出即打回：`make_cache_key` 语义变化、`data_collector` 按档拆池、frontend、校准 hold_days、`propagation.py`、分析师。

## 复核要点

1. 同 ticker+date 换档，log/checkpointer 不互相覆盖。
2. 原始池键仍是 ticker+date。
3. 隔离复跑实现卡写明的 pytest。书面 ✅ / ⚠️ / ❌，含路径行号。勿 FF。
