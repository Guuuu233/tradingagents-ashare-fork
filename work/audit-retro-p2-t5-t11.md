# 回顾审计报告：P2-T5…T11（`ca4747c..68ae241`）

- 日期：2026-08-30
- 主干 tip：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
- 基线（不含）：`ca4747c8653afe1de83f410b666537e511a26b0f`
- Cursor 回归：`178 passed`（`tests/test_social_*.py` + collector/report/prompts/graph 相关）
- Multica 独立审核员：DAV-510（并行进行中）
- **总评：❌ 未通过「全部没有问题」——发现 High，施工继续暂停**

## 范围

跳过独立审核员即合入的 7 个 commit：T5 archive provider → T11 analyst separation（约 +6482 行 / 30 文件）。

## High

### H1 — lookback 窗外无合格帖被标 `refused` 并进入 structural `data_gaps`

- **契约**：§5.5 `empty` 不写失败 ledger；`refused` 仅非法/未来 as_of 或无历史快照。
- **实锤**：archive 有 `snapshot_at <= cutoff` 的旧帖，但 `published_at` 早于 lookback → `status=refused` + `observed_after_cutoff_excluded` → `build_social_failure_ledger` 写出 structural gap。
- **位置**：`provider.py:667-676`；`collector.py:107-118`
- **期望**：`empty` + `social_empty`（或等价），**无**失败 ledger。

## Medium（已知残留 / 可排期）

| ID | 摘要 |
|---|---|
| M1 | `fetch_records` 过长（~300 行） |
| M2 | 缺失 `ingest_run` 时硬编码 `crawler_commit` |
| M3 | `ingest_at` 字符串 tie-break（未解析 datetime） |
| M4 | `_log_state` 漏 `social_data_context` |
| M5 | 宽 `except Exception` / 错误码误标 |
| M6 | shadow `source_mode` 与 plan「legacy_proxy」漂移 |
| M7 | aggregator 去重后 0 行标 `partial` 而非 `empty` |

## 已通过的关键项

- D-008：`ingest_at` 不参与资格；非法 as_of 拒绝且不填「今天」
- `disabled` 不打开 archive DB
- active 分析师正文不回退 `get_news`；social `ToolNode([])`
- 默认 `TA_SOCIAL_MODE=disabled`；未提前删 `legacy_proxy`
- empty/insufficient **字面**不会写成 `failed` ledger（H1 是误用 `refused`）

## 施工门禁

在 H1 修复并经 **独立审核员 + Cursor「准予合入」** 落主干前：

- 禁止推进 P2-T12 FF（DAV-507/509 保持 blocked）
- 禁止开 T13+
- 禁止部署
