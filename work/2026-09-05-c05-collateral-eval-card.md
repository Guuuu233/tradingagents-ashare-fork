# C-05 旁证切片 1（只读）：forecast / repurchase / disclosure_date

**基线：** 主干须已含 C-05d `b42bb50893ec135c9f365f98f12255a18728f696`。  
**一个关注点：** 冻结这三张私有网关表如何作为**结构化旁证**挂到巨潮主源，而不是替代 CNINFO AKShare。  
**禁止：** 改 `tradingagents/`、接线 `anns_d`、改聚类/PDF、部署、把旁证空表写成「确认无公告」。

依据 `work/2026-09-05-tushare-private-gateway-matrix.md` 与已合入巨潮 envelope 契约。只新增 `work/2026-09-05-c05-collateral-forecast-repurchase-disclosure.md`。

必须写清：每张表的日期字段与 PIT；与 `canonical_event_id` 的关联只能是旁证不能发明巨潮 id；`403`/空表/缺列如何记入 gap。禁止打印 token。一个 commit，push，40 位 SHA。禁止 FF/部署。
