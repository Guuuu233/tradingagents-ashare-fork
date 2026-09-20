# 卡 3：Tushare 财报接入（BLOCKED —— 须待卡 2 采样结论通过）

**本卡当前不具备开工条件，保持 blocked，不得派发。**
前置：卡 2（Tushare 网关可用性采样，DAV-1097）产出通过性结论。

背景：`work/2026-09-19-global-indices-data-source-audit.md`「卡 3」节与 §4.4。
当前 Tushare 三表探测结果三方矛盾，单次探测不得作为开工依据。

## 范围（解锁后执行）

- `income` / `balancesheet` / `cashflow` 接入 `fundamental_data` 链
- 处理重复报告期：**完全相同的行可折叠；同报告期不同值必须 fail-closed 为【数据缺失】**
- 处理公告日（`ann_date`）与 PIT 边界（防前视偏差）
- RED 用例必须含：构造同报告期不同值的输入，断言 fail-closed

## 环境铁律

- `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，报告贴 `-V`。
- 凭据从 `.env` 读，复用 `industry_linkage_provider._query_tushare_api`，token 不打印/不落盘/不提交。
- 只读生产库用 `immutable=1`，不得开可写连接。
- 不得合入主干、不得部署、不得重启服务；不得改用户配置。
