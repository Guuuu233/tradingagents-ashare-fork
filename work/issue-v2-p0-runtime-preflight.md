# TradingAgents-AShare v2 Phase 0 服务与账户部署前置审计（只读）

## 固定事实

- 目标 trunk：`codex/dav-4-p2a-trunk@45821dd4f21a5f65578dbf54f5d916970ae835c0`
- 项目根：`/Users/davidliu/Documents/TradingAgents-AShare`
- DB：`data/tradingagents.db`
- 真实账户：`davidliu022305@gmail.com` / `429163f7-50b6-4982-8bdf-96ae99506843`
- 当前已核验：服务未监听 8000；reports 无 pending/running；user_llm_configs 仍 `3/1`

## 任务

只读核验重启前置，不启动或重启服务：

1. `git ls-remote target` 核对 trunk 与候选 refs；记录服务当前是否存在及端口状态。
2. 检查项目 `.venv310`、`.env` 权限和真实 DB 路径；不得打印任何 key/token。
3. 直接查库确认真实用户 `max_debate_rounds=3`、`max_risk_discuss_rounds=1`，并检查 reports 是否出现 pending/running。
4. 形成审核通过后的重启清单：旧 PID/端口释放、`env -u PYTHONPATH`、`DATABASE_URL=sqlite:///./data/tradingagents.db`、国内 `no_proxy`、healthz commit_sha、/api/health JSON、role bindings 脱敏、真实业务 smoke。
5. 不要因为 `/healthz` 或 HTTP 200 缺失就假设服务已上线；当前停机必须明确记录。

## 交付

报告精确命令和真实输出摘要、DB/环境/端口证据、阻塞项、重启前置条件。明确未合入、未重启、未上线。禁止改任何个人设置或配置。
