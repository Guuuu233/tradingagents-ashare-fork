开工。基线 `b2f7b77bca19a9b50f0556989f06553c5b15404f`。只做巨潮 AKShare 公告/IR **标题级元数据**。

必须调用 `stock_zh_a_disclosure_report_cninfo` 与 `stock_zh_a_disclosure_relation_cninfo`。`canonical_event_id=cninfo:{announcementId}`；1.18.30 会丢掉列，从 URL query 取；取不到就 null，禁止标题哈希。`KeyError` ≠ 确认无公告。不改 `get_news`、聚类、manifest、PDF、Tushare。一个 commit，push origin，评论 40 位 SHA。禁止 FF/部署。

[@资深开发2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc)
