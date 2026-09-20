# P0-2b 返修：七分析师报告路径不得把 unverified as_of 核成 verified

## 为什么不开合入

Cursor 独立复审 **DAV-467** @ `80c099f61141909fa0ec074b9bf3d9cde421cc38`：**不准予合入。**

独立审核员 PASS 与项目主管「建议合入」不够（D-010）。

已复现 High（Cursor 在该 SHA 上跑出）：

```
claim = "归属于母公司所有者的净利润 11223344.55"
seven_reports = {"fundamentals_report": "基本面：归属于母公司所有者的净利润 11223344.55，盈利稳定。"}
source_provenance.fundamentals = available_unverified_as_of / unverified
→ status == verified   # 生产路径
```

空 `seven_reports` 时确实不是 verified（现有测试只锁了这条弱路径）。辩论/总监采用证据走的是报告匹配（`evaluate_single_evidence` 步骤 3.1–3.3），在 provenance 检查之后、且 **不** 看 `unavailable_sources`。

`str(market_data_context)` 泄漏已堵；**采用点仍开着。**

## 基线

- 继续分支 `agent/dev2/p0-2b-unverified-as-of`
- 父提交必须是 `80c099f61141909fa0ec074b9bf3d9cde421cc38`
- 不要从主干 `bb39ad4` 另开平行分支，不要 `_v2`
- 不要 FF、不要部署、不要碰社交

## 只改

- `tradingagents/agents/utils/evidence_verifier.py`
- `tests/test_ohlcv_fail_closed_verdict.py`（扩展现有 `test_evaluator_rejects_claims_quoting_unverified_fundamentals`，或同文件新测）

禁止改 `data_collector.py`（provenance 戳字段已够）、禁止新文件、禁止 golden 改期望除非证明旧期望在采用无日期证据。

## 行为

在所有从 **七份报告** 返回 `STATUS_VERIFIED` 的路径（精确子串 / 单行 / 多行聚合 / 跨报告聚合）之前：若该 `role_key` 映射到的 provenance source 落在 `_extract_unavailable_sources` 结果里（含 `available_unverified_as_of` 与 `provenance_status in {unverified, refused, future}`），**不得** 返回 verified。

最低映射（不要发明未证实的社交/宏观大表）：

| report key | provenance sources |
|---|---|
| `fundamentals_report` | `fundamentals`, `balance_sheet`, `income_statement`, `cashflow` |
| `market_report` | `stock_data` |
| `volume_price_report` | `stock_data` |
| `news_report` | `news` |

被挡住时：跳过该报告匹配，继续看其他报告；全部挡住则落到 unsupported。**不要**因此把未点名数据源的数值引用标成 `is_fatal` 严重幻觉（点名 `fundamentals` 的既有 fatal 路径保持）。

## 测试（必须在 `80c099f` 上先红）

现有空 reports 用例保持绿。新增：

1. 上面的 High 复现：`fundamentals_report` 含同一数值 → **不得** verified
2. 对照：同一 claim + `fundamentals` provenance `available` + `as_of` + `verified` → 可以 verified
3. `stock_data` 为 `available_unverified_as_of` 时，`market_report` 含该日线事实的 claim 不得 verified

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_ohlcv_fail_closed_verdict.py \
  tests/test_data_collector.py \
  tests/test_evidence_verifier_fairness.py \
  tests/test_financial_as_of.py -k 'not smoke' \
  -q --tb=short
```

## 交付

- 同一分支新 commit 的完整 40 位 SHA，已 push
- `git diff --stat` 相对 `80c099f`
- 精确 pytest 行（passed/failed）
- 不要自行合主干。完工只 @项目调度助手 一次写明 SHA。等 Cursor 同时写出 SHA 与「准予合入」。不准予部署。
