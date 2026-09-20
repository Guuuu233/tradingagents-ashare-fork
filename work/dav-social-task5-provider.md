# 修订后 Task 5：只读 archive provider 与资格护栏

## 规范

`docs/social_data/implementation_plan.md` §5.2 时间资格 + Task 5。

## 基线

- 主干 tip（已含 Task 4）：`de88de4eb33b7595d6fcb9a4c4e84d0a80967db5`
- 父链：Task 2–4 已在主干；本任务从该 tip 开隔离分支
- 建议分支：`agent/cursor/social-b5-archive-provider`
- 禁止改脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 禁止改 `data_collector.py` / prompts / evidence_verifier（属后续 Task）
- 禁止继承 `BaseMarketDataProvider`

## 只做

1. `tradingagents/dataflows/social/provider.py`
2. `tradingagents/dataflows/social/registry.py`
3. `tests/test_social_archive_provider.py`
4. `tests/test_social_as_of_guard.py`
5. 可最小改 `__init__.py` 导出

## 行为

- Protocol 仅 `name` + `fetch_records`；独立 registry
- 只读 URI；缺失库 / schema mismatch / 锁超时 / 未来 as-of → 明确 status（refused/failed 等，按契约）
- 正文资格：`published_at` + `first_seen_at`（相对 cutoff）
- 指标资格：**只用** `snapshot_at <= cutoff`
- **`ingest_at` 永不参与资格**；回归：源时间与 snapshot 均 ≤ cutoff、ingest_at > cutoff → 仍应计入
- 历史日取 `snapshot_at` 最大且 ≤ cutoff 的快照
- `source_updated_at` 在 meta 未 trusted 时不影响资格
- 不启动爬虫；不写 archive

## 验收

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

## 交付

- Commit：`feat(social): add read-only archive provider registry`
- 推隔离分支；回帖精确 SHA + pytest 摘要
- **不要自行 FF 主干**
