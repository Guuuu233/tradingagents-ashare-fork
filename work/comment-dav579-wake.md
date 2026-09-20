## Cursor 派工唤醒 — DAV-579 Track A14

基线 tip：`41c5ed3adfa34609fd9ea87408445608e9c67cdf`  
说明：`work/issue-a14-gates-ensure-schema.md`

本地未迁移库上 tip 的 `verify_h1b_gates --db-path` 因缺 `reports.industry` 崩溃。请在查询前调用 `_ensure_report_schema`；附可失败测试。push → `in_review`。

**不准予部署。** 勿开加权。
