# DAV-220：主干上线与正确账户 3/3 复验

立即执行。不要等待调度助手。不要再跑旧失败报告。

## 合入门

先确认远端主干已是：

```text
target/codex/dav-4-p2a-trunk@6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2
```

若主干仍是 `7ef89f63`，每 30 秒核验一次，最多等 10 分钟；超时则评论 blocked 并停止，不要用旧 SHA 部署。

## 部署

1. 保护全部 untracked/WIP，禁止 `reset --hard` / `clean -fd`
2. 确认 active reports=0
3. 安全 kill 旧 8000 进程，确认端口释放
4. 宿主 fast-forward 到 `6a8a896b`
5. 用现有 DATABASE_URL、`env -u PYTHONPATH`、完整国内 no_proxy 重启 uvicorn :8000
6. 核验新 PID、cwd、打开的 `data/tradingagents.db`、`/healthz.commit_sha=6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`

## 正确账户 3/3 复验

1. 真实登录 `davidliu022305@gmail.com`
2. `/v1/auth/me` 必须是 `user_id=429163f7-50b6-4982-8bdf-96ae99506843`
3. 新发起 `600036.SH`，请求临时：
   ```json
   {"max_debate_rounds": 3, "max_risk_discuss_rounds": 3}
   ```
4. 禁止复用失败 ID：`50d1f2f94bf2489db7897eab4c00928e`、`dfcf75d53e8343e7bcbdeca8b43ff80d`
5. 新 report 创建后立即断言正确 user_id
6. 完成后核验：
   - status=completed
   - investment_debate_state.count=6，Bull/Bear 各 3 轮
   - history 与 current_*_response 不含 DEBATE_STATE/RISK_STATE 标签
   - risk_debate_state.count=9，三方各 3 轮
   - claims/responded/resolved/judge 完整
   - 用户持久配置仍为 debate=3 / risk=1
7. 资金流：记录 selected_source、selected_field、selected_value、selected_window_days、attempted_sources、fallback_errors；不得把 5 日累计标成 1d；仅 THS netamount 时不得写成主力资金

不得改模型/Provider/Key/角色绑定/持久轮次。不得手工写报告。输出新 report ID、耗时、PASS/FAIL。
