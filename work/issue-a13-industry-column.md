# Track A13：ReportDB.industry 列迁移 + 写路径

## 背景（Cursor 审计 2026-09-02）

tip `98fe5d1` 代码中 `api/database.py` ReportDB 已声明 `industry = Column(...)`，且 A7 把行业写入 `result_data.instrument_context.industry`。

但本地 SQLite `reports` 表 **没有** `industry` 列；`db_report.industry` **无赋值**。门槛脚本靠 JSON/`instrument_context` 提取（69 v2 中约 66 有行业、22 类），SQL 列是死字段。

## 基线

- tip：`98fe5d199e8874ae829d2b492882d82339c836f0`
- 分支建议：`agent/dev1/a13-report-industry-column`
- **单关注点**（迁移与写路径可同卡，但若过大则拆两个 commit：①迁移 ②写路径）

## 只做这件事

1. 为 SQLite/现有库增加 `reports.industry`（可空字符串列 + 索引若已在模型声明）；失败须显式报错，禁止静默忽略。
2. 在 `ensure_report_industry_persisted` / completed 持久化路径同时写入 **SQL 列** 与既有 JSON（改原路径，禁 `_v2`）。
3. `backfill_report_industry.py`：`--db-path` 实写时也填 SQL 列；缺数据不编造。
4. 测试：
   - 新 completed 报告 SQL `industry` 非空（有来源时）
   - 库无列时迁移可重复执行/可检测
   - 接口失败/缺行业 → 列保持 NULL，不填假行业

## 明确不做

- 部署；生产库擅自迁移（本卡只交代码+本地可验证；生产执行另开运维卡）
- 开加权；改门槛阈值；碰 A0 前端
- 脏文件 trio

## 验收

- 定向 pytest 绿；push → 40 位 tip → `in_review`；D-010

## 权威

审计 G4；A7；AGENTS.md 数据/schema 纪律。
