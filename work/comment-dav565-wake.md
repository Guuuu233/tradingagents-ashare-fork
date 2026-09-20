## Cursor 派工唤醒 — DAV-565 Track A0

基线 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`  
说明：`work/issue-a0-frontend-v2.md`；总纲：`work/2026-09-02-audit-followup-dispatch.md`

只改 `frontend/src/services/api.ts` 加 `config_overrides: { v2_debate_enabled: true }`。单 commit。push 后写完整 40 位 SHA → `in_review`。

**不准予部署。** 勿开加权。勿 `git add .`。
