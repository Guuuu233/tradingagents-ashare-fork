# P1-FE 实施：HistoricalDebateDrawer v2 结构可见

## 基线

- fresh isolated checkout：`target/codex/dav-4-p2a-trunk@0554216305b3c860cbe893681335b6b1a29e17ef`
- 禁止进入宿主脏树；独立 worktree；用仓库既有 frontend 脚本测试。
- 不得合入主干、不得重启、不得改 3/1。

## 允许文件

- `frontend/src/types/index.ts`
- `frontend/src/components/HistoricalDebateDrawer.tsx`
- `frontend/src/components/HistoricalDebateDrawer.test.tsx`
- 若 `ReportViewer` 仅因透传类型报错，可做最小类型适配；不得改页面布局以外的业务逻辑。

越界先停并报告。

## 必须实现

类型字段全部 optional（旧报告兼容）：

- ProtocolVersion / DebateStage / Challenge / DisputeMapItem / ShadowCreditMetrics / tiebreak_skipped / debate_degenerate / protocol_version / protocol_stage / challenges / dispute_map / challenge_verification

抽屉新增区块：

1. 协议版本徽标（v1_legacy vs v2_structured_disagreement）。
2. Opening：按 bull/bear 显示 battlefield、claim、证据、置信度、失效条件。
3. Challenge：target、weakest_point、severity、证据状态、总监采纳/驳回。
4. Tiebreak：executed 显示问答；`tiebreak_skipped=true` 显示“证据足以裁决，未触发加赛”。
5. Dispute map。
6. Degenerate 标志。
7. legacy 6 条消息走现有渲染，不得破坏。

硬闸：unsupported/contradicted 的 fatal challenge **不得**显示为“已击穿”。缺字段不崩。

## TDD

先 RED 再 GREEN，覆盖：

- 现有 v1 fixture 视觉/结构不回归。
- v2 4 条消息 + challenges 渲染。
- tiebreak skipped 与 executed。
- unsupported fatal 不显示击穿。
- 缺 protocol/challenges/dispute_map 的旧报告不抛。

命令：`cd frontend && npm test`（或仓库既有等价命令）必须全绿。不要跑后端全量。

## 交付

推独立远端 branch + 精确 SHA。评论必须含：基线 SHA、候选 SHA、变更文件、测试命令与结果、未合入/未重启。不要 mention 项目调度助手。完成后 mention：

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)
