红队覆盖复核 DAV-857 已完成，结论为 `NEEDS_ADD`。实施卡面已补入 RT-13～RT-16，且 RT-10 已收窄为“既有旧行在任务启动时拦截”。

当前实施 run 需要纳入四条新增强制场景：`AMBIGUOUS` 三态的保存/运行时分层处置、`cluster_id`/`independent_cluster_count` 单独出现的零容忍、研究经理直接绕过 API 的最后一道防线、`migrate_legacy_prompt` 的违规拦截与原子回滚。不要把原 RT-1～RT-12 的旧版本当成完整清单；不新增重复 run，不改白名单，不合入/部署。
