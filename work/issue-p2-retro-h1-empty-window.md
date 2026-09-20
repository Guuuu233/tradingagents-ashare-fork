# 返修：P2 回顾 H1 — lookback 空窗勿标 refused（基线 68ae241）

## 背景

回顾审计（`work/audit-retro-p2-t5-t11.md`）High：候选快照存在但全部因 lookback/`published_at` 资格失败时，provider 返回 `refused` + `observed_after_cutoff_excluded`，ledger 写入 structural gap。应视为 **empty**，不得进 `merge_data_gaps`。

## 基线

- 主干：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
- 新建隔离分支，例如 `agent/dev2/p2-retro-h1-empty-window`
- 不要 FF、不要部署、不要动 T12 返修分支合入

## 白名单

1. `tradingagents/dataflows/social/provider.py`（`fetch_records` 空结果分支）
2. `tradingagents/dataflows/social/collector.py`（仅当 ledger 映射需同步；优先保持 §5.5：empty 无条目）
3. `tests/test_social_archive_provider.py` 和/或 `tests/test_social_as_of_guard.py` / `tests/test_social_data_collector.py`（TDD）

禁止：删 legacy、改辩论轮次、部署、顺手修全部 Medium（M1–M7 另卡，除非本卡最小必要）。

## 行为契约

| 场景 | 期望 status | reason | ledger |
|---|---|---|---|
| 符号在库无任何记录 | `empty` | `social_empty` | 无 |
| 有快照但全部 `snapshot_at > cutoff` | `refused` | `social_no_historical_snapshot` | structural（保持） |
| 有 `snapshot_at <= cutoff` 候选，但全部资格失败（含 lookback 外 `published_at`、`first_seen_at > cutoff` 等） | `empty` | `social_empty`（reason_codes 可附带排除计数类码，但 **status 必须 empty**） | **无** |
| 非法/未来 as_of | `refused` | 既有码 | structural（保持） |

## 测试

先写失败用例再改代码。建议：

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py
```

## 交付

单 commit；完整 40 位 SHA；`in_review` 后交独立审核员。不要 @调度助手。
