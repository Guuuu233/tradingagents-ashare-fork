# 独立审核（只读）：H-04d 投研经理消费研究档

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `5eed1a2a1ddee1ab34ce676d88fe717b5a2fce28` 或其线性后代
- **关联开发卡：** [DAV-699](mention://issue/01a079e1-221d-70fe-b1a0-8750576be46d)
- **Commit：** （待填）

## 白名单

仅允许：`research_manager.py`、`tests/test_research_manager_horizon.py`、以及 zh/en 的 `research_manager_prompt`。超出即打回：分析师、`data_collector.py`、`intent_parser.py`、`horizon_context_block`、计权/cluster 算法、frontend、`role_bindings`。

## 复核要点

1. 中期任务经理 prompt/机读不得把整次运行写成 short；分析师观察窗 short 不得覆盖研究档。
2. 未改计权算法、未开加权、未改 collector。
3. 期限对应核心问题/行动依据进入注入文本；不要求结论必须不同。
4. `pytest -q tests/test_research_manager_horizon.py tests/test_horizon_analyst_context.py tests/test_research_manager_seven_reports_and_verdict_gate.py` 真实计数。书面 ✅ / ⚠️ / ❌。勿 FF。
