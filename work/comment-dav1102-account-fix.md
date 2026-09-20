## 第三轮验收：账户纠正 + no_proxy 已覆盖正确端点

[@代码运维测试员](mention://agent/f179edb8-9a81-4dbd-8787-afbfd307eda4) 总工纠正：上一轮 401 的根因是**用错了账户**。

### 纠正内容

1. **必须用 `davidliu022305@gmail.com` 账户发起分析，不要用本地账户**（`local-default-user`/`local@tradingagents.local`）。
   - 该账户在 `user_llm_configs` 的 `backend_url` = `http://100.67.61.23:8317/v1`（已实测：无代理直连在线，对无效 key 回 401 JSON，即鉴权正常工作）；
   - 注意库里还有个形似的 `davidliu022035@gmail.com`（**022035 ≠ 022305**），别选错。
2. **服务已二次重启**（新 PID 53717），`no_proxy` 现同时覆盖 `100.65.130.33`、`100.67.61.23` 及 `100.64.0.0/10` 全段（进程环境已实测无引号残留、httpx CIDR 匹配生效）。SHA 不变：`42977154`。

### 重跑要求

- 标的 `600036.SH`、基准日 `2026-09-18`（历史日期）不变；以 davidliu022305@gmail.com 身份调用；
- 四条并列逐条独立出证；陈旧/越界拒绝分支未自然命中记「未覆盖」；
- 新增观察项：**Tushare `index_global` 首选链路是否命中**（上一轮 2 次 attempt 超时回退 akshare；服务现已走 `100.64.0.0/10` 直连 Tailscale，记录本次 attempt 成败与耗时）；
- 记账：reports 现基线 **1745**（1742 + 上轮 1 completed + 阻塞期 2 failed），本轮 completed 应恰好再 +1 → 1746；
- 完成后更新 `work/2026-09-20-global-indices-post-deploy-verification.md` 并 mention `项目调度助手`。
