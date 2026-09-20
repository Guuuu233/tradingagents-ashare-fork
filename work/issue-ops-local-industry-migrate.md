# Ops：本地库 A13 industry 迁移 + 回填（非部署）

## 背景

tip `41c5ed3…`。本地 `data/tradingagents.db` 无 `reports.industry`，导致 tip 上 `verify_h1b_gates --db-path` 崩溃。

## 授权范围

仅本地：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`  
代码须用 tip worktree / `PYTHONPATH` 钉 `41c5ed3adfa34609fd9ea87408445608e9c67cdf`（或更新的 trunk tip）。

## 步骤

1. 备份：`data/tradingagents.db.bak-industry-YYYYMMDD`
2. dry-run：`backfill_report_industry.py --db-path data/tradingagents.db --dry-run`（应触发 ensure schema；贴统计）
3. 实写去掉 `--dry-run`
4. `PRAGMA table_info(reports)` 确认有 `industry`
5. 复跑 `verify_h1b_gates.py --db-path …`：不得再因缺列崩溃；贴 7 维摘要；期望仍 `KEEP_FALSE`（除非维度真过）
6. 评论：命令、备份路径、回填统计、门槛摘要

## 明确不做

- 生产/VPS 库、部署、开加权、改产品代码（代码修在 A14）

## 验收

本卡 → `done`（无 FF）。
