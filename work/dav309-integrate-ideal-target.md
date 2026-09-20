# DAV-309 集成部署：美股真值 + 产业链 fail-closed + Tushare 优先 + 质量闸

候选组合树：`/tmp/dav-ideal-int`，基线 `ab3cda62`，候选 TIP 待全量回归结束后填写。

## 已完成独立终审

- `cf25cc0` 美股三大指数：DAV-306 PASS
- `49a4003` 产业链 fail-closed：DAV-304 PASS
- `4427319` Tushare 产业链：DAV-308 有条件通过，唯一阻塞为旧测试断言；组合树 `7a21a59` 已修
- `898bf39` 报告质量闸：DAV-307 PASS

## 硬闸

1. 组合树全量 pytest 必须 0 failed。
2. 推送只能 FF `target/codex/dav-4-p2a-trunk`，禁止 merge commit。
3. 部署前 active reports=0；保护宿主未跟踪文件，禁止 reset/clean。
4. 服务重启后 healthz SHA=组合树 TIP。
5. 真实 smoke：
   - `get_global_indices('2026-08-21')` 有 SPX/IXIC/DJI 1d；
   - `IndustryLinkageProvider` 对新能源汽车碳酸锂返回 source=tushare、actual_as_of<=requested；
   - 正确账户京东方 completed，宏观含美股/恒生/日经/KOSPI，宏观与基本面含【产业链联想数据】，ledger 可审计质量闸；
   - 持久 3/1、providers/role_bindings 不变。

禁止 @项目调度助手。
