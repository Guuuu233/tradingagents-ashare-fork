## Cursor 派工唤醒 — DAV-580 Ops 本地 industry

基线 tip：`41c5ed3adfa34609fd9ea87408445608e9c67cdf`  
说明：`work/issue-ops-local-industry-migrate.md`

仅本地 `data/tradingagents.db`：备份 → dry-run → `backfill_report_industry` 实写（会 ensure schema）→ 复跑门槛。完成后 `done`。

**禁止**生产库/部署/开加权。
