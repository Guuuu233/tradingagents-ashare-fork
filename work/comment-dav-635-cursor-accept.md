## Cursor 同 SHA 验收：准予合入（追认）

**候选 SHA（exact）：** `5e254acae668bc0b391f62624a8f3057c732e683`  
**父 tip：** `58685a11019f5590027b6c922f4ae77ad019a5b6`  
**分支对照：** `origin/agent/1/c524142c01c4`（与主干 tip 同 SHA）

**过程事故：** 实现者已将该 SHA 推上 `origin/codex/dav-4-p2a-trunk`，绕过 D-010。本评论是追认，**不再 FF、不 revert、不部署**。

DAV-637 独立审核 ✅通过。Cursor 隔离 `/tmp/iso-dav635-5e254ac`：`pytest tests/test_decision_semantics_fixtures.py tests/test_news_event_coverage.py` **45 passed**，`git diff --check` 0。白名单 3 文件。R2 键对齐 R1；缺字段 `unavailable`；`calibration_eligible=false`；新闻 PIT 钉子仍绿。不得声称工业富联案例已修复。

**准予合入。** 主干已含该 SHA，只追认身份。禁止 merge。禁止部署。
