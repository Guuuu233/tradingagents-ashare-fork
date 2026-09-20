## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `15764beb0955fc5f8b05f0f8cffd26fb7fa6cbaf`  
**父 tip：** `d4d145fae714a21bd919fad3ad66dba7fa1ae852`（与 C-09 同父；先 FF `d56232f`，再 cherry-pick 本提交，blob 须一致）  
**分支：** `origin/agent/2/a3830afc78c6`

DAV-634 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav632-15764be`：仅新增 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md`；`pytest tests/test_news_event_coverage.py` **32 passed**。主源仍是巨潮；旁证不发明 `canonical_event_id`；空表不得写成确认无公告；不接线 `anns_d`。残留：markdown 行尾空白。

**准予合入。** cherry-pick。禁止 merge。禁止部署。
