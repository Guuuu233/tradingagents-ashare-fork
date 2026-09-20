# 独立审核（只读）：H-05a 双档顶层 not_applicable 聚合

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `5fbd70ee2d09637bce8aa64e441a75e003897d32` 或其线性后代
- **关联开发卡：** [DAV-701](mention://issue/01a07a11-e811-7519-ac99-87d61b21bba4)
- **Commit：** （待填）

## 白名单

仅允许：`api/main.py`；必要时 `decision_status.py` 或 `report_service.aggregate_horizon_metadata`；以及 `tests/test_dual_horizon_e2e.py` / `tests/test_dual_horizon_bugs.py` 的增补。超出即打回：分析师、research_manager、frontend DualHorizon*、计权、collector。

## 复核要点

1. 三个已复现失败用例转绿：混档顶层 `not_applicable is False`；空 structured 默认顶层 False。
2. 单档 `test_structured_not_applicable_still_overrides_existing_false` 仍绿。两档都 True 时顶层仍可为 True。
3. 未重写整套决策状态机，未改前端。
4. 定向 pytest 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
