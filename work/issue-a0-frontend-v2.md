# Track A0：前端 chat 强制 v2_debate_enabled

## 背景

审计收口。`work/2026-08-27-unified-final-plan.md` Phase 1 / A0。本地脏文件已有 +1 行，**主干 tip 无此行**。

基线 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`

## 只做这件事

1. 从 tip 开分支 `agent/dev2/a0-frontend-v2-override`
2. 在 `frontend/src/services/api.ts` 的 `chatCompletion` 请求体加入：
   `config_overrides: { v2_debate_enabled: true }`
3. **单 commit、单文件**（或同文件最小相关类型若已有）
4. 确认后端 allowlist 已含 `v2_debate_enabled`（只读，勿改除非缺）
5. 有前端测试则跑相关；无则说明

## 明确不做

- 部署 / 加权 / schema / 社交 / 其它前端重构
- `git add .`；勿带上 `AGENTS.md` / `h1b_gates_report.json`

## 验收

- push → 完整 40 位 tip → `in_review`
- 评论写清：分支、SHA、diff 文件列表、测试命令与结果
- D-010：等独立审核 + Cursor「准予合入」后才 FF

## 权威

unified-final-plan §A0；审计 G3；AGENTS.md §0–1。
