# Track A15 — `backfill_report_industry` 默认读库路径补 ensure schema

**日期**：2026-09-03  
**父 tip（必钉）**：`03cfb47c2f01981a6f80254fba8ac6a857b4d987`  
**分支建议**：`codex/dav-a15-industry-backfill-ensure`（从 tip 新建；禁止从脏 host 工作树直接改）

## 背景

A14 已让 `verify_h1b_gates` / `backfill_tplus5_shadow` / `recalculate_weekly_metrics` 在查询 `ReportDB` 前调用 `_ensure_report_schema`。  
审计发现 `scripts/backfill_report_industry.py` **仍有一条默认路径漏掉**：

- `--db-path` 分支（约 L99–104）：**已** `create_engine` + `_ensure_report_schema(engine)` ✅  
- 无 `--db-path`、无 input、落到 **#4 `get_db_ctx()`**（约 L170–176）：**未** ensure ❌  

旧库缺 `reports.industry` 时，默认回填会复现 A13 后同类 OperationalError。

## 任务（实现）

1. 修改 `scripts/backfill_report_industry.py` 默认 `get_db_ctx` 路径：与 `backfill_tplus5_shadow.py` 对齐，查询前：
   - `from api.database import get_db_ctx, ReportDB, _ensure_report_schema, engine`
   - `_ensure_report_schema(target_engine=engine)`（或与既有 `--db-path` 调用签名一致；**不要**再造并行 helper）
2. 失败必须显式抛错/记录，禁止静默吞掉后掉到 golden。
3. 测试（优先扩 `tests/test_report_industry_persistence.py` 或同目录最小新测）：
   - 构造缺 `industry` 列的临时 SQLite；
   - 走**默认 get_db_ctx 路径**（或等价：不传 `--db-path` 但指向该引擎的代码路径）；
   - 断言：ensure 后查询成功，且不会因缺列崩溃。
4. **TDD**：先红后绿；汇报隔离 pytest 精确命令与结果。
5. 遵守 `AGENTS.md`：改原路径、删死代码、一次关注点、不擅自 commit 到主干、不部署、不改 `credit_weighting_enabled`。

## 非目标

- 不准予部署 / 不碰生产库  
- 不改 A13 写库语义、不改门槛矩阵阈值  
- 不改 `AGENTS.md` / `frontend/src/services/api.ts`（除非无关脏文件请勿触碰）

## 验收

- 默认读库路径与 `--db-path` 路径均 ensure  
- 相关测试 passed；diff 仅本关注点  
- 文末贴 **完整 40 字符 SHA**；状态 `in_review`；等独立审核 → Cursor 隔离 → 「准予合入」→ 运维线性 FF  

## D-010

禁止自行 merge；忽略无 Cursor「准予合入」的调度合入指令。
