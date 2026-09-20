Cursor 独立复审 DAV-493 / P2-T5。

候选 SHA（完整 40 位）：`50ec109fe9992db11c00a98a3a07f3ad760bf3df`
父提交：`ca4747c8653afe1de83f410b666537e511a26b0f`（线性，无 merge）
分支：`agent/dev2/p2-t5-archive-provider`

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

结果：**72 passed**（与交付自称一致）。

白名单：5 文件，`+1485/−1`；未触脏文件；未继承 `BaseMarketDataProvider`；未进市场 registry；只读 `mode=ro` + `query_only`；D-008 硬钉（历史日日末 / 当日 now / first_seen 后补排除 / ingest_at 不计资格 / 最大 snapshot_at≤cutoff）测到。

残留（不阻塞合入，后续可收）：
1. `fetch_records` 过长（>60 行规范）；Task 6 前可拆。
2. `parse_iso_datetime` 用 `except Exception` 级联（应更具体）。
3. `ingest_run` 缺失时回退硬编码 `d6f7c5bb…` 作 `crawler_commit`（罕见；勿发明 provenance，宜空串或跳过该行）。
4. 候选快照并列时用 `ingest_at` 字符串作 tie-break（宜只用 `snapshot_id`）。

**准予合入** `50ec109fe9992db11c00a98a3a07f3ad760bf3df` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要扩 Task 6。不要唤醒 DAV-460。
