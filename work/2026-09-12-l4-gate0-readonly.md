# L4 / B-01 Gate 0 环境核实（只读）

核验时间：2026-09-12（UTC+8）

## 基线与边界

- 远端主干 `codex/dav-4-p2a-trunk`：`41b77dc7a0871a849744b8013db3290700ccc883`（`git ls-remote`）。
- 运行服务 `/healthz`：`70b5b47bcff0618e0db1258a438b0875440d4ac5`，因此服务尚未包含 L1/L2/L3。
- 调度 daemon：`running`，PID `47460`（`multica daemon status`）。
- 主工作树当前是带用户未提交文件的旧 agent 分支 `4fa76815d5aa7d1cfab9942c8f9a9606034c279d`，本核验未把它当作代码施工基线；代码契约以 detached worktree 的 `41b77dc...` 为准。

本项只读核验。没有启动 MediaCrawler，没有调用 `/crawler/start`，没有读取 Cookie，没有访问外网采集，没有写入 source DB 或 social archive，也没有重启或部署服务。

## 核验结果

| 项目 | 实测结果 | 证据边界 |
|---|---|---|
| MediaCrawler 目录 | 在 `/Users/davidliu/Documents`、`/Users/davidliu/multica_workspaces_desktop-api.multica.ai`、`/Users/davidliu/multica_workspaces` 下按 `*MediaCrawler*` / `*media-crawler*` 目录名搜索，未找到 | 仅能证明上述搜索根下未找到，不能扩大为整机绝对不存在 |
| MediaCrawler 版本 / SHA | 无本地安装目录，无法回读实际版本；代码契约要求 `d6f7c5bb906b6dac40ddf343ef9e26438a3de092` | 文档中的 pinned SHA 不是实际检出证明 |
| 运行任务 | 进程扫描仅见 `uvicorn api.main:app`，未见 MediaCrawler 进程或 crawler 控制任务 | 未调用控制 API，不能据此证明未来任务状态 |
| source DB | 上述搜索根下未找到 `social_archive.db`、`*media*crawler*.db`、`*crawler*.sqlite*`、`*xhs*.db`、`*douyin*.db` | 未打开任何疑似 Cookie 或真实采集数据库 |
| archive 路径 | 主干代码默认 `TA_SOCIAL_ARCHIVE_DB` 为空，文档要求绝对路径；上述搜索根下未找到物理 `social_archive.db` | 未读取生产环境变量；运行进程环境不可由本机 `/proc` 回读 |
| Cookie / 安全登录 | `/Users/davidliu/.mediacrawler/cookies` 目录不存在；未读取、创建或注入 Cookie | 不代表授权已获得，也不代表可开始登录 |
| 安全登录契约 | 文档要求独立 Python `>=3.11`、SQLite `save_option`、控制面仅 `127.0.0.1`、`ENABLE_CDP_MODE=True`；默认 QR 登录不能用于无人值守，实际导入使用受控 Cookie | 这是待核对清单，不是环境已满足证明 |

## 结论

L4 的**只读环境清单已完成核验，但结论为未满足 / blocked**：当前批准搜索根下没有可回读的 MediaCrawler 检出、工作库或 archive 路径，Cookie 安全目录也不存在。

这不等于真实 Gate 0 完成，更不等于获得 AUTH-01～AUTH-03 的安装、Cookie、真实采集或归档写入授权。下一步只能由具备相应授权的人补齐外部环境；本卡不触发任何物理操作。

测试：0（本项为只读环境核验，未运行测试套件）。
