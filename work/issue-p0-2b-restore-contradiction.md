# P0-2b 返修2：撤回 3.4 对「预期/目标」的矛盾检测豁免

## 为什么仍不准予合入

Cursor 独立复审 **DAV-468** @ `c10fed826ebb935256d5ae1475a2e339763bea3c`。

上一刀 High（`fundamentals_report` 含 unverified 数值仍 verified）**已堵住**。相关套件 Cursor 复跑 **115 passed, 3 deselected**。golden 两例改 unsupported 有依据：ledger 已把 fundamentals 等标 unavailable，只是旧报告匹配路径无视 ledger。

**不准予合入**，因为同 commit 多带了未要求的 3.4 豁免：

```python
is_forward_scenario = any(w in raw_text for w in ("预测", "预期", "目标", "展望", "情景", "未来", "压力测试", "底线"))
if not is_forward_scenario:
    # contradiction check
```

Cursor 复现（fundamentals provenance **verified**）：

- `净利润为 10 元` vs 报告 `净利润 8 元` → `contradicted`（正确）
- `净利润预期为 10 元` → `unsupported`（矛盾检测被关掉）
- `目标净利润 10 元` → `unsupported`（同上）

`预期`/`目标` 是交易 claim 高频词。这不是 P0-2b 采用闸，是削弱事实冲突检测。

golden CASE-015（INV-9）在去掉整段豁免后会变成 `contradicted`（macro 净利 44.65 亿 vs 压力测试底线 60 亿）。不要用关掉「预期/目标」来迁就这一例。

## 基线

- 继续 `agent/dev2/p0-2b-unverified-as-of`
- 父提交必须是 `c10fed826ebb935256d5ae1475a2e339763bea3c`
- 不要平行分支、不要 `_v2`、不要 FF、不要部署、不要碰社交、不要改 `data_collector.py`

## 只改

- `tradingagents/agents/utils/evidence_verifier.py`（删掉 3.4 的 `is_forward_scenario` 整段判断，恢复「只要不是 unavailable 报告就做矛盾检测」）
- `tests/test_ohlcv_fail_closed_verdict.py`（加回归：verified fundamentals 下，`净利润预期为 10 元` 与 `净利润为 10 元` 对 `净利润 8 元` 都必须 `contradicted`）
- 若 CASE-015 因此变 `contradicted`：只改 `tests/golden/evidence_sentences_20260823.json` 该条 `expected_status`/`reason`/`is_positive`，reason 写明是 3.4 把压力测试底线与报告净利比成冲突，**不是** unverified 采用闸

允许 `tests/test_evidence_verifier_fairness.py` 仅当 pos 计数断言必须跟着 CASE-015 调整。

## 禁止

- 再发明「情景/预测/目标」豁免列表
- 放宽 `_is_report_unavailable` / `REPORT_TO_PROVENANCE_SOURCES`
- 改 P0-2b 已绿的 Case C/D

## 测试

必须先在 `c10fed8` 上让新回归红，再删豁免。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_ohlcv_fail_closed_verdict.py \
  tests/test_evidence_verifier_fairness.py \
  tests/test_data_collector.py \
  tests/test_financial_as_of.py -k 'not smoke' \
  -q --tb=short
```

## 交付

完整 40 位 SHA；`git diff --stat` 相对 `c10fed8`；精确 pytest 行。不要自行合主干。完工只 @项目调度助手 一次。等 Cursor 同时写 SHA 与「准予合入」。不准予部署。
