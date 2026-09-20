Cursor 独立复审 DAV-495 / P2-T6。

候选 SHA（完整 40 位）：`bdefe4f85f61d5308280846e44ff69ca93bc3116`
父提交：`50ec109fe9992db11c00a98a3a07f3ad760bf3df`（线性，无 merge）
分支：`agent/dev2/p2-t6-sentiment-bundle`

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

结果：**90 passed**（与交付自称一致）。

白名单：4 文件，`+1659`；未触脏文件；无 LLM；空正文不计方向；unknown≠neutral；否定 3 字翻转；半衰期按 `published_at`；单平台 partial+`direction_allowed=false`；不足覆盖 `score=null`/`insufficient`（非 0.0）；`ingest_at` 不进 content/metric_as_of；provider failed/empty 映射正确；cutoff 后 likes 端到端隔离测到。

残留（不阻塞）：
1. `classifier.py`/`aggregator.py` 体量仍大（后续可拆）。
2. `aggregate(..., platforms=)` 参数未使用。
3. 匿名作者整桶计 1（偏保守，fail-closed 可接受）。

**准予合入** `bdefe4f85f61d5308280846e44ff69ca93bc3116` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要开 Task 7（另卡）。不要删 legacy_proxy。
