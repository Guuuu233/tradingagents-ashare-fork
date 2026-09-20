# e30f312 发布后运行态核验

## 版本与服务

- 远端主线：`e30f312c968c1e0b82cff38df6c456941eb44f86`
- 8000 `/healthz`：HTTP 200，`commit_sha` 与 `build_identity` 均为 `e30f312c968c1e0b82cff38df6c456941eb44f86`
- 进程：PID `54289`，PPID `1`，工作目录 `/private/tmp/ta-serve-e30f312`
- `/`：HTTP 200；`/v1/reports?limit=1`：HTTP 200；伪造发布探针：HTTP 404
- 前端：当前服务目录的 `frontend/dist` 已存在，`index.html` 与 assets 可读

## 回归

合并 `cdd201db` 与修正后的 DAV-1040 候选 `01cdd7de043b8a58cdf9e2ee62faa212d4b87b37` 后，整合提交 `e30f312` 的离线聚合回归为：

`4926 passed / 1 skipped / 6 deselected / 0 failed / 183 warnings / 379.30s`

## 数据库守恒与实际写入

- 生产库逻辑计数：`1410 reports / 794 completed / 616 failed`
- `PRAGMA quick_check`：`ok`
- 发布前备份：`work/tradingagents.db.bak-20260918-034833-deploy-e30f312`
- 备份与当前库逐表比对：报告、用户、令牌、模型、调度、持仓等逻辑数据均一致
- 唯一差异：`historical_cases` 中 `600519.SH`、`trade_date=2026-09-17`、`eval_date=2026-09-18` 的一条待回填记录，语义字段仍为 `actual_change_pct=NULL`、`actual_outcome=【数据缺失】`、`is_error=NULL`，仅 `updated_at` 从 `2026-09-17 18:26:15.177740` 更新为 `2026-09-17 19:48:54.146306`
- 启动日志对应：扫描 1 条、实际回填 0 条、仍缺失 1 条；原因是评估日尚未收盘且行情不可用

## 结论

发布成功，报告数据没有变化，待回填案例没有被提前填充或伪造。但启动回填对相同暂态拒绝产生了一次无语义时间戳更新，已转为 DAV-1048 施工项；在该卡完成前，不能宣称启动回填完全写入幂等。
