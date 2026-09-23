# DAV-1227 准备线 B：样本供给漏斗只读诊断

一条命令复跑（仓库根目录，Python 用项目 `.venv310`）：

```sh
# 1) 取生产库只读快照（写本目录，已在 .gitignore 排除，不入库）
sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db?mode=ro" \
    ".backup 'work/prep-b-supply-funnel/snapshot.db'"

# 2) 跑诊断（只读，immutable 打开快照；零 LLM、零写库）
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
    work/prep-b-supply-funnel/run.py --db work/prep-b-supply-funnel/snapshot.db
```

输出：`report.md`（人读）+ `report.json`（机读，含 occurrence 级明细）。
口径：production view = 3d9c414（无 Stage 3.5）；trunk view = 9d03c89（DAV-1139 HOLD 语义隔离 + DAV-1200 价格口径隔离）。E-04 复放走 trunk `research_manager` 的 occurrence 级判定函数。
