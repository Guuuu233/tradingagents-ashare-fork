# 社交 shadow 配置发布门禁修正（2026-09-18）

## 现场发现

- 当前线上 `TA_SOCIAL_MODE=shadow`、`TA_SOCIAL_PLATFORMS=xhs,dy`、`TA_SOCIAL_ARCHIVE_DB=/Users/davidliu/Documents/TradingAgents-AShare/data/social_archive.db`。
- 宿主项目 `.env` 原本没有这三项；服务 worktree 的 `.env` 有这三项。
- 因此下一次按旧脚本从宿主 `.env` 重建 serve worktree 时，确实可能把社交配置丢掉，导致 shadow 降为 disabled。
- 归档库当前 `social_record_snapshots=357`、`social_entity_mentions=93`；最近 xhs/dy ingest 均 completed 且 0 rejected。

## 修正

`work/deploy-4b540b0.sh` 已增加：

1. 发布前从当前 8000 进程提取三项非敏感配置；
2. 缺失、mode 不是 `shadow`、平台不是 `xhs,dy` 或归档库不存在时立即 fail-fast；
3. 重建 serve worktree 后显式写入三项配置；
4. 服务启动后从新 PID 回读并强校验 `shadow`、`xhs,dy`、archive path；
5. `bash -n` 与 `git diff --check` 已通过。

后续运行态核验发现旧 PID `52913` 在 13:20:47 正常 shutdown、8000 短暂无监听；未发现数据库损坏或配置错误。曾用同一 SHA `4b540b0`、shadow/xhs,dy/archive 配置恢复为临时 PID `65820`；该 PID 随 Hermes 临时会话退出，后已改用独立脱离方式恢复为 PID `68658`。恢复后 `/healthz` 正常，`/v1/social-data/status` 回读 `mode=shadow / status=operational`，xhs/dy 均 operational，`analysis_availability.available=true`；归档库 `PRAGMA quick_check=ok`。active 未开启，未读取/导出/保存 Cookie，未改生产交易数据。

## 后续稳定性与运行态门禁复核

- PID `65820` 曾随 Hermes 临时会话退出；已改用 fork + `setsid` 脱离启动，当前 PID `68658`、`PPID=1`。
- 当前 `/healthz` 精确回读 `4b540b0c9b08d77a12ce7d08cfdf0095288cd350`。
- 当前 `/v1/social-data/status` 实测为 `shadow / operational`；xhs、dy 和 `analysis_availability.available` 均通过。
- 归档库再次 `PRAGMA quick_check=ok`，快照 357、实体映射 93。
- 发布脚本新增的接口门禁和归档库门禁已通过 `bash -n`、尾部空白检查；当前服务未因门禁补丁再次重启。
