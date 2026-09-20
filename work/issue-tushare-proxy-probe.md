# 第三方 Tushare 代理只读能力探针

## 目标

用户已授权核验卖家提供的第三方 Tushare 代理，作为 TradingAgents-AShare 的候选新算法资金流源。不要把它直接视为官方 Tushare，也不要在没有真实响应证据前接入生产。

## 入口

- HTTP base：`https://t.xiaodefa.top/`
- MCP：`https://t.xiaodefa.top/mcp`
- 用户凭据已在用户私密消息中提供；禁止把 token 写入 issue、评论、代码、命令输出、附件或提交。只从宿主机安全环境变量/`.env` 读取；若当前运行环境没有安全注入，报告 `token_not_available`，不要索要或复制 token。

## 固定探针

1. `trade_cal`：验证代理 envelope、权限和日期字段；
2. `moneyflow_dc`：`002167.SZ`、`601398.SH`，请求日期 `2026-08-14`；
3. `moneyflow_ths`：同标的、同日期；
4. 记录 HTTP 状态、脱敏响应 schema、行数、请求日期、实际日期、字段名、单位、失败类别、耗时；禁止输出完整响应。

## 验收

- 使用项目 `.venv310` / Python 3.10.20；只读，不改代码/配置/数据库/个人设置；
- 明确区分：官方 Tushare API、第三方代理转发、MCP 工具层；
- 只有当 DC/THS 的真实返回字段、日期、单位和稳定性得到验证，才建议进入 DAV-179 的来源优先级施工；
- 若 token 不可用，输出精确 `token_not_available`，不伪造 provider 成功；
- 0 代码测试可接受，但必须报告实际命令和请求结果；不得合入、重启、上线。

交付：脱敏结构化探针报告、token 可用性、DC/THS 结果、source/as-of/field/unit 证据和下一步建议。不要 mention 项目调度助手。
