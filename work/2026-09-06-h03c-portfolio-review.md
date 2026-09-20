# 独立审核（只读）：H-03c 组合选档

**只读审查。** 禁止改功能代码、commit、push、FF、部署。PASS ≠ 准予合入。

无完整 40 位候选 SHA 前保持 `todo`、**不指派**。有 SHA 后必须 `issue assign --to-id aa01a41a-c3da-4021-9e45-a592ac77166c` 并只在本卡 @。实现卡禁止 @本审核员。

## 候选（合入用 SHA）

- **审核/合入 tip：** （待填 40 位）
- **分支：** （待填）
- **第一父 / 基线 tip：** 必须是 `c62ff8e2a4a8d00dbb05aa592c40d7729da6150e` 或其线性后代
- **关联开发卡：** [DAV-681](mention://issue/01a07593-face-75b8-a17d-2f444db9ec0b)
- **Commit：** （待填）

## 白名单

仅允许 frontend：`Portfolio.tsx`（及测试）、必要时 `api.ts` 的 scheduled 三个方法 + 对应测试。超出即打回：`ChatCopilotPanel`、分析师、`scheduled_service.py`、`role_bindings`、DualHorizon 报告组件。

## 复核要点

1. 定时请求体显式单档；默认 short 可见；无 dual。
2. 4xx 可见；未静默改档。
3. vitest 断言 body。书面 ✅ / ⚠️ / ❌。勿 FF。
