# Track A14：门槛/读库脚本在查询前 ensure reports schema

## 背景

主干 tip：`41c5ed3adfa34609fd9ea87408445608e9c67cdf`  
本地未迁移库上 `verify_h1b_gates --db-path` 因 `reports.industry` 缺失直接 `OperationalError` 崩溃。  
`api/database._ensure_report_schema` 与 `backfill_report_industry` 已具备迁移；门槛脚本未接。

## 只做这件事

1. 基线 tip 开分支 `agent/dev2/a14-gates-ensure-schema`
2. 在 `scripts/verify_h1b_gates.py`（及同模式、用 `ReportDB` 读库且未 ensure 的脚本，若有）于 `query` **之前**对所用 engine 调用 `_ensure_report_schema(target_engine=…)`；迁移失败须显式 raise，禁止吞错继续空结果。
3. 测试（可失败→绿）：
   - 构造无 `industry` 列的 SQLite reports 最小库 → 调用 load/verify 路径 → 列被补上且能读 completed
   - 迁移失败（可 mock inspect）→ 明确失败，不 silent golden fallback
4. 单关注点 commit；push → 40 位 tip → `in_review`

## 明确不做

- 部署 / 加权 / 改门槛阈值 / 生产库
- 脏文件 trio
- 大重构 ReportDB

## 验收

D-010；定向 pytest 绿。

## 权威

A13；AGENTS 静默失败禁令；本审计回归。
