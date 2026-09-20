# 修订后 Task 2：社交 contract 与 archive schema

## 授权与基线

- 统一方案：`work/2026-08-27-unified-final-plan.md` Phase 8 / B2
- 详细规格：`docs/social_data/implementation_plan.md` Task 2 + §3–§4 + D-008
- 宿主主干：`codex/dav-4-p2a-trunk` @ `aa41f44992674144eb2e320fc1962f7b0022a795`
- **隔离分支施工**：从上述 SHA 新建分支，例如 `agent/cursor/social-b2-contracts`。禁止 checkout 覆盖宿主脏文件。

## 只做这件事

新增（仅这些路径）：

- `tradingagents/dataflows/social/__init__.py`
- `tradingagents/dataflows/social/contracts.py`
- `tradingagents/dataflows/social/archive_schema.py`
- `tests/test_social_contracts.py`

要求：

1. 七种 status：`available/partial/empty/refused/failed/timeout/not_applicable`
2. 时间字段名必须是：`published_at` / `source_updated_at` / `first_seen_at` / `snapshot_at` / `ingest_at`
3. 资格 API 参数**不得**包含 `ingest_at`
4. 契约中**不得**出现把 `add_ts` 解释为正文时间的别名；**禁止**实现 `content_observed_at=add_ts` / `metric_observed_at=last_modify_ts`
5. TDD：先写可失败测试，再最小实现
6. 一个 commit：`feat(social): define raw and bundle contracts`
7. 推远程分支；交付精确 SHA + 定向 pytest 原文

## 禁止

- 改 `data_collector.py`、`frontend/**`、`AGENTS.md`、`work/h1b_gates_report.json`
- 改 H1b flag、辩论轮次、snapshot refusal
- `git add .`
- 做 Task 3+（导入器等）——本卡只到 contract/schema

## 验收

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_social_contracts.py -vv
```

评论交付：分支名、精确 SHA、父提交是否祖先含 `aa41f449`、pytest 原文。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
