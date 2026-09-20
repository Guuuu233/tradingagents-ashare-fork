# H-03b 交互选档（api.ts + 选择器 + ChatCopilotPanel）

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `75b00a41bb0124c034d7bb8626bdc7a568730d12`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 聊天/普通分析入口在请求体里显式发送分析档位；默认短期可见；计划持有 `investment_horizon` 与分析档 `horizons` 分开。测请求体，不只测按钮颜色。  
**禁止：** Portfolio 页与定时批量 UI（H-03c）；改 `role_bindings`/`providers`；把宿主未提交的 `frontend/src/services/api.ts` WIP 整文件拷进提交——从**干净基线**按 hunk 加 `horizons`，保护模型配置相关改动不入库；H-04+；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-03b**。现网（合入后的 `75b00a4`）：

- `api.chatCompletion`（`frontend/src/services/api.ts` 约 98–115）POST `/v1/chat/completions` **不传** `horizons`，因此 chat 一直走 H-01 缺省 short。
- 无 `AnalysisHorizonSelector`。`UserContext.investment_horizon` 已存在，不要与分析档合并。
- `createScheduled` 已有单档 `horizon` 字段；本卡可给聊天请求加 `horizons: string[]`，**不要**改 Portfolio 调用点。
- DualHorizon 报告组件已存在，不要重写展示逻辑（H-05b）。

## 允许改

- `frontend/src/services/api.ts`、`frontend/src/services/api.test.ts`（或新建同目录测试）
- `frontend/src/types/index.ts`
- `frontend/src/components/ChatCopilotPanel.tsx`（及已有测试若必须）
- 新增 `frontend/src/components/AnalysisHorizonSelector.tsx` 与 `AnalysisHorizonSelector.test.tsx`；禁止两套含义冲突控件
- 若普通分析入口不在 Copilot：只改实际发 `/v1/analyze` 或 chat 的那一处，先 grep 点名，不要扫荡改 Portfolio

## 契约

1. 选择器默认 **short**，提交前可见；可选 medium；双档为显式 `['short','medium']`（保序）。
2. `chatCompletion`（及若存在的 analyze helper）JSON body 含所选 `horizons`；未改选择器时发送 `["short"]`（显式默认，与「字段省略」不同——产品要求提交前可见默认短期，因此请求应带 `horizons`）。
3. `investment_horizon` 不得写入 `horizons`。
4. 非法组合不要静默改档；跟 H-03a 后端 422/400 对齐的错误要能看见（chat 路径至少不把失败当成 short 成功）。
5. 宿主 api.ts 个人 WIP 不纳入 commit。

## 测试

```bash
cd frontend && npm test -- src/components/AnalysisHorizonSelector.test.tsx src/services/api.test.ts
```

断言 fetch body 的 `horizons`。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、真实 vitest 计数。
