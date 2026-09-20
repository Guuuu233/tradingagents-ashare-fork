# 独立审核（只读）：H-04a 研究档与专业观察窗

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `8325943e23e67b2b8f81438d03aa297d9f7eccfe` 或其线性后代
- **关联开发卡：** [DAV-683](mention://issue/01a076f3-5383-7627-bc19-e152f5984c70)
- **Commit：** （待填）

## 白名单

仅允许：`intent_parser.py` 的 `build_horizon_context`/绑定；`prompts/zh.py` 与 `en.py` 的 `horizon_context_block`；`propagation.py` 的初始 state 绑定；`tests/test_horizon_analyst_context.py`；必要时 `tests/test_intent_parser.py` 三个 context 断言。超出即打回：任一分析师/researcher、`data_collector.py`、`research_manager.py`、`api/main.py`、frontend、`role_bindings`。

## 复核要点

1. 研究档与专业窗两行可区分；中期任务 + 短线观察窗不得把任务写成 short。
2. 未改分析师文件；未把回看天数改成评价步长。
3. 指定 pytest 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
