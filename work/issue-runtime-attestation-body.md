# DAV-176 生产运行时 SHA attestation 与收口证据

## 固定状态

- target trunk：`codex/dav-4-p2a-trunk@21f58832b270917e540afa157ffd2daffa5b6e3f`
- 服务 PID：`19732`
- cwd：`/Users/davidliu/Documents/TradingAgents-AShare`
- 数据库：`data/tradingagents.db`
- `/healthz`：HTTP 200，但响应没有 commit/version/build 字段。
- 真实 smoke：`601398.SH`、`002167.SZ` / `2026-08-14` 均为 `sina_historical / legacy_web_algorithm`、`data_conflict`、`failure_categories=[provider,transport]`、`direction_allowed=false`、`hard_guard.blocked=true`，无 EM/THS 新算法证据。

## 目标

只做只读运行时收口证据，不修改生产业务代码、API、数据库 schema、provider、配置或用户个人设置：

1. 核验 PID 19732 的 command、cwd、实际打开数据库、环境中的 `DATABASE_URL`、`PYTHONPATH` 和 proxy/no_proxy；
2. 核验进程加载代码与 target SHA 的不可变关联（例如进程 cwd + git HEAD + 文件/启动日志证据）；
3. 核验端口监听者、healthz、active reports=0；
4. 核验 post-deploy smoke 结构化结果与 DAV-124/DAV-125 门控状态；
5. 给出最终判定：`runtime_attested` 或 `runtime_attestation_blocked`；外部 EM/THS 阻塞必须单独列为 `provider_blocked`，不能混成代码失败；
6. 不重跑完整回归，不重复 DAV-172，不生成新报告，不重启服务，不合入。

## 交付

评论必须包含精确 trunk SHA、PID/cwd/DB、healthz、active reports、runtime attestation 证据、两个标的脱敏 smoke 字段、未完成事项。不得输出凭据；评论不要 mention 项目调度助手。
