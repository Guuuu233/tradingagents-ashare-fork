## 目标
修复“当天分析的主力资金数据停在上一交易日/获取失败”的问题：当今天已经有收盘资金流历史记录时，必须优先/兜底取到当天数据，而不是只返回 8/7 或“暂无有效记录”。

## Hermes 实测证据（2026-08-10）
- 用户今天（8/10）看到的报告使用 8/7 数据。
- 正确交易日归一化已验证：`2026-08-10 -> 2026-08-10`，`2026-08-09 -> 2026-08-07`；因此周末报告用 8/7 是正确的，但 8/10 工作日新分析不能继续停在 8/7。
- 直接请求新浪历史资金流接口成功返回 000657.SZ 的 8/10 数据：收盘价 72.80，`netamount=-286620931.5`，`r0_net=-381071910.46`。
- 当前项目路由调用：`route_to_vendor('get_individual_fund_flow', '000657.SZ', '2026-08-10')` 仍返回失败：东财 `ConnectionError` + `stock_fund_flow_individual: AttributeError`。

## 根因
`tradingagents/dataflows/providers/cn_akshare_provider.py:get_individual_fund_flow` 当前按日期分流：
- 历史日期：东财失败后调用 `_fetch_sina_historical_fund_flow`，可取得历史数据；
- 当天日期：东财失败后只调用 akshare 的 `stock_fund_flow_individual(symbol="即时")`，该备用调用当前报 `AttributeError`，没有再调用已经验证可用的新浪历史接口，因此当天资金流失败。

另：本次排查发现服务曾被无 `DATABASE_URL` 启动，监听 8000 的旧进程实际打开项目根目录 `tradingagents.db`（空/旧库），而正确库是 `data/tradingagents.db`。Hermes 已重启本地服务并核实当前 PID 打开正确库；请在交付说明中检查是否需要增加启动配置/健康检查提示，避免回归，但不要把用户配置写入代码。

## 修复要求
1. 保持防前视：只返回 `opendate <= curr_date` 的新浪历史行，绝不取未来日期。
2. 当东财失败且当前日期的 akshare 即时快照失败时，调用现有 `_fetch_sina_historical_fund_flow(symbol, curr_date, cutoff)` 作为当天兜底；如果新浪已有当天收盘行，返回该行及近 5 日序列。
3. 如果当前日期仍在交易中、历史接口尚无当天行，再按现有即时快照路径尝试；所有源失败必须显式返回数据缺口，不能填 0/空串。
4. 明确标注来源和数据日期（如“新浪历史/收盘数据，截至 2026-08-10”），让模型知道不是实时盘口资金流。
5. 检查 `netamount` 与 `r0_net` 的字段语义，沿用已有新浪历史格式化规则，不把字段混用。

## TDD 验收
- mock 东财失败 + akshare 即时接口异常 + 新浪历史返回 8/10 行 → `get_individual_fund_flow(..., '2026-08-10')` 返回包含 2026-08-10 和具体净额，不是失败文案。
- 新浪返回包含未来 8/11 行 → 结果必须截断，不出现 8/11。
- 当前日期无当天行 → 仍尝试即时快照；两者均失败时显式失败。
- 历史日期既有行为不回归。
- 全量回归 0 新增失败。

## 环境铁律
- Python 命令使用 `env -u PYTHONPATH` 和 `.venv310/bin/python`。
- 一个 commit 一个关注点；完成后先推送分支、汇报测试，等待 Hermes 验收，不自行提交主干。
- API Key 不得进入代码、测试、评论或提交。 
