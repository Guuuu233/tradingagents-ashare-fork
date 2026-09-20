Cursor 独立复审 DAV-499 / P2-T8（返修后）。

候选 SHA（完整 40 位）：`a375bdc9cf3d07584eb6c28c637bde9bca867876`
父提交：`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`（线性，无 merge）
分支：`agent/dev2/p2-t8-data-collector-social`（相对旧候选 force-update 为单 commit，可接受）

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_data_collector_social_integration.py \
  tests/test_data_collector.py \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py
```

结果：**113 passed**。

契约核对：
- `_fetch_all` 源码无社交接线；社交在 `collect()` 市场 `_fetch_all` 之后、独立 1-worker 超时
- `except (TimeoutError, FuturesTimeoutError)` → `status=timeout` + `social_archive_locked`
- `news` / `social_data_context` 独立；`market_attention` 含 zt_pool/hot_stocks 的 status/as_of
- 白名单 3 文件；未触脏文件；未改 Graph/API/analyst；未删 legacy

残留（不阻塞）：通用 `except Exception` 仍粗映射为 `social_archive_missing`。

**准予合入** `a375bdc9cf3d07584eb6c28c637bde9bca867876` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要开 Task 9（另卡）。不要删 legacy_proxy。
