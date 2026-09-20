# Ops：本地库 T+5 shadow 实写回填（非部署）

## 背景

审计：tip `98fe5d1` 上 `verify_h1b_gates --db-path data/tradingagents.db` → Dim4 `due=69 / evaluated=0`。  
`backfill_tplus5_shadow.py --dry-run` → due=69 / hit=47 / miss=22 / 完整率 100% / 命中率 68.1%。

Cursor **授权本卡仅对仓库本地** `data/tradingagents.db` 实写。  
**禁止** VPS/生产库、**禁止部署**、**禁止开加权**。

## 基线

- 代码必须用 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`（隔离 worktree / `PYTHONPATH` 钉 tip；宿主 `4fa7681` 不可用）
- 库：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`

## 步骤

1. `git fetch`；确认 tip SHA。
2. 先再跑一遍 `--dry-run`，评论贴汇总。
3. **备份** DB（例如 `data/tradingagents.db.bak-t5-YYYYMMDD`）。
4. 去掉 dry-run 实写回填。
5. 用 tip 再跑 `verify_h1b_gates.py --db-path ...`：期望 Dim4 `due≈69`、`completed≈69`（或 hit+miss）、完整率 ≥95%；系统总状态仍可能 FAIL（bear/交易日/多空比）→ 必须仍为 `KEEP_FALSE`。
6. 评论贴：命令、完整率、命中率、门禁总建议、备份路径。

## 明确不做

- 部署；改代码（若脚本 bug 另开开发卡）
- 开 `credit_weighting_enabled`
- 碰远程生产库

## 验收

- 本地 Dim4 不再 `evaluated=0`；`KEEP_FALSE` 仍成立则写明原因维度
- 本卡 → `done`（无 FF；无产品 commit）
