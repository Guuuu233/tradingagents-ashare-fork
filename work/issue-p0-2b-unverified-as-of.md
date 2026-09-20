# P0-2b：无 ISO as_of 的证据不得被采用为方向 claim

## 目标

P0-2a 已禁止 `cached_at` 冒充数据日。下一刀是 **采用点**：`available_unverified_as_of` 仍表示「表拉到了但没有可验证 ISO 日期」，**不得**改回「数据获取失败」缺口（A1 契约必须保持）。但它也 **不得** 被证据核验器当成已验证来源去支撑多空 claim。

不是全量 EvidenceRecord 迁移，不是 P0-3 period_kind，不是社交，不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `bb39ad4bfab3e715fcefd610a4fee3172470ed26`
- 从该 SHA 开隔离分支，例如 `agent/<you>/p0-2b-unverified-as-of`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`

## 权威

- DECISIONS.md D-009 / D-010
- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-2：解析失败/缺日期必须拒绝或标 unknown；旧文本只能 `legacy_unverified`，不能当高质量方向证据

## 已复现（主干 `bb39ad4`）

1. `_build_source_provenance` 对「有财务字段+数值、无 ISO 日期」写 `status=available_unverified_as_of`，`actual_as_of=None`（`tests/test_data_collector.py::test_financial_numeric_payload_without_iso_as_of_is_not_fetch_failure`）。这是 A1，保留。
2. 该条目 **没有** `provenance_status`。
3. `evidence_verifier.UNAVAILABLE_STATUSES` 不含 `available_unverified_as_of`。`_extract_unavailable_sources` 不会把该源标成不可用，claim 仍可能被核成 verified。

## 行为契约

### A. `_build_source_provenance`（原函数，禁止 `_v2`）

每条 provenance 必须有 `provenance_status`，取值仅限：`verified | unverified | refused | future`。

| 现有 status / 条件 | provenance_status |
|---|---|
| `available` 且 `actual_as_of` 非空且 `<= requested_as_of` | `verified` |
| `available_unverified_as_of` 或 `available` 但 `actual_as_of` 为空（含 realtime） | `unverified` |
| `unavailable` / `failed` / `refused` / `timeout` / `error` | `refused` |
| 若出现 `actual_as_of > requested_as_of`（不应发生；防御） | `future`，且不得当 available |

**禁止**：把 `available_unverified_as_of` 改成 `unavailable` 或写入 `【数据获取失败】`。A1 测试必须仍绿。

**禁止**：新增无人调用的 `EvidenceRecord` 文件。字段打在现有 provenance dict 上。

### B. `evidence_verifier`

采用证据时：`provenance_status in {unverified, refused, future}` 的 source 与 `UNAVAILABLE_STATUSES` 同等，**不得**把引用该源的 claim 标成 verified。不要把 A1 的 provenance `status` 改成 failed。

`is_daily_ohlcv_unavailable`：`available_unverified_as_of` 或缺少 `as_of` 已应 fail-closed；加测试锁住，不要放宽。

## 允许修改

- `tradingagents/graph/data_collector.py`
- `tradingagents/agents/utils/evidence_verifier.py`
- `tests/test_data_collector.py`
- `tests/test_ohlcv_fail_closed_verdict.py`
- 若必须碰到现有 verifier 单测文件：`tests/test_evidence_verifier_fairness.py`（只加用例，不改 golden 期望除非证明旧期望在采用无日期证据）

## 禁止修改

- 社交 `tradingagents/dataflows/social/`、DAV-460
- P0-3 / P0-4 / P0-5、前端、schema 大迁移
- `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 主干快进、部署

## 测试（TDD）

先写在 `bb39ad4` 上会失败的测试，再改产品代码。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_data_collector.py \
  tests/test_ohlcv_fail_closed_verdict.py \
  tests/test_financial_as_of.py -k 'not smoke' \
  -q --tb=short
```

必须断言：

1. A1：`test_financial_numeric_payload_without_iso_as_of_is_not_fetch_failure` 仍绿，且这些条目 `provenance_status == "unverified"`
2. `available` + 非空 `actual_as_of` → `provenance_status == "verified"`
3. `unavailable` 缺口 → `provenance_status == "refused"`
4. evaluator：market_data_context 里 fundamentals 为 `available_unverified_as_of` 时，引用该源数值的 claim **不得** 为 verified
5. `is_daily_ohlcv_unavailable` 对 `available_unverified_as_of` 且无 as_of 为 True

既有 eight-interface provenance 测试仍绿。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `bb39ad4`
- 精确 pytest 输出（passed/failed，不要只报「全绿」）
- 不要写「彻底修复」；不要自行合主干
- 完工后只 @项目调度助手 一次并写明 SHA。合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」。不准予部署。
