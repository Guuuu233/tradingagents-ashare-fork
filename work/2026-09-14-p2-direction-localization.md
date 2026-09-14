# DAV-914 前端 direction 展示本地化收口

日期：2026-09-14（Australia/Perth）

## 目标与范围

全仓审计在已有 `localizeDirection` 的前提下，发现三个仍把历史英文方向直接呈现给用户的前端入口：`ChatCopilotPanel`、`AgentCollaboration` 和 `HistoricalDebateDrawer`。本卡只修显示层，不改变持久化值、业务判断、决策优先级、报告内容、API、数据库、配置、社交模式或信用加权。

## 候选与审查

- 候选完整 SHA：`8ccecb8d59de31835f9d0f67578123db9a358e7b`
- 直接父提交：`59ec435306b8c2e49c5ec4a66d433db30df2450c`
- 候选分支：`origin/agent/2/22bc7d1f1184`
- 目标主线：`origin/codex/dav-4-p2a-trunk`
- 实际差异严格为 6 个允许文件：三个组件和三个对应测试文件。
- DAV-915 已由指定的**代码审核员**对同一完整 SHA 做只读审查并 PASS；确认 SHA、直接父、白名单、clean 状态和 `git diff --check` 均通过。审查未修改、未合入、未部署、未重启、未写生产库、未采集真实数据。

## 合入后独立验证

在干净合入树 `/private/tmp/ta-merge-dav910-20260914` 中重新安装前端锁定依赖后执行：

- `npm test -- --run`：**17 个测试文件，171 个测试全部通过**。
- `npm run build`：`tsc` 与 Vite 生产构建均成功；输出只有既有 Vite `__dirname`/native config 提示和包体大小提示，没有类型或构建错误。
- `git diff --check`：通过；合入树无未提交变更。

## 实现边界核对

- 三个组件均复用 `frontend/src/utils/reportText.ts` 的 canonical `localizeDirection`。
- legacy English direction 显示为现有中文别名；当前中文原样保留；空值和未知值保留各入口原有 fallback。
- `AgentCollaboration` 的颜色查找使用同一显示语义，未知值使用默认颜色。
- 原始 `direction`、`verdict`、报告对象和业务数据未被改写。`HistoricalDebateDrawer` 的可选 `initialTab` 默认仍为 `timeline`，只用于测试直达裁决页，不改变生产调用的默认行为。
- 全仓复查未发现这三处之外的已知 raw direction 用户展示入口；`TrackingBoardPanel` 和 `Portfolio` 已由 DAV-887 接入同一映射。

## 合入与运行边界

- 已在目标主线从 `59ec435306b8c2e49c5ec4a66d433db30df2450c` 执行 `git merge --ff-only` 到候选 SHA，并推送；远端回读为 `8ccecb8d59de31835f9d0f67578123db9a358e7b`。
- 本卡**不部署、不重启、不重建线上 live bundle、不写生产数据库、不跑真实分析、不采集社交数据**。线上服务仍运行发布 SHA `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`；后续若发布主线，须另走发布门并重建/复验前端 bundle。
