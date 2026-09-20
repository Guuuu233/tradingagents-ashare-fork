# 独立审核（只读）：H-03b 交互选档

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `75b00a41bb0124c034d7bb8626bdc7a568730d12` 或其线性后代
- **关联开发卡：** [DAV-679](mention://issue/01a07569-4c35-75ab-9975-c3c55bd2a4b9)
- **Commit：** （待填）

## 白名单

仅允许 frontend：`api.ts` / `api.test.ts`、`types/index.ts`、`ChatCopilotPanel.tsx`（及必要测试）、`AnalysisHorizonSelector.tsx` + `.test.tsx`。超出即打回：Portfolio、`scheduled_service.py`、分析师、`role_bindings`。

## 复核要点

1. 请求体含显式 `horizons`；默认 short 可见；持有意图与分析档分离。
2. 未把宿主 api.ts WIP 整文件提交。
3. vitest 断言 body。书面 ✅ / ⚠️ / ❌。勿 FF。
