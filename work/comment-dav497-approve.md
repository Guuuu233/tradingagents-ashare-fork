Cursor 独立复审 DAV-497 / P2-T7。

候选 SHA（完整 40 位）：`ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd`
父提交：`bdefe4f85f61d5308280846e44ff69ca93bc3116`（线性，无 merge）
分支：`agent/dev2/p2-t7-social-collector`

隔离 worktree 复跑（宿主 `.venv310`，精确 SHA）：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_data_collector.py \
  tests/test_social_aggregator.py \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_contracts.py \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py
```

结果：**105 passed**（与交付自称一致）。

白名单：5 文件，`+1039`；未触脏文件；未改 `data_collector.py` / 辩论轮次；默认 `mode=disabled`；disabled 不调 provider；空/相对/缺失路径 fail-closed；非 A 股 not_applicable；canary 未命中不 active；§5.5 ledger 映射；超时配置传入 registry。

残留（不阻塞）：
1. `collector.py` 仍偏长；未知 mode 字符串会落入 shadow/active 路径（建议未知当 disabled——可后续修）。
2. `__init__` 中 `cfg.update(config)` 会混入顶层无关键（当前无冲突键，可接受）。

**准予合入** `ed6a687c1ed77d8b0c0169edd2b92b5cd5e305fd` 到 `codex/dav-4-p2a-trunk`（线性 FF only）。

**不准予部署。** 不要开 Task 8（另卡）。不要删 legacy_proxy。
