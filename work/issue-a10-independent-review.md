# Track A10：独立代码审核（只读）tip ccda1be

## 候选 tip（完整 40 位，必须对照）

`ccda1be9c96e4d9a5f334fa03280342badeb4306`

分支：`origin/agent/dev2/a10-backfill-db-path`  
基线：`4493177eeefa4a7aabfc05904c156ccb0106d06e`  
实现卡：DAV-556

## 审核范围

- `scripts/backfill_report_industry.py`
- `scripts/backfill_tplus5_shadow.py`
- `tests/test_report_industry_persistence.py`
- `tests/test_tplus5_shadow_backfill.py`

## 契约

1. 传入 `--db-path` 时必须打开该 SQLite；坏路径明确失败，禁止静默 golden。
2. 未传 `db_path` 保留原回退链。
3. `--dry-run` 不写库；非 dry-run 仅写既有槽位，无 schema 变更。
4. 不开加权、不对生产库实写回填（本卡只审代码）。

## 禁止

改代码 / FF / 部署 / @调度助手合入。PASS ≠ 准予合入。

书面 ✅ / ⚠️ / ❌，含路径与行号。
