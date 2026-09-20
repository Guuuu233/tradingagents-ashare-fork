# 全面终审 泳道四：前端交互与实时流性能审计 (frontend/src/)
【严格只读，不修改代码，不合入，不重启】

重点审计范围：
1. `frontend/src/components/`（ChatCopilotPanel.tsx, ReportViewer.tsx, AgentCollaboration.tsx, RoleModelConfigSection.tsx, CalibrationPanel.tsx 等）
2. `frontend/src/pages/`（Analysis.tsx, Settings.tsx, Reports.tsx 等）
3. `frontend/src/stores/` 与 `services/`（analysisStore.ts, api.ts 等）

审查要求：
- 逐行检查 SSE 实时流事件处理、消息状态机转换（避免孤儿气泡、重复卡片、内存泄漏）；
- 检查虚拟滚动、长列表渲染性能与平滑滚动体验；
- 检查多角色配置联动、模型切换与缓存清理逻辑；
- 产出详细缺陷清单（文件、行号、问题现象、风险等级、优化建议）；
- 评论末尾不要 mention 项目调度助手。

[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff)
