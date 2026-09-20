@资深开发2 开工 Track A15。

父 tip 钉死：`03cfb47c2f01981a6f80254fba8ac6a857b4d987`
派发说明：`work/2026-09-03-a15-industry-backfill-ensure-dispatch.md`

唯一缺口：`scripts/backfill_report_industry.py` 默认 `#4 get_db_ctx()` 路径未 `_ensure_report_schema`（`--db-path` 路径已有）。对齐 `backfill_tplus5_shadow.py`，TDD，完成后贴 40 字符 SHA 并转 `in_review`。D-010：禁止自行合入/部署。
