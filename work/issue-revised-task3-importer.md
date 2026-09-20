# 修订后 Task 3：MediaCrawler 导入器（append-only）

## 授权与基线

- 统一方案 B3；细节 `docs/social_data/implementation_plan.md` Task 3
- **基线父提交必须是** `92cf2abdf1a970535de78fadc5b78fbac869defc`（已合 Task 2 的隔离分支 tip），或从该 SHA 继续分支：`agent/cursor/social-b3-importer`
- 禁止从脏宿主树开工；禁止改 `data_collector.py` / frontend / prompts / evidence_verifier

## 只做

- `tradingagents/dataflows/social/mediacrawler_importer.py`
- `tests/social_fixtures.py`
- `tests/test_mediacrawler_importer.py`
- 可最小改 `contracts.py` / `archive_schema.py` 仅当 Task 3 必需（须说明）

要求：按 §3.3 映射；空正文可归档；缺 published_at/add_ts/last_modify_ts 拒收；禁止回填 now/ingest；append-only（涨赞新 snapshot，旧行不变；无 UPDATE）；昵称/token/下载 URL 不落库；TDD；一个 commit。

## 验收

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_mediacrawler_importer.py tests/test_social_contracts.py -vv
```

交付：分支、精确 SHA、父提交祖先含 92cf2ab、pytest 原文。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
