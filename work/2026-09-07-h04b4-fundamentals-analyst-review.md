# 独立审核（只读）：H-04b-4 基本面分析师运行档/观察窗/trace

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `1e28ee115e96b56fcf333e05832e0ae9231fda4c` 或其线性后代
- **关联开发卡：** [DAV-691](mention://issue/01a077d4-be85-7ac7-946f-c2b6b085ea14)
- **Commit：** （待填）

## 白名单

仅允许：`fundamentals_analyst.py`、`tests/test_fundamentals_analyst.py`、以及 zh/en 的 `fundamentals_system_message`。超出即打回：其它分析师、`data_collector.py`、`intent_parser.py`、`horizon_context_block`、frontend、`role_bindings`。

## 复核要点

1. 短期任务 trace 不得把整次运行写成 medium；观察窗保持 medium；`data_window` 仍为「财报周期」。
2. 未把回看/评价步长改成 T+40。
3. trace 字段与 H-04b-1 冻结方案一致：`horizon`=研究档，另有 `research_horizon` / `observation_horizon`。
4. `pytest -q tests/test_fundamentals_analyst.py tests/test_horizon_analyst_context.py` 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
