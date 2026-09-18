# Social Gate 0 沙箱实证记录

日期：2026-09-18（AWST）

## 范围

本记录只证明独立沙箱中的 Gate 0 真实链路，不代表生产 social archive、shadow、active
或 Gate 2/3 已通过。用户授权了抖音和小红书的受控小样本采集；Cookie 内容没有被读取、
导出、保存到聊天、代码仓库、日志或任务评论。

## 环境

- MediaCrawler：`d6f7c5bb906b6dac40ddf343ef9e26438a3de092`
- 独立环境：`/Users/davidliu/Documents/TradingAgents-SocialSandbox/MediaCrawler/.venv`，Python 3.11.15
- 专用 Chrome profile + 本地 CDP：`127.0.0.1:9222`
- 工作库与 archive 均在 `TradingAgents-SocialSandbox`，权限 `0600`；没有写入 TradingAgents 生产库。

## 首轮采集与归档

查询词为 `贵州茅台`：

- 小红书：20 条笔记 + 183 条一级评论，共 203 条，拒绝 0 条。
- 抖音：14 条视频 + 140 条一级评论，共 154 条，拒绝 0 条。
- archive 导入运行：`1e0604b7-3321-4a94-bad9-d1671eb182b0`（xhs）、
  `af8f1f0d-dba2-42bd-aa0b-2a74a950a2f5`（dy）。

## 运行器修复后的真实验证

候选 `08f40178729da384b6ba0341489c1877328649d4` 已合入
`origin/codex/dav-4-p2a-trunk`；直接父为 `061f007ebf58692024c78772cb21e1429695a569`。
代码审核员对同 SHA 给出“有条件通过”，无阻塞项；定向社交回归为 63 passed / 0 failed。

用修复后的 runner 各跑一次真实进程，均未导入生产 archive：

- xhs 写入 `runtime/runner_xhs_probe.db`：29 条笔记、279 条一级评论，退出码 0。
- dy 复用同一工作库：17 条视频、190 条一级评论，退出码 0。
- `MediaCrawler/database/sqlite_tables.db` 实际为指向该 source DB 的软链；source DB 权限 `0600`。
- pinned MediaCrawler 的实际 `PRAGMA journal_mode` 为 `delete`；两次运行后没有遗留 WAL/SHM sidecar。

## 明确未做

- TradingAgents 8000 服务没有重启，仍保持原运行 SHA。
- 生产 TradingAgents DB 没有写入，`TA_SOCIAL_MODE` 仍为 `disabled`。
- 没有开启 `shadow`，没有开启 `active`，没有执行 Gate 2 的 30 份/10 标的人工抽检。
