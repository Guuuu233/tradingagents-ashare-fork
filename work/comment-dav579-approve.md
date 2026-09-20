## Cursor 隔离复测 + 准予合入 — DAV-579 Track A14

**准予合入** 候选完整 40 位 SHA：`03cfb47c2f01981a6f80254fba8ac6a857b4d987`  
父：`41c5ed3adfa34609fd9ea87408445608e9c67cdf`（= 当前主干 tip，可线性 FF）

证据：
- 独立审核 DAV-581：✅ 通过（同 SHA）
- Cursor 隔离：`test_h1b_gates` + `test_tplus5_shadow_backfill` + `test_report_industry_persistence` → **97 passed**
- 白名单：`scripts/verify_h1b_gates.py`、`backfill_tplus5_shadow.py`、`recalculate_weekly_metrics.py` + 对应测试
- 本地库复跑门槛：不再因缺 `industry` 崩溃

请运维线性 FF `codex/dav-4-p2a-trunk` 至上述 SHA。  
**不准予部署。** 不开加权。
