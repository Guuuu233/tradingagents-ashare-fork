# 审计收口续：A13 后未迁移库导致门槛脚本崩溃

日期：2026-09-02  
主干 tip：`41c5ed3adfa34609fd9ea87408445608e9c67cdf`

## 事实

tip 代码 `ReportDB` 含 `industry` 列。本地 `data/tradingagents.db` **尚无**该列。  
`scripts/verify_h1b_gates.py` 经 SQLAlchemy 查询时报：

`sqlite3.OperationalError: no such column: reports.industry`

`backfill_report_industry.py` 已调用 `_ensure_report_schema`；门槛脚本未调用 → **旧库不可用门槛脚本**（回归）。

## 并行派工

| 卡 | 负责人 | 动作 |
|---|---|---|
| A14 | 资深开发2 | 读库脚本先 ensure schema；可失败测试 |
| Ops | 代码运维测试员 | 本地库迁移 + industry 回填 + 复跑门槛 |

禁：部署、开加权、生产库。
