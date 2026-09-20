# P2-G3：财报三张表带公告日备用源（窄 writer）

## 基线

主干/服务 `f8f624241d125125a61c69454638982f2423740f`。规格 10.3。DAV-425 只读盘点已独立核验。

从该 SHA 开隔离 worktree / 独立分支。禁止自行 FF、禁止重启服务、禁止改 3/1、禁止改模型绑定、禁止刷分析单。

## 问题

历史分析下三张表主源是新浪 `stock_financial_report_sina`（经 `build_effective_announce_map` / `filter_financial_df_by_effective_announce` 按公告生效日截断）。新浪失败时，同花顺 `stock_financial_abstract_new_ths` **无公告日字段**，历史日必须拒绝，不能降级。v2 样本里会出现 `actual_as_of=None` / `未返回可验证数据日期`。

`fund_flow_individual`、北向停更、板块/质押快照拒绝不在本卡范围。

## 允许文件

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tradingagents/dataflows/financial_announce.py`
- `tests/test_financial_as_of.py`
- `tests/test_financial_announce_cutoff.py`

需要新测试文件时只加 `tests/test_financial_*`。禁止改 `api/main.py`、辩论图、前端、用户配置。

## 行为

1. TDD：先写失败用例再改实现。至少覆盖：新浪成功（回归）、新浪失败+备用源带 `ann_date`/`f_ann_date` 且按 A4 截断成功、新浪失败+备用源无公告日仍拒绝、解析失败不得填今天。
2. 备用源必须有可验证公告日。禁止把无日期摘要当历史财报。禁止伪称北向。禁止历史日打即时快照。
3. 不新增第三方 pip 依赖。Tushare 若已在项目内，只用已有客户端路径；接口失败要有明确失败类型，不得空字符串。
4. 按列名取数，禁止位置切片。缺列进 missing。注入前清洗。
5. 候选 SHA 推到独立分支后 **mention 独立代码审核员**。不要 mention 项目调度助手。

验收：`.venv310` 跑上述测试文件；系统 Python 全量 pytest 不算证据。
