# H-03c 组合选档（Portfolio + 定时/批量）

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `c62ff8e2a4a8d00dbb05aa592c40d7729da6150e`（或其线性后代）。脏宿主禁止当工作树、禁止 reset/clean。  
**一个关注点：** 组合页创建/改期/批量改期的请求体显式发送单档 `horizon`（`short` 或 `medium`）；默认短期可见；双档禁止（H-03a 定时无双档存储）。测 fetch body，不只测开关颜色。  
**禁止：** 改 `ChatCopilotPanel` / 聊天路径（H-03b 已合入）；重写 DualHorizon 报告 UI（H-05b）；把 `AnalysisHorizonSelector` 的 dual 选项接到定时 API；改 `role_bindings`/`providers`；H-04+；C-04/C-09-3/Track B/H1b/PDF；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。不要自建审核卡。

计划 v1.1 **H-03c**。现网（合入后的 `c62ff8e`）：

- `api.createScheduled(symbol, 'short', '20:00')`（`frontend/src/pages/Portfolio.tsx` `toggleScheduled`）创建时写死 short，用户在开启前看不到可选档。
- 行内/批量已有 `HorizonSwitch`（仅 short/medium），`updateScheduled` / `updateScheduledBatch` 传 `horizon` 字符串。缺请求体测试。
- 后端定时只存单档；双档列表必须 400/ValueError，UI 不得发送 `['short','medium']` 或静默当 short。
- `investment_horizon`（持仓意图）不得写入 `horizon`。

## 允许改

- `frontend/src/pages/Portfolio.tsx`（及必要测试；可新建 `Portfolio.test.tsx` 或扩 `frontend/src/services/api.test.ts`）
- 仅当 create/update/batch 的 JSON 字段名与 H-03a 契约不一致时，才改 `frontend/src/services/api.ts` 的 `createScheduled` / `updateScheduled` / `updateScheduledBatch`（不要动 `chatCompletion`）
- 可复用页面内 `HorizonSwitch`（短/中），不要为定时引入 dual 第三档

## 契约

1. 开启定时：请求体含显式 `horizon`（默认 `short`），提交前可见；可选 `medium`。
2. 行内改档 / 批量改档：body 为所选单档，不得省略成后端缺省。
3. 不得发送双档列表；不得把非法档静默改成 short。后端 4xx 要 `alert`/可见，不得当成功。
4. `investment_horizon` 不写入 `horizon`。
5. 不改聊天选档。

## 测试

```bash
cd frontend && npm test -- src/pages/Portfolio.test.tsx src/services/api.test.ts
```

（若未新建 Portfolio 测试文件，则把断言放进 `api.test.ts` 并在命令里写真实路径。）断言 `createScheduled` / `updateScheduled` / `updateScheduledBatch` 的 fetch body。一个 commit，push 功能分支，评论完整 40 位 SHA、父提交、`git diff --stat`、真实 vitest 计数。
