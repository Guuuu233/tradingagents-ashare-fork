# 独立审核（只读）：H-04b-7 量价分析师运行档/观察窗/trace

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `a822a866c3301cc3acd474d073b3ef823d5f6916` 或其线性后代
- **关联开发卡：** [DAV-697](mention://issue/01a079c7-9409-733e-98e8-fb1f1f58f884)
- **Commit：** （待填）

## 白名单

仅允许：`volume_price_analyst.py`、`tests/test_volume_price_analyst.py`、以及 zh/en 的 `volume_price_system_message`。超出即打回：其它分析师、`data_collector.py`、`intent_parser.py`、`horizon_context_block`、frontend、`role_bindings`。

## 复核要点

1. 中期任务 trace 不得把整次运行写成 short；14 天观察窗保留；`get_window` 仍用观察窗 short。
2. 未把回看天数改成 T+10。
3. trace 字段与 H-04b-1 冻结方案一致：`horizon`=研究档，另有 `research_horizon` / `observation_horizon`。
4. `pytest -q tests/test_volume_price_analyst.py tests/test_horizon_analyst_context.py` 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
