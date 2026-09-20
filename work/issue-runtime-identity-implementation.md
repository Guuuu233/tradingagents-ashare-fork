# DAV-177 运行时 SHA 身份证据最小实现

## 固定基线

- target trunk：`codex/dav-4-p2a-trunk@21f58832b270917e540afa157ffd2daffa5b6e3f`
- 当前服务 PID：`19732`
- 当前问题：服务 `/healthz` 只有 status/executor 字段，启动日志没有 commit/version/build/source identity；cwd + git HEAD 只能旁证。
- 当前真实 provider smoke 仍为 EM/THS blocked，Sina Web legacy fallback；本任务不处理 provider。

## 目标

在不改用户模型/provider/API Key、不改数据库 schema、不接入凭据的前提下，为运行时增加不可变代码身份的最小可审计证据，使启动日志和 `/healthz` 能返回当前构建的 commit SHA/source root/build identity。

## 允许范围

只允许修改：

- `api/main.py` 中 healthz/lifespan 相关实现；
- 一个现有 API 测试文件，覆盖 healthz identity；
- 必要的启动/版本 helper（若确有必要，必须说明文件）。

禁止：

- 修改 provider、资金流语义、数据库 schema、用户配置、model/provider/API Key；
- 修改前端；
- 直接改 target 主干；
- 重启生产服务；
- 把运行时 identity 缺失伪装成已解决。

## 验收

1. 先在精确 target checkout 复现当前 healthz 缺 identity；
2. 实现后 healthz 至少包含：`commit_sha`、`source_root`、稳定 `build_identity` 或等价字段；缺少 Git 元数据时必须显式 `unknown`，不能猜；
3. 启动日志记录同一 identity；
4. 测试覆盖正常值、元数据缺失/unknown 和 API 响应字段；
5. 使用 `.venv310` 跑新增/相关测试、compileall、git diff-check；
6. 推送新远端 branch/SHA，交给独立代码审核；不合入、不重启、不宣布上线。

## 外部 provider 单独处理

EM/THS 的 `provider_blocked` 保持唯一外部 sentinel，不在本任务中重复探针或把 Sina legacy 当成新算法成功。评论不要包含凭据，也不要 mention 项目调度助手。
