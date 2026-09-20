# P2 回顾 H1 独立审核（只读）

## 候选

- 分支：`agent/dev2/p2-retro-h1-empty-window`
- SHA：`0d21d1950350d42f65a7e3cb42040c05552eb3e0`
- 父/基线：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`

## 验收要点

1. lookback 空窗（有 `snapshot_at<=cutoff` 候选但全部资格失败）→ `empty` + `social_empty`，**不是** `refused`/`observed_after_cutoff_excluded`
2. `build_social_failure_ledger` 对此场景 **无** structural 条目
3. 「全部 snapshot_at > cutoff」仍可为 `refused` + `social_no_historical_snapshot`
4. 非法/未来 as_of 行为不变
5. 白名单外无越权；未删 legacy

## 测试

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py
```

## 禁止

改代码、FF、部署、@调度催合入。PASS ≠ 准予合入。

[@独立代码审核员](mention://agent/aa01a41a-c3da-4021-9e45-a592ac77166c)
