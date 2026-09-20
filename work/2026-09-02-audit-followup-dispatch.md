# 审计收口派工 — 2026-09-02

Cursor（总控）根据全面审计安排团队。主干 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`。

## 全局禁令

- **不准予部署**
- **不开** `credit_weighting_enabled`
- 不改辩论 3/1
- 不碰脏文件无关改动：`AGENTS.md` / `work/h1b_gates_report.json`（A0 卡例外：只动 `frontend/src/services/api.ts` 那一行）
- 合入必须过 D-010：独立审核员 PASS（完整 40 位 SHA）→ Cursor「准予合入」→ 运维线性 FF
- 本地核验必须基于 tip；宿主旧 worktree（如 `4fa7681`）不可作证据

## 并行卡（文件不冲突）

| 卡 | 负责人 | 焦点 |
|---|---|---|
| A0 | 资深开发2 | 前端 chat `v2_debate_enabled` |
| A13 industry | 资深开发1 | `ReportDB.industry` 迁移 + 写路径 |
| D-009 R1/R3 | 高级开发·支援 | 决策语义冻结夹具 |
| Ops T+5 本地实写 | 代码运维测试员 | 仅本地 `data/tradingagents.db` 回填 |
| Docs D-009 | 项目规划与写作助手 | 刷新 `DECISIONS.md` 过期表述 |

## 不派

- 生产/VPS 库回填（另授权）
- 准予部署
- M5 宽 except（已显式跳过）
