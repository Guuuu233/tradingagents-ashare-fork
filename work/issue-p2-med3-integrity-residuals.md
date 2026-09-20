# P2-MED3：社交完整性残留（R1 / R2 / R3）

来源：`work/audit-recent-social-20260901.md`（Cursor 总审，无 HIGH；下列为 MED）。

## 基线

合入 MED2 后的主干 tip（预期 `883bded…`；开工前 `git ls-remote` 核验）。  
新建分支如 `agent/dev2/p2-med3-integrity-residuals`。  
**每个 R 单独 commit。** 禁止 Gate4 / 删 legacy / 部署。

## R1 — metrics_json 损坏误标 archive_missing

- 现象：损坏 `metrics_json` 抛 `JSONDecodeError`，外层常映射为 `failed` + `social_archive_missing`
- 期望：行级拒绝该 snapshot；或 typed `social_archive_corrupt` / 等价；**仅**真缺库才用 `ARCHIVE_MISSING`
- 文件：`provider.py`（`_build_raw_record` / fetch 外层）+ 测

## R2 — 缺 crawler_commit 拒行后伪装 empty

- 现象：PIT 候选存在但元数据拒尽 → `empty`/`social_empty`，掩盖完整性问题
- 期望：独立 reason（如 `social_invalid_ingest_run`）；必要时 `failed`；M2 测断言 status/reason 不只 `len==0`
- 文件：`provider.py` + `tests/test_social_archive_provider.py` 等

## R3 — DEFAULT_CRAWLER_COMMIT 仍可省略捏造

- 现象：importer / `run_social_ingestion` 在 `None` 时仍可落到硬编码 SHA；空串已拒
- 期望：与 `import_mediacrawler_social.py` 一样**强制显式** `--crawler-commit`；删除或废止默认捏造路径
- 文件：`mediacrawler_importer.py`、`scripts/run_social_ingestion.py` + 测

## 不做

LOW L1–L5（另卡）；Gate 4；部署。

## 交付

先 push；每 commit 40 位 SHA + pytest → `in_review`。
