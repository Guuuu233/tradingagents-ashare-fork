# P1-F 连板天梯受控发布证据

核验日期：2026-09-14（Australia/Perth）

## 发布对象与前置核验

- 目标主线发布提交：`6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`
- 其中 P1-F 代码候选：`6612aea82e0fb3212d3682c5d09835f529ffec16`
- 线上旧版本：`026349614a3f1b92a95dc06c0515f10ebec193bc`
- 发布副本：`/private/tmp/ta-release-p1f-6cc4e-20260914`
- 旧发布回退点：`/private/tmp/ta-release-p1e-0263496-20260914`

发布前重新回读目标远端，确认主线为 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`。生产服务旧 PID `19944`、工作目录为 P1-E 发布副本；旧版本 `/healthz` 正常。

## 数据库保护

发布前对生产 SQLite `/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db` 建立一致性备份：

- 备份：`work/tradingagents.db.bak-20260914-predeploy-6cc4e1`
- 生产库和备份均为 `quick_check=ok`
- 两者 reports 计数均为总数 `1409`、completed `793`、failed `616`
- 生产库发布前后大小均为 `285339648` bytes
- 生产库发布前后 SHA-256 均为 `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`
- SQLite 一致性备份 SHA-256 为 `df628f4293069a820ef69a136a1acbb0e7a517f6638dd24a9faeea30f5b51b9e`

## 受控切换

1. 在 8001 预启动发布副本，启动身份精确为 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`。
2. 8001 预启动回读 `/healthz` 为 HTTP 200，完整 `commit_sha` 和 `build_identity` 均匹配；provider health 为 `ok`，只读 K 线请求返回两根蜡烛，social 状态为 `disabled`，网页入口 HTTP 200。
3. 旧 8000 PID `19944` 以 SIGTERM 优雅停止。
4. 发布副本切换到 8000，当前 PID `29528`，父进程 `71186`，工作目录为 `/private/tmp/ta-release-p1f-6cc4e-20260914`。
5. 临时 8001 实例已关闭；旧发布副本仍保留。

## 上线后只读烟测

- `/healthz`：HTTP 200，`commit_sha=6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`，`build_identity` 同 SHA，executor 为 `queued=0 / threads=1`。
- `/v1/dataproviders/health`：`status=ok`，预期 provider 均 healthy。
- `/v1/market/kline?symbol=600519.SH&start_date=2026-09-08&end_date=2026-09-09`：HTTP 200，返回 2026-09-08 和 2026-09-09 两行。
- `/v1/social-data/status`：`mode=disabled`，没有运行记录。
- `/`：HTTP 200；未知 API 路径：HTTP 404。
- 数据库再次 `quick_check=ok`、`integrity_check=ok`，reports 仍为 `1409/793/616`，SHA 仍为 `94d2f674...`。

## 边界

本次是代码发布和只读运行核验，不调用真实天梯上游、不启动真实分析、不写生产报告、不采集真实社交数据、不启用信用加权、不改历史。P1-F 的来源、日期、失败语义和独立字段由 DAV-908 同 SHA 复审及 RT-FULL 覆盖；真实业务样本和生产 trace/report/readback 仍未产生。
