## Cursor 派工唤醒 — DAV-566 Track A13

基线 tip：`98fe5d199e8874ae829d2b492882d82339c836f0`  
说明：`work/issue-a13-industry-column.md`

目标：`reports.industry` SQL 列真正存在并可写；与 JSON `instrument_context.industry` 同步。改原路径。完成后 push → 40 位 tip → `in_review`。

与 A0 / R1-R3 / Ops 卡文件不冲突，可并行。**不准予部署。** 勿对生产库执行迁移。
