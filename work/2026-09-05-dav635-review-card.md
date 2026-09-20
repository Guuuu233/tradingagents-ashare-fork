# 独立审核（只读）：DAV-635 R2 夹具 SHA `5e254acae668bc0b391f62624a8f3057c732e683`

**过程事故：** 实现者把该 SHA 直接推上了 `origin/codex/dav-4-p2a-trunk`（父 `58685a11019f5590027b6c922f4ae77ad019a5b6`），绕过 D-010。本卡仍必须只读复审；**禁止再 FF、禁止 revert、禁止部署**。Cursor 尚未「准予合入」。

**候选 SHA（exact）：** `5e254acae668bc0b391f62624a8f3057c732e683`  
**分支对照：** `origin/agent/1/c524142c01c4` 与主干 tip 目前同 SHA。

## 审核要点

1. 白名单仅：`tests/fixtures/decision_semantics/r2_foxconn_fixture.json`、`manifest.json`、`tests/test_decision_semantics_fixtures.py`。禁止 `tradingagents/`。
2. 顶层键与 R1 对齐；缺字段为 `missing`/`unavailable`，不得用 0 或当天日期冒充。
3. `symbol=601138.SH`，`trade_date=2026-07-30`；`calibration_eligible=false`。
4. 既有新闻 PIT：`hit_count==1`、`unverifiable_count==2`、`future_rejected_count==1`。
5. 不得声称工业富联案例已修复。
6. 隔离：`env -u PYTHONPATH .venv310/bin/pytest tests/test_decision_semantics_fixtures.py tests/test_news_event_coverage.py`

书面 ✅通过 / ❌打回，含路径行号。独立审核 PASS ≠ 准予合入。
