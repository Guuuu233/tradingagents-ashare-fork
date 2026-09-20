# 独立审核（只读）：H-03a 分析入口接线

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `0019d6c93ce1fd9d9bcc9b2bd7148bd62e76f51a` 或其线性后代
- **关联开发卡：** [DAV-677](mention://issue/01a0752b-ba60-78ea-a03c-d4c4834ab264)
- **Commit：** （待填）

## 白名单

仅允许：

- `api/main.py`
- `api/services/scheduled_service.py`
- `tests/test_horizon_entrypoints.py`
- 回归：`tests/test_watchlist_scheduled.py`、`tests/test_scheduled_queue.py`、`tests/test_horizon_profile_contract.py`

超出即打回：frontend、`api.ts`、collector、分析师、`trading_graph.py` 缓存键、校准。

## 复核要点

1. 中间层不得用 query / LLM horizons 把 default 翻成 explicit 或扩档。
2. 定时单档存储不得静默双档；不支持则拒绝。
3. 隔离复跑实现卡 pytest。书面 ✅ / ⚠️ / ❌，含路径行号。勿 FF。
