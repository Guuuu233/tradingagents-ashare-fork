## Cursor 隔离复测 — DAV-567 / SHA 5b63375

**候选**：`5b633754df0ffbc4191aea97a388815d2ddf199d`  
**父**：`98fe5d199e8874ae829d2b492882d82339c836f0`

白名单：仅 `tests/fixtures/decision_semantics/*` + `tests/test_decision_semantics_fixtures.py`。无产品代码。

隔离复测：`pytest tests/test_decision_semantics_fixtures.py` → **10 passed**。

等独立审核同 SHA 后，再「准予合入」。FF 须与并行卡排队保持线性。

**不准予部署。** 夹具齐备 ≠ 宣称线上案例已修（仍受其它闸约束）。
